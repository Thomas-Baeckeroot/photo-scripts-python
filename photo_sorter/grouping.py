# -*- coding: utf-8 -*-
"""
Image group detection: panoramas, HDR, focus bracketing, bursts.

Functions:
    identify_image_groups()          — Basic time-based grouping
    review_and_cleanup_groups()      — Drop 2-image groups, reassign IDs
    identify_advanced_image_groups() — Advanced grouping (calls basic + refinement)
    confirm_groups()                 — Interactive user confirmation of groups
    _edit_group()                    — Edit the range of images in a group
    create_sub_folder_name()         — Generate subfolder name from a series
"""

import logging
from datetime import datetime
from typing import List

from photo_sorter.constants import MIN_TIME_BETWEEN_PANOS
from photo_sorter.display import log_files, log_title
from photo_sorter.models import GroupInfo, ImageFile

log = logging.getLogger(__name__)


def review_and_cleanup_groups(files, groups):
    """
    Walk through each group to:
    - Drop groups with only 2 images (too small to be a meaningful series)
    - Assign group_id to the first image of each remaining group
    """
    for group in groups:
        log.debug(f"Walking through group '{group.group_id}' with {group.n_images} images...")
        if group.n_images == 2:
            log.warning(f"\tDropping group '{group.group_id}' "
                        f"with 2 images: '{group.first_image}' and '{group.last_image}'")
            # Set this group.n_images to 0 in groups:
            group.n_images = 0
            for f in files:
                # Remove group_id for each image of this group:
                if f.group_id == f"group_{group.group_id}":
                    f.group_id = None
                    f.group_type = None
                    log.debug(f"\tDropped group_id '{f.group_id}' for '{f.original_filename}'")
                    break  # Only one file to remove from group => exit loop on files
        else:
            # Get ImageFile object from images with basename = group.first_image
            for f in files:
                if f.basename == group.first_image:
                    f.group_id = f"group_{group.group_id}"
                    f.group_type = "group"
                    log.debug(f"\tReassigned group_id '{f.group_id}' to {group.first_image}")
                    break
            log.info(f"\tGroup {group.group_id} with {group.n_images} images: "
                     f"{group.first_image} and {group.last_image}")

    log.warning(groups)
    # Count groups
    n_groups = len(set(f.group_id for f in files if f.group_type == "group"))
    log.info(f"Identified {n_groups} panorama groups")
    return files


def identify_image_groups(files):
    """
    Identifies groups of images that form series like panoramas, HDR, or focus bracketing.
    Groups are identified based on timestamp proximity and shooting parameters.

    Args:
        files (list[ImageFile]): List of ImageFile objects to analyze

    Returns:
        list[ImageFile]: Updated list with group information
    """
    log.info("Identifying image groups")

    if not files:
        log.warning("No files with timestamps available for grouping")
        return files

    log.info(f"Analyzing {len(files)} files with timestamps for series identification")

    groups = []
    previous_image_part_of_group = False
    current_group_id = 0
    last_timestamp = datetime(1970, 1, 1)
    previous_image_basename = None

    for file in files:
        log.debug(f"Verifying if {file.original_filename} is part of a group...")
        # Skip if no timestamp
        if not file.timestamp:
            log.debug(" └→ no timestamp => ignore file (skip)")
            continue

        if last_timestamp is None:
            log.debug(" └→ Previous image file had no timestamp defines => this image can be the first of a series...")
            last_timestamp = file.timestamp
            continue

        # Calculate time difference from the previous shot
        # (time between two shots, not including exposure time that can be seconds for nightly panoramas)
        time_diff = (file.timestamp - last_timestamp).total_seconds() - file.exposure_time

        # Determine if this is part of a set
        if MIN_TIME_BETWEEN_PANOS >= time_diff >= 0:
            # note: if time_diff is negative, it is mostly because picture number looped
            # (from IMG_9999 back to IMG_0000)
            log.debug(" │\tShot within panorama time range")
            if not previous_image_part_of_group:
                # Start a new group
                current_group_id += 1
                if previous_image_basename is None:
                    previous_image_basename = "ERROR"
                    log.error(
                        f" │\t\tNo previous image basename for group {current_group_id}! (this should not happen)")
                group_obj = GroupInfo(
                    group_id=current_group_id,
                    first_image=previous_image_basename,
                    last_image=file.basename,
                    n_images=2
                )
                groups.append(group_obj)
                previous_image_part_of_group = True
                log.debug(f" │\t\tStarting new group '{current_group_id}' with "
                          f"first shot '{previous_image_basename}' and last shot '{file.basename}' (for now)")
            else:
                # Update last_image and increment n_images in the current group (last one added to groups):
                groups[-1].last_image = file.basename
                groups[-1].n_images += 1

            file.group_id = f"group_{current_group_id}"
            file.group_type = "group"
            log.debug(f" └→ Added {file.basename} to group {current_group_id}")

        else:
            log.debug(" │\tThis shot doesn't belong to a series with previous image")
            log.debug(" └→ (starting new group)")
            previous_image_part_of_group = False

        # Update last timestamp
        last_timestamp = file.timestamp
        previous_image_basename = file.basename

    log.info(f"Found {len(groups)} groups: {groups}")

    files = review_and_cleanup_groups(files, groups)

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
        serie_of_photos = list()
        for file in files:
            if group_id is None and file.group_id and file.group_id.startswith("group_"):
                print("Starting review of new group_id:")  # Groups not reviewed yet start with "group_"
                group_id = file.group_id
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

            sub_folder_name = create_sub_folder_name(serie_of_photos, False)
            # Ask if this group is confirmed as a Panorama, or Canceled, or something else:
            print(f"What should be done with this group? '{sub_folder_name}' ")
            print("- [P]anorama: keep group, generate png, create Hugin script (default)")
            print("- [H]DR: keep group, fuse the bracketed exposures with enfuse")
            print("- [E]dit panorama: change images in this group")
            print("- [C]ancel: images were incorrectly detected as a group")
            print("- [O]ther: group is correct but not a panorama:"
                  " keep group, generate png but do not create Hugin script")
            choice = input("\nEnter your choice [P/H/E/C/O]:")
            if choice.lower() == "e":
                _edit_group(files, group_id, serie_of_photos)
                continue
            elif choice.lower() == "p" or choice == "":
                log.info("Group confirmed as Panorama")
                choice = "panorama"
            elif choice.lower() == "h":
                log.info("Group confirmed as HDR")
                # Recompute the subfolder name with the _HDR suffix
                sub_folder_name = create_sub_folder_name(serie_of_photos, True)
                choice = "hdr"
            elif choice.lower() == "c":
                log.info("Group confirmed as Canceled")
                sub_folder_name = None
                choice = "canceled"
            else:
                log.info("Group confirmed as something else")
                sub_folder_name = sub_folder_name + "_other"
                choice = "other"

            for file in files:
                if file.group_id == group_id:
                    file.group_id = sub_folder_name
                    file.group_type = choice
                    log.debug(f"\tReclassified {file.basename}"
                              f" from {group_id}(group) to {file.group_id} ({file.group_type}).")

        else:
            log.info("No more groups to validate.")

        # end of while group_id loop

    return files
