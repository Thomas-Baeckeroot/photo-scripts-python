# -*- coding: utf-8 -*-
"""
Image group detection: panoramas, HDR, focus bracketing, bursts.

Design & rationale
------------------
Grouping is a two-level, timestamp-based segmentation. The design emerged from
real Canon EOS R7 footage where the naive single-threshold approach silently
lost most HDR brackets — keep the reasoning here, because new edge cases will
surely appear.

WHY two levels (a single threshold cannot serve both):
  - Panoramas are shot by hand, reframing between frames: cadence ~1-15 s.
  - HDR / focus brackets are shot in continuous drive: cadence ~0.2-0.5 s,
    and consecutive brackets can be only a few seconds apart.
  A single 15 s window merges adjacent brackets (and can't tell them from a
  panorama); a single 2 s window shreds panoramas. So we cluster loosely first
  (MIN_TIME_BETWEEN_PANOS → "series", panorama-friendly), then split each series
  tightly (MAX_TIME_WITHIN_BURST → individual bursts). A series with no tight
  cluster is a panorama; a series with tight clusters is one group per burst.

WHY sub-second timestamps are mandatory (see metadata.extract_image_metadata):
  DateTimeOriginal has 1 s resolution. Burst frames share the same clock second,
  so integer-second gaps are 0 and any "gap - exposure_time" test goes slightly
  negative — the old code read that as "not a series" and dropped the frame. A
  whole bracket inside one second produced *no* group at all. SubSecTimeOriginal
  restores the true ~0.33 s gaps and fixes this at the root.

WHY exposure-ratio classification (HDR vs generic burst):
  A tight burst is HDR when the exposure is deliberately laddered; it is a focus
  stack or action burst when exposure is ~constant. We suggest "hdr" when
  max/min exposure_time >= HDR_EXPOSURE_RATIO, else "burst". This is only a
  *suggestion*: confirm_groups() shows it as the default and the user overrides.
  Framing-based detection (panorama vs static burst) is intentionally NOT
  attempted — it needs image content, and the interactive confirmation is cheap.

Tunable knobs (photo_sorter/constants.py):
  MIN_TIME_BETWEEN_PANOS, MAX_TIME_WITHIN_BURST, HDR_EXPOSURE_RATIO.

Known edge cases / limitations (candidates for future work):
  - Slow AEB (> MAX_TIME_WITHIN_BURST between frames) is seen as a panorama.
  - Fast hand-held panorama (< MAX_TIME_WITHIN_BURST cadence) is seen as a burst.
  - Borderline exposure ladders (ratio just under HDR_EXPOSURE_RATIO, e.g. a
    ±0.3 EV bracket) are suggested "burst"; the user can still pick [H].
  - A 2-shot bracket is kept only if classified "hdr"; 2-shot pan/focus groups
    are dropped as too small (see _keep_group) to avoid spurious pairs.
  - Detection relies purely on capture cadence + exposure; it does not inspect
    ExposureCompensation/DriveMode/BracketMode EXIF tags (a possible refinement).

Functions:
    identify_image_groups()          — Two-level timestamp segmentation + suggestion
    identify_advanced_image_groups() — Entry point (wraps identify_image_groups)
    confirm_groups()                 — Interactive user confirmation of groups
    create_sub_folder_name()         — Generate subfolder name from a series
    _segment_by_gap()                — Split a sorted list on time gaps
    _classify_burst()                — Suggest "hdr"/"burst" from exposure variation
    _keep_group() / _assign_group()  — Size filter / tag frames
    _edit_group()                    — Edit the range of images in a group
"""

import logging

from photo_sorter.constants import (HDR_EXPOSURE_RATIO, MAX_TIME_WITHIN_BURST,
                                     MIN_TIME_BETWEEN_PANOS)
from photo_sorter.display import log_files, log_title

log = logging.getLogger(__name__)


def _segment_by_gap(dated_files, max_gap):
    """
    Split a timestamp-sorted list into runs where every consecutive gap is
    <= *max_gap* seconds.

    Args:
        dated_files (list[ImageFile]): Files sorted by timestamp (all with a timestamp).
        max_gap (float): Maximum allowed gap between consecutive frames of a run.

    Returns:
        list[list[ImageFile]]: One list per run (never empty for a non-empty input).
    """
    runs = []
    if not dated_files:
        return runs
    current = [dated_files[0]]
    for previous, current_file in zip(dated_files, dated_files[1:]):
        gap = (current_file.timestamp - previous.timestamp).total_seconds()
        if gap <= max_gap:
            current.append(current_file)
        else:
            runs.append(current)
            current = [current_file]
    runs.append(current)
    return runs


def _classify_burst(run):
    """
    Suggest a group type for a tight burst from its exposure variation.

    HDR brackets deliberately vary the exposure across frames; focus brackets
    and action bursts keep it ~constant.

    Returns:
        str: "hdr" when the longest/shortest exposure ratio reaches
             HDR_EXPOSURE_RATIO, else "burst".
    """
    exposures = [f.exposure_time for f in run
                 if f.exposure_time is not None and f.exposure_time > 0]
    if (len(exposures) >= 2
            and max(exposures) / min(exposures) >= HDR_EXPOSURE_RATIO):
        return "hdr"
    return "burst"


def _keep_group(run, group_type):
    """Whether a detected group has enough frames to be kept."""
    if group_type == "hdr":
        return len(run) >= 2   # a 2-shot exposure pair is a valid HDR bracket
    return len(run) >= 3       # panorama / focus / action: >= 3 to be meaningful


def _assign_group(run, group_id, group_type):
    """Tag every frame of *run* with its group id and suggested type."""
    for f in run:
        f.group_id = f"group_{group_id}"
        f.group_type = group_type
        log.debug(f"\t{f.basename} → group_{group_id} ({group_type})")


def identify_image_groups(files):
    """
    Identify series of related images with a two-level, timestamp-based
    segmentation:

      1. **Loose level** (MIN_TIME_BETWEEN_PANOS, 15 s): cluster shots into
         "series". This preserves panoramas, whose frames are seconds apart.
      2. **Tight level** (MAX_TIME_WITHIN_BURST, 2 s): within each series,
         detect continuous-drive bursts (HDR / focus brackets), whose frames
         are a fraction of a second apart.

    A series with no tight burst (all frames > 2 s apart) is a panorama. A
    series that contains tight bursts is split into one group per burst — this
    separates consecutive brackets that a single 15 s window would merge. Each
    burst is classified as HDR (exposure varies) or a generic burst (exposure
    ~constant); this is a *suggestion* the user confirms in confirm_groups().

    Requires sub-second timestamps (see metadata.extract_image_metadata): with
    only 1-second resolution, frames of a same-second burst are
    indistinguishable and brackets are lost.

    Args:
        files (list[ImageFile]): List of ImageFile objects to analyze

    Returns:
        list[ImageFile]: Updated list with group_id / group_type suggestions
    """
    log.info("Identifying image groups")

    dated = sorted((f for f in files if f.timestamp), key=lambda f: f.timestamp)
    if not dated:
        log.warning("No files with timestamps available for grouping")
        return files

    log.info(f"Analyzing {len(dated)} timestamped files for series identification")

    group_id = 0
    counts = {"panorama": 0, "hdr": 0, "burst": 0}

    for series in _segment_by_gap(dated, MIN_TIME_BETWEEN_PANOS):
        bursts = _segment_by_gap(series, MAX_TIME_WITHIN_BURST)

        if len(bursts) == len(series):
            # No tight clustering: frames all > 2 s apart → panorama series.
            if _keep_group(series, "panorama"):
                group_id += 1
                _assign_group(series, group_id, "panorama")
                counts["panorama"] += 1
                log.info(f" └→ group_{group_id}: panorama ({len(series)} shots, "
                         f"{series[0].basename}..{series[-1].basename})")
        else:
            # Burst-type series: keep each tight burst as its own group.
            for burst in bursts:
                suggested = _classify_burst(burst)
                if _keep_group(burst, suggested):
                    group_id += 1
                    _assign_group(burst, group_id, suggested)
                    counts[suggested] += 1
                    log.info(f" └→ group_{group_id}: {suggested} ({len(burst)} shots, "
                             f"{burst[0].basename}..{burst[-1].basename})")

    log.info(f"Identified {group_id} groups "
             f"({counts['panorama']} panorama, {counts['hdr']} HDR, "
             f"{counts['burst']} other burst)")
    return files


def identify_advanced_image_groups(files):
    """
    Advanced identification of image groups with additional metadata criteria.

    Args:
        files (list[ImageFile]): List of ImageFile objects to analyze

    Returns:
        list[ImageFile]: Updated list with group information
    """
    log_title("Identifying advanced image groups")
    # Basic grouping by time first
    files = identify_image_groups(files)
    log_files(files, "<folder>/")

    # Additional refinement could be done here
    # For example: checking exposure_time patterns for HDR

    return files


def create_sub_folder_name(serie_of_photos, serie_is_hdr):
    """
    Generate a subfolder name from the first and last photo names in a series.

    Example: ["IMG_0010", "IMG_0011", "IMG_0012", "IMG_0013"]
             → "IMG_0010-3_4" (common prefix stripped from last name)

    Args:
        serie_of_photos (list[str]): List of photo basenames in the series
        serie_is_hdr (bool): Whether this series is an HDR set

    Returns:
        str: Subfolder name for this series
    """
    first_picture = serie_of_photos[0]
    last_picture = serie_of_photos[len(serie_of_photos) - 1]
    subfolder_name = first_picture + "-"  # Starts with the name of first picture
    i = 0
    while True:
        if first_picture[i] == last_picture[i]:
            i = i + 1
            if i >= len(first_picture) or i >= len(last_picture):
                break
        else:
            break
    subfolder_name = subfolder_name + last_picture[i:] + "_" + str(len(serie_of_photos))
    if serie_is_hdr:
        subfolder_name = subfolder_name + "_HDR"
    return subfolder_name


def _edit_group(files, group_id, serie_of_photos):
    """
    Prompt user for new first/last basenames, validate, handle conflicts,
    and update group assignments in-place.

    Returns True if edit was applied, False if user cancelled or input was invalid.
    """
    first_default = serie_of_photos[0]
    last_default = serie_of_photos[-1]

    new_first = input(f"Which is the 'basename' of the first image (default is {first_default})? ")
    if not new_first:
        new_first = first_default

    new_last = input(f"Which is the 'basename' of the last image (default is {last_default})? ")
    if not new_last:
        new_last = last_default

    # Validate basenames exist
    all_basenames = [f.basename for f in files]
    if new_first not in all_basenames:
        print(f"Error: '{new_first}' not found in file list.")
        return False
    if new_last not in all_basenames:
        print(f"Error: '{new_last}' not found in file list.")
        return False

    first_idx = all_basenames.index(new_first)
    last_idx = all_basenames.index(new_last)
    if first_idx > last_idx:
        print(f"Error: first image '{new_first}' comes after last image '{new_last}'.")
        return False
    if first_idx == last_idx:
        print(f"Error: group must contain at least 2 images.")
        return False

    # Check for conflicts with other groups
    new_range = files[first_idx:last_idx + 1]
    conflicts = {}
    for f in new_range:
        if f.group_id and f.group_id != group_id:
            conflicts.setdefault(f.group_id, []).append(f.basename)

    if conflicts:
        print("Warning: the following images belong to other groups:")
        for gid, basenames in conflicts.items():
            print(f"  {gid}: {', '.join(basenames)}")
        confirm = input("Transfer these images to the current group? [Y/N]: ")
        if confirm.lower() != 'y':
            return False

    # Clear old group assignments
    for f in files:
        if f.group_id == group_id:
            f.group_id = None
            f.group_type = None

    # Clear conflicting group assignments
    for gid in conflicts:
        for f in files:
            if f.group_id == gid:
                f.group_id = None
                f.group_type = None

    # Assign new group
    for f in new_range:
        f.group_id = group_id
        f.group_type = "group"

    log.info(f"Group '{group_id}' edited: {new_first} to {new_last} ({len(new_range)} images)")
    log_files(files, "<folder>/")
    return True


def confirm_groups(files):
    """
    Walks through each group to confirm with the user if this group is:
    - [P]anorama: keep group, generate png, create Hugin script (default)
    - [E]dit: change the range of images in this group
    - [C]ancel: images were incorrectly detected as a group
    - [O]ther: group is correct but not a panorama

    Args:
        files (list[ImageFile]): List with group information

    Returns:
        list[ImageFile]: Updated list with confirmed group types
    """
    group_id = "group_000_start_while_loop"
    while group_id:
        group_id = None
        first_picture = None
        last_picture = None
        suggested_type = None   # auto-detected type: "panorama" / "hdr" / "burst"
        serie_of_photos = list()
        for file in files:
            if group_id is None and file.group_id and file.group_id.startswith("group_"):
                print("Starting review of new group_id:")  # Groups not reviewed yet start with "group_"
                group_id = file.group_id
                suggested_type = file.group_type
                first_picture = file.basename
                print("╔═══════════════════════════════╦───────────┬───────┐")
                print(f"║ {group_id:<30}║ timestamp │exp.(s)│")
                print("╠═════════════════════╤═════════╩═══════════╪═══════╣")
            if group_id and file.group_id == group_id:
                last_picture = file.basename
                serie_of_photos.append(file.basename)
                print(
                    f"║ {file.basename:<20}│ {file.timestamp} │ "
                    f"{f'{file.exposure_time:.3f}' if file.exposure_time is not None else '-.---'} ║")

        if group_id:
            print("╚═════════════════════╧═════════════════════╧═══════╝")

            # The default action follows the auto-detected type (see
            # identify_image_groups): HDR bursts default to [H], panoramas to
            # [P], other bursts (focus/action) to [O].
            default_choice = {"hdr": "h", "panorama": "p",
                              "burst": "o"}.get(suggested_type, "p")
            example_name = create_sub_folder_name(
                serie_of_photos, suggested_type == "hdr")
            print(f"Auto-detected as: {suggested_type.upper()}. "
                  f"What should be done with this group? (e.g. '{example_name}')")
            print(f"- [P]anorama: keep group, generate png, create Hugin script"
                  f"{'  (default)' if default_choice == 'p' else ''}")
            print(f"- [H]DR: keep group, fuse the bracketed exposures with enfuse"
                  f"{'  (default)' if default_choice == 'h' else ''}")
            print("- [E]dit: change the images in this group")
            print("- [C]ancel: images were incorrectly detected as a group")
            print(f"- [O]ther: keep group, generate png but do not create Hugin script"
                  f"{'  (default)' if default_choice == 'o' else ''}")
            choice = input(f"\nEnter your choice [P/H/E/C/O] "
                           f"(default {default_choice.upper()}): ")
            choice = (choice or default_choice).lower()

            if choice == "e":
                _edit_group(files, group_id, serie_of_photos)
                continue
            elif choice == "p":
                log.info("Group confirmed as Panorama")
                sub_folder_name = create_sub_folder_name(serie_of_photos, False)
                choice = "panorama"
            elif choice == "h":
                log.info("Group confirmed as HDR")
                # Recompute the subfolder name with the _HDR suffix
                sub_folder_name = create_sub_folder_name(serie_of_photos, True)
                choice = "hdr"
            elif choice == "c":
                log.info("Group confirmed as Canceled")
                sub_folder_name = None
                choice = "canceled"
            else:
                log.info("Group confirmed as something else")
                sub_folder_name = create_sub_folder_name(serie_of_photos, False) + "_other"
                choice = "other"

            for file in files:
                if file.group_id == group_id:
                    file.group_id = sub_folder_name
                    file.group_type = choice
                    log.debug(f"\tReclassified {file.basename}"
                              f" from {group_id}({suggested_type}) to "
                              f"{file.group_id} ({file.group_type}).")

        else:
            log.info("No more groups to validate.")

        # end of while group_id loop

    return files
