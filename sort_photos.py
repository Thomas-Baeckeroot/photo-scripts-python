#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This script is usually launched right after having downloaded pictures from digital cameras onto your computer.
It aims to organize picture files: Raws in a separated folder, panorama grouped in a folder, processed files
(jpg/avif) generated in the root folder from the raw files if none are found. If pictures are not geo-tagged,
this script bases itself on a .gpx file to add location into the picture (if .gpx file available).

More in README.md
"""

import configparser
import glob
import json
import logging
import os
import shutil
import subprocess

from dataclasses import dataclass  # Not used yet: field
from datetime import datetime
from PIL import Image  # If PIL module not installed, then: `pip install Pillow`
# soon pillow-avif-plugin will also be required
from PIL.ExifTags import TAGS
from sys import argv
from typing import Optional, List, Tuple
import xml.etree.ElementTree as ET

# CONSTANTS:
ERROR = '\033[1;31mError:\033[0m '
# Return codes:
#   0 Normal exit
#  -1 Invalid number of arguments
#  -2 Folder given as picture folder is not detected as a valid folder
RC_ATTRIBUTES_ERROR = -1
RC_PATH_ERROR = -2

RAW_EXTENSIONS = {'.crw', '.cr2', '.cr3', '.nef'}
RENDERED_EXTENSIONS = {'.jpg', '.jpeg', '.heif', '.avif'}

# Folder to which backups of photos will be moved before applying geo-tagging
# (relative to photoFolder, without final '/'):
FOLDER_BACKUP_GPS = "BackupBeforeGPS!AE!"

# MAXIMUM time allowed between each picture of a panorama (in seconds):
MIN_TIME_BETWEEN_PANOS = 15  # Can be corrected between 19 and 7.5...
# 13.5 was considered good for a good time
# For room panorama, up to 40 s. were needed between 2 shots.

logging.basicConfig(
    # filename=utils.get_home() + "/tmp/logfile.log",
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(filename)s:%(lineno)d\t%(message)s')
log = logging.getLogger("sort_photos.py")  # %(name)s


def load_configuration():
    config = configparser.ConfigParser()
    # define default values:
    config.read_dict({
        'Folders': {
            'root': '~/Images',
            'raw': 'RAW'
        }
    })

    config_path = os.path.expanduser('~/.config/sort_photo.conf')
    config.read(config_path)

    root_folder_load_cfg = os.path.expanduser(config['Folders']['root'])  # FIXME Not used yet!
    # root-folder will be used only if sort_photos.py is called without folder as argument.
    # Folder where raw files will be moved to (without final '/'):
    folder_for_raws_load_cfg = config['Folders']['raw']
    return root_folder_load_cfg, folder_for_raws_load_cfg


root_folder, FOLDER_FOR_RAWS = load_configuration()


# if more global values must be used, then we would use config=load_configuration() and get them separately.

@dataclass(order=True)
class ImageFile:
    basename: str
    original_filename: str
    raw_relative_path: str
    raw_filename: Optional[str] = None
    processed_relative_path: Optional[str] = None
    processed_filename: Optional[str] = None
    timestamp: Optional[datetime] = None
    exposure_time: Optional[float] = None
    has_gps: bool = False
    group_id: Optional[str] = None  # Identifiant du groupe (panorama, HDR, etc.)
    group_type: Optional[str] = None  # Type de groupe: "panorama", "hdr", "focus"


@dataclass(order=True)
class GroupInfo:
    group_id: int
    first_image: str
    last_image: str
    n_images: int
    # todo To be used later to determine the type of group:
    # group_type: str = "group"  # "group" meaning undetermined. Changed after to "panorama", "hdr", "focus", ...
    # total_shooting_time: float = 0.0
    # min_ev: float = None  # with EV = ISO * exposition_time / (f-stop)^2
    # max_ev: float = None


def log_files(files, folder):
    if files:
        log.info(f"Found {len(files)} files in '{folder}':")
    else:
        log.warning(f"Did not find any files in '{folder}':")
    log.debug(
        "┌────────────────────────────────────────────┬───────────────────────────────┬───────────────────────────────┬─────────────────────┬───────┬─────┬──────────────┐")
    log.debug(
        "| basename             (original_filename)   | raw                           | processed                     |      timestamp      |exp.(s)| gps | group (type) |")
    previous_group_id = "STARTING"
    if files:
        for file in files:
            if file.group_id != previous_group_id:
                log.debug(
                    "├────────────────────────────────────────────┼───────────────────────────────┼───────────────────────────────┼─────────────────────┼───────┼─────┼──────────────┤")
                previous_group_id = file.group_id
            raw_file_with_path = f"'{file.raw_relative_path}'/'{file.raw_filename}'" if file.raw_filename else "  -"
            processed_file_with_path = f"'{file.processed_relative_path}'/'{file.processed_filename}'" if file.processed_filename else "  -"
            log.debug(
                f"| {file.basename:<20} ({file.original_filename:<20})"
                f"| {raw_file_with_path:<30}"
                f"| {processed_file_with_path:<30}"
                f"| {f'{file.timestamp}' if file.timestamp is not None else '---- -- -- --:--:--'} "
                f"| {f'{file.exposure_time:.3f}' if file.exposure_time is not None else '-.---'} "
                f"| {'yes' if file.has_gps else 'no '} "
                f"| {file.group_id or '-'} ({file.group_type or '-'}) "
                # f"|"  # todo Adjust last column width
            )
    log.debug(
        "└────────────────────────────────────────────┴───────────────────────────────┴───────────────────────────────┴─────────────────────┴───────┴─────┴──────────────┘")


def log_title(title):
    log.info(" " * 20 + "┌" + "─" * (2 + len(title)) + "┐")
    log.info("╒" + "═" * 19 + "╡ " + title + " ╞" + "═" * 19 + "╕")
    log.info("│" + " " * 19 + "└" + "─" * (2 + len(title)) + "┘" + " " * 19 + "│")


def scan_directory(root_directory):
    """
    Recursively scans a directory and its subdirectories to create ImageFile instances for all files.

    Args:
        root_directory (str): Path to the root directory to scan

    Returns:
        list[ImageFile]: List of all files found
    """
    log_title(f"Scanning directory '{root_directory}'")
    files = []
    root_directory = os.path.abspath(root_directory)

    # Walk through all files and subdirectories
    for dirpath, _, filenames in os.walk(root_directory):
        for filename in filenames:
            # Extract basename (name without extension) and extension
            basename, ext = os.path.splitext(filename)

            # Calculate the relative path from the root directory
            rel_path = os.path.relpath(dirpath, root_directory)
            # (no more need to add '/')
            # if rel_path == ".":  # If it's the root directory
            #     rel_path = ""
            # rel_path = rel_path if rel_path else ""

            # Determine if it's a RAW or processed image
            if ext.lower() in RAW_EXTENSIONS:
                raw_filename = filename
                processed_relative_path = None
                processed_filename = None
            elif ext.lower() in RENDERED_EXTENSIONS:
                raw_filename = None
                processed_relative_path = rel_path
                processed_filename = filename
            else:
                raw_filename = None
                processed_relative_path = None
                processed_filename = None
                log.info(f"File '{filename}' is neither a RAW nor a processed image. Skipping.")

            # Create and add the ImageFile instance
            file_obj = ImageFile(
                basename=basename,
                original_filename=filename,
                raw_relative_path=rel_path,
                raw_filename=raw_filename,
                processed_relative_path=processed_relative_path,
                processed_filename=processed_filename
            )
            log.info(f"Adding file '{file_obj.original_filename}' to list of files.")
            files.append(file_obj)

    # Sort files by basename (since order=True is defined in the dataclass)
    files.sort()

    return files


def get_exif_with_exiftool(filepath):
    try:
        result = subprocess.run(
            ["exiftool", "-j", filepath],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        exif_data = json.loads(result.stdout)[0]
        return exif_data
    except Exception as e:
        log.error("")
        log.error(f"Could not extract EXIF using exiftool for {filepath}: {e}")
        log.error("If failing as 'No such file or directory: 'exiftool'' ")
        log.error("then install exiftool: see https://exiftool.org/install.html (or install with brew, apt, etc...)")
        log.error("")
        return {}


def get_exif(filepath):
    # if exif data should be extracted without exiftool, then we should re-use method get_exif_as_dict(file_name) below.
    return get_exif_with_exiftool(filepath)


def parse_exposure_time(value):
    """Returns the exposure time as a float."""
    try:
        if isinstance(value, tuple) and len(value) == 2:
            # EXIF stores exposure as a fraction (tuple of numerator, denominator)
            return value[0] / value[1]
        elif isinstance(value, str) and '/' in value:
            # EXIF stores exposure as a fraction string, ex.: "1/80"
            num, den = value.split('/')
            return float(num) / float(den)
        else:
            # EXIF stores exposure as a float (or its representation as a string)
            return float(value)
    except Exception as e:
        log.error(f"Could not parse exposure time '{value}': {e}")
        return None


def extract_image_metadata(photo_folder, files):
    """
    Extracts metadata (timestamp, exposure time, GPS info) from image files
    and updates ImageFile objects using named EXIF tags.

    Returns:
        list[ImageFile]: Updated list of ImageFile objects with extracted metadata

    :param photo_folder: Root location of pictures in 'files'
    :param files: List of ImageFile objects to process (list[ImageFile])
    """
    log_title("Extracting image metadata")
    # Define EXIF tag constants
    EXIF_DATETIME_ORIGINAL = 'DateTimeOriginal'
    EXIF_DATETIME = 'DateTime'
    EXIF_EXPOSURE_TIME = 'ExposureTime'
    EXIF_GPS_INFO = 'GPSInfo'

    for file in files:
        # Only process files that are images (RAW or processed)
        if file.raw_filename or file.processed_filename:
            full_path = os.path.join(
                photo_folder,
                file.raw_relative_path.lstrip('/'),
                file.original_filename
            )
            log.debug(f" ⬐Processing image file '{full_path}'...")

            exif = get_exif(full_path)

            # Extract timestamp
            if EXIF_DATETIME_ORIGINAL in exif:
                date_str = exif[EXIF_DATETIME_ORIGINAL]
                try:
                    file.timestamp = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    log.warning(f"Invalid date format in {file.original_filename}: {date_str}")
            elif EXIF_DATETIME in exif:
                date_str = exif[EXIF_DATETIME]
                try:
                    file.timestamp = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                except ValueError:
                    log.warning(f"Invalid date format in {file.original_filename}: {date_str}")

            # Extract exposure time
            if EXIF_EXPOSURE_TIME in exif:
                file.exposure_time = parse_exposure_time(exif[EXIF_EXPOSURE_TIME])

            # Check if GPS data exists
            file.has_gps = EXIF_GPS_INFO in exif and exif[EXIF_GPS_INFO]

            log.debug(f"File '{file.original_filename}': "
                      f"timestamp={file.timestamp}; "
                      f"exposure={file.exposure_time}; "
                      f"has GPS info = {file.has_gps}")
        else:
            log.debug(f"File '{file.original_filename}' "
                      f"not identified as image (=> not checking EXIF data for timestamp, exposure time, or GPS info).")

    return files


def consolidate_images(files):
    """
    Consolidates ImageFile objects with the same basename.
    Merges information from RAW and processed versions of the same image.
    Warns about discrepancies in metadata between files with the same basename.

    Args:
        files (list[ImageFile]): List of ImageFile objects to consolidate

    Returns:
        list[ImageFile]: Consolidated list of ImageFile objects
    """
    log_title("Consolidating image files")
    # Dictionary to store consolidated files, keyed by basename
    consolidated = {}

    log.info(f"Starting consolidation of {len(files)} files...")

    for file in files:
        if file.basename in consolidated:
            # File with this basename already exists, consolidate information
            existing = consolidated[file.basename]

            # Check for a RAW image file:
            if file.raw_filename and not existing.raw_filename:
                existing.raw_relative_path = file.raw_relative_path
                existing.raw_filename = file.raw_filename
            elif file.raw_filename and existing.raw_filename and file.raw_filename != existing.raw_filename:
                log.warning(f"Multiple RAW formats for {file.basename}: "
                            f"{existing.raw_relative_path}{existing.raw_filename} and "
                            f"{file.raw_relative_path}{file.raw_filename}")

            # Check for a processed image file:
            if file.processed_filename and not existing.processed_filename:
                existing.processed_relative_path = file.processed_relative_path
                existing.processed_filename = file.processed_filename
            elif file.processed_filename and existing.processed_filename and file.processed_filename != existing.processed_filename:
                log.warning(f"Multiple processed files for {file.basename}: "
                            f"{existing.processed_relative_path}{existing.processed_filename} and "
                            f"{file.processed_relative_path}{file.processed_filename}")

            # Check timestamps
            if file.timestamp and not existing.timestamp:
                existing.timestamp = file.timestamp
            elif file.timestamp and existing.timestamp:
                # Allow for small discrepancies in timestamps (up to 2 seconds)
                if abs((file.timestamp - existing.timestamp).total_seconds()) > 2:
                    log.error(f"Timestamp mismatch for {file.basename}: {existing.timestamp} vs {file.timestamp}")
                # Take the earlier timestamp to be safe
                existing.timestamp = min(existing.timestamp, file.timestamp)

            # Check exposure time
            if file.exposure_time and not existing.exposure_time:
                existing.exposure_time = file.exposure_time
            elif (file.exposure_time
                  and existing.exposure_time
                  and abs(file.exposure_time - existing.exposure_time) > 0.001):
                log.warning(f"Exposure time mismatch for {file.basename}: "
                            f"{existing.exposure_time}s vs {file.exposure_time}s")

            # Set has_gps to True if either file has GPS data
            existing.has_gps = existing.has_gps or file.has_gps

        else:
            # First time seeing this basename, add to the consolidated dictionary
            consolidated[file.basename] = file

    # Convert dictionary values back to a list
    result = list(consolidated.values())

    log.info(f"Consolidation complete: {len(files)} files consolidated into {len(result)} unique images")

    # Sort by basename (since order=True is defined in the dataclass)
    result.sort()

    return result


def review_and_cleanup_groups(files, groups):
    # Walk through each group in order to:
    # - drop it if 2 images only
    # - inform the group in group_id of the first image
    for group in groups:
        log.debug(f"Walking through group '{group.group_id}' with {group.n_images} images...")
        if group.n_images == 2:
            log.warning(f"\tDropping group '{group.group_id}' "
                        f"with 2 images: '{group.first_image}' and '{group.last_image}'")
            # set this group.n_images to 0 in groups:
            group.n_images = 0
            for f in files:
                # Remove group_id for each image of this group:
                if f.group_id == f"group_{group.group_id}":
                    # if f.basename == group.last_image:
                    f.group_id = None
                    f.group_type = None
                    log.debug(f"\tDropped group_id '{f.group_id}' for '{f.original_filename}'")
                    break  # only one file to remove to group => exit loop on files
        else:
            # Get ImageFile object from images with basename = group.first_image
            for f in files:
                if f.basename == group.first_image:
                    f.group_id = f"group_{group.group_id}"
                    f.group_type = "group"
                    log.debug(f"\tReassigned group_id '{f.group_id}' to {group.first_image}")
                    break  # only one file to remove to group => exit loop on files
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

    # Optional: sort by timestamp
    # (issue: Canon camera can "RAW image processing" retrospectively and create a processed image with a NEW number
    # that cannot be consolidated with the original raw)
    # sorted_files = sorted([f for f in files if f.timestamp], key=lambda x: x.timestamp)

    if not files:
        log.warning("No files with timestamps available for grouping")
        return files

    log.info(f"Analyzing {len(files)} files with timestamps for series identification")

    groups = []
    previous_image_part_of_group: bool = False
    current_group_id = 0
    last_timestamp = datetime(1970, 1, 1)
    previous_image_basename = None

    # Groups can be: panorama, hdr, focus bracketing, burst, etc.
    # This method only makes groups of pictures.
    # Differentiation between HDR, bracketing focus and other will be made differently later.

    for file in files:
        log.debug(f"Verifying if {file.original_filename} is part of a group...")
        # Skip if no timestamp
        if not file.timestamp:
            log.debug(" └→ no timestamp => ignore file (skip)")
            continue  # back to the next element of loop "for file in files..."

        if last_timestamp is None:
            log.debug(" └→ Previous image file had no timestamp defines => this image can be the first of a series...")
            last_timestamp = file.timestamp
            continue  # back to the next element of loop "for file in files..."

        # Calculate time difference from the previous shot (time between to shots, not including exposure time that can be seconds for nightly panoramas)
        time_diff = (file.timestamp - last_timestamp).total_seconds() - file.exposure_time

        # Determine if this is part of a set
        if MIN_TIME_BETWEEN_PANOS >= time_diff >= 0:
            # note: if time_diff is negative, it is mostly because picture number looped
            # (from IMG_9999 back to IMG_0000)
            # if time_diff too big, then not a group/pano
            # Entering here only if possible group/pano.
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
                # update last_image and increment n_images in the current group (last one added to groups):
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

    # # Identify HDR groups by checking exposure variation
    # potential_hdr_groups = {}
    # for file in files:
    #     if file.group_type == "hdr" and file.exposure_time:
    #         if file.group_id not in potential_hdr_groups:
    #             potential_hdr_groups[file.group_id] = []
    #         potential_hdr_groups[file.group_id].append(file)
    #
    # # Verify HDR groups by checking exposure variation
    # for group_id, group_files in potential_hdr_groups.items():
    #     if len(group_files) < 2:
    #         continue
    #
    #     exposures = [f.exposure_time for f in group_files if f.exposure_time]
    #     if not exposures or len(exposures) < 2:
    #         continue
    #
    #     # Check if there's significant exposure variation
    #     min_exp = min(exposures)
    #     max_exp = max(exposures)
    #
    #     # If max exposure is at least 2x min exposure, it's likely HDR
    #     if max_exp / min_exp >= 2:
    #         log.info(f"Confirmed HDR group {group_id} with exposure range: {min_exp}s to {max_exp}s")
    #     else:
    #         # Not enough exposure variation, might be something else
    #         for f in group_files:
    #             if f.group_type == "hdr":
    #                 f.group_type = "burst"  # Reclassify as generic burst
    #                 log.debug(f"Reclassified {f.basename} from HDR to burst (insufficient exposure variation)")

    return files


def confirm_groups(files):
    """
    Walks through each file of files to check the group_id and for each group_id confirm with the user if this group is:
    - [P]anorama: keep group, generate png, create Hugin script (default)
    - [C]ancel: images were incorrectly detected as a group
    - [O]ther: group is correct but not a panorama: keep group, generate png but do not create Hugin script
    """

    new_group_id: int = 0
    group_id: str | None = "group_000_start_while_loop"
    while group_id:
        group_id = None
        first_picture: str | None = None
        last_picture: str | None = None
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
                    f"║ {file.basename:<20}│ {file.timestamp} │ {f'{file.exposure_time:.3f}' if file.exposure_time is not None else '-.---'} ║")

        if group_id:
            print("╚═════════════════════╧═════════════════════╧═══════╝")

            sub_folder_name = create_sub_folder_name(serie_of_photos, False)
            # Ask if this group is confirmed as a Panorama, or Canceled, or something else:
            print(f"What should be done with this group? '{sub_folder_name}' ")
            print("- [P]anorama: keep group, generate png, create Hugin script (default)")
            print("- [C]ancel: images were incorrectly detected as a group")
            print("- [O]ther: group is correct but not a panorama:"
                  " keep group, generate png but do not create Hugin script")
            choice = input("\nEnter your choice [P/C/O]:")
            if choice.lower() == "p" or choice == "":
                log.info("Group confirmed as Panorama")
                choice = "panorama"
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


def create_folder_for_raws(photo_folder):
    # Create folder for "RAWS" if not existing already:
    raw_folder = os.path.join(photo_folder, FOLDER_FOR_RAWS)
    if os.path.isdir(raw_folder):
        log.debug(f"│ Folder '{FOLDER_FOR_RAWS}' already exists")
    else:
        log.info(f"│ Creating folder '{FOLDER_FOR_RAWS}'...")
        os.mkdir(raw_folder)
    return raw_folder


def move_raws_to_folder(photo_folder: str, files):  # -> list<ImageFile>
    """
    Moves a RAW file associated with an ImageFile to the dedicated RAW subfolder.

    Args:
        photo_folder (str): The base folder where the images are stored.
        files (list of ImageFile): The images metadata (that contains raw file paths).

    Returns:
        None
    """
    log_title("Move raw files to adequate folder")

    for file in files:
        if file.raw_filename:
            source_folder = os.path.join(photo_folder, file.raw_relative_path)
            log.debug(f"source_folder = '{source_folder}'")
            destination_folder = create_folder_for_raws(photo_folder)
            if source_folder != destination_folder:  # os.path.samefile(...)
                source_file = os.path.join(source_folder, file.raw_filename)
                destination_file = os.path.join(destination_folder, file.raw_filename)
                log.debug(f"│\tMoving file '{source_file}'")
                log.debug(f"│\t         to '{destination_file}' ...")
                real_dst_ignored = shutil.move(source_file, destination_file)
                file.raw_relative_path = FOLDER_FOR_RAWS
    return files


def get_newest_gpx_in_parent(folder):
    list_of_files = glob.glob(folder + '../*.gpx')  # * means all if need specific format then *.csv
    log.debug(list_of_files)
    if len(list_of_files) == 0:
        log.warning("│ \nlatest_file: No GPX file found!")
        return None
    else:
        latest_file = max(list_of_files, key=os.path.getctime)
        log.info(f"│ \nlatest_file: '{latest_file}'")
        # type(latest_file)
        return str(latest_file)  # FIXME Should be "../abc.gpx"


def parse_gpx_time_range(gpx_path: str) -> Optional[Tuple[datetime, datetime]]:
    """
    Parse a GPX file and extract the time range covered by its trackpoints.

    Args:
        gpx_path: Path to the GPX file

    Returns:
        Tuple of (first_trackpoint_time, last_trackpoint_time) or None if parsing fails
    """
    GPX_NAMESPACES = [
        {'gpx': 'http://www.topografix.com/GPX/1/1'},
        {'gpx': 'http://www.topografix.com/GPX/1/0'},
        {}  # No namespace
    ]

    try:
        tree = ET.parse(gpx_path)
        root = tree.getroot()

        time_elements = []
        # Try different namespace variants
        for ns in GPX_NAMESPACES:
            if ns:
                time_elements = root.findall('.//gpx:trkpt/gpx:time', ns)
            else:
                time_elements = root.findall('.//trkpt/time')
            if time_elements:
                break

        if not time_elements:
            log.warning(f"│ No trackpoints with time found in '{gpx_path}'")
            return None

        times = []
        for time_elem in time_elements:
            time_str = time_elem.text
            if time_str:
                try:
                    # Handle 'Z' suffix (UTC) by replacing with +00:00 for fromisoformat
                    time_str_clean = time_str.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(time_str_clean)
                    # Convert to naive datetime (remove timezone for comparison)
                    times.append(dt.replace(tzinfo=None))
                except ValueError:
                    # Fallback to strptime for strict GPX format
                    try:
                        dt = datetime.strptime(time_str.rstrip('Z'), '%Y-%m-%dT%H:%M:%S')
                        times.append(dt)
                    except ValueError:
                        log.debug(f"│ Could not parse time '{time_str}' in GPX file")
                        continue

        if not times:
            log.warning(f"│ No valid timestamps found in '{gpx_path}'")
            return None

        return (min(times), max(times))

    except ET.ParseError as e:
        log.error(f"│ Failed to parse GPX file '{gpx_path}': {e}")
        return None
    except Exception as e:
        log.error(f"│ Error reading GPX file '{gpx_path}': {e}")
        return None


def search_gpx_upward(start_folder: str) -> List[str]:
    """
    Search for GPX files starting from start_folder and moving upward
    through parent directories until at least one GPX file is found.

    Args:
        start_folder: The directory to start searching from

    Returns:
        List of absolute paths to found GPX files (empty if none found)
    """
    current_folder = os.path.abspath(start_folder)

    # Safety limit to prevent infinite loops (e.g., at filesystem root)
    max_levels = 10
    level = 0

    while level < max_levels:
        gpx_pattern = os.path.join(current_folder, '*.gpx')
        gpx_files = glob.glob(gpx_pattern)

        if gpx_files:
            log.info(f"│ Found {len(gpx_files)} GPX file(s) in '{current_folder}'")
            return gpx_files

        # Move to parent directory
        parent_folder = os.path.dirname(current_folder)

        # Check if we've reached the filesystem root
        if parent_folder == current_folder:
            log.debug("│ Reached filesystem root without finding GPX files")
            break

        current_folder = parent_folder
        level += 1
        log.debug(f"│ No GPX in current folder, checking parent (level {level})...")

    log.warning("│ No GPX files found in directory hierarchy")
    return []


def find_matching_gpx(photo_folder: str, reference_timestamp: datetime) -> Optional[str]:
    """
    Find a GPX file whose track time range contains the reference timestamp.

    Searches upward from photo_folder until GPX files are found, then selects
    the one that contains the reference timestamp within its trackpoint time range.

    Args:
        photo_folder: Directory containing photos
        reference_timestamp: The photo timestamp to match against GPX tracks

    Returns:
        Path to matching GPX file, or None if no match found
    """
    gpx_files = search_gpx_upward(photo_folder)

    if not gpx_files:
        return None

    log.info(f"│ Checking {len(gpx_files)} GPX file(s) against reference time {reference_timestamp}")

    for gpx_path in gpx_files:
        time_range = parse_gpx_time_range(gpx_path)

        if time_range is None:
            log.debug(f"│ Could not parse time range from '{gpx_path}'")
            continue

        start_time, end_time = time_range
        log.debug(f"│ GPX '{os.path.basename(gpx_path)}': {start_time} to {end_time}")

        if start_time <= reference_timestamp <= end_time:
            log.info(f"│ Found matching GPX: '{gpx_path}' "
                     f"(covers {start_time} to {end_time})")
            return gpx_path

    log.warning(f"│ No GPX file contains the reference timestamp {reference_timestamp}")
    log.warning("│ Available GPX files and their time ranges:")
    for gpx_path in gpx_files:
        time_range = parse_gpx_time_range(gpx_path)
        if time_range:
            log.warning(f"│   - {os.path.basename(gpx_path)}: {time_range[0]} to {time_range[1]}")

    return None


def get_exif_as_dict(file_name):
    """Get exif information from file given in parameter"""
    ret = {}
    with Image.open(file_name) as img:
        exif_data = img._getexif()
        if not exif_data:
            # Have a try if .getexif() works better:
            exif_data = img.getexif()
            if exif_data:
                log.warning(f"│ Method .getexif() worked while ._getexit() failed for file '{file_name}'")
    if exif_data:
        for tag, value in exif_data.items():
            decoded = TAGS.get(tag, tag)
            ret[decoded] = value
    else:
        log.warning(f"│ Unable to get exif information from file '{file_name}' (returning empty dictionary).")
    return ret


def get_exposure_time_from_exif(file_name):
    # Get exposure time (in seconds) of file given in parameter from exif information
    exif_of_filename = get_exif_as_dict(file_name)
    ssv = exif_of_filename['ShutterSpeedValue']

    # Example for exposure of 1/6th of second:
    # ssv._numerator    172032                int
    # ssv._denominator    65536                int
    # ssv._val            Fraction(21, 8)        <class 'fractions.Fraction'>

    # FIXME Correct value is more complicated than that...
    # With former version or Python2, ExposureTime return had the form: (1, 100) for 1/100 of second;
    # to get the result in seconds:
    # ret = exp_time_vect[0] / float(exp_time_vect[1])                             # returns a float
    # ret = timedelta(seconds=exp_time_vect[0] / float(exp_time_vect[1]))  # returns a timedelta
    # return ret

    log.info("│ ssv._numerator / ssv._denominator / ssv._val:")
    try:
        log.info(ssv._numerator)
        log.info(ssv._denominator)
        log.info(ssv._val)
    except AttributeError:
        log.info("│ AttributeError! (ignored and proceeding...)")
    return 1


def get_date_time_original_from_exif(file_name):
    # Candidates to get le exposure date-time from EXIF:
    #  DateTimeOriginal    ***     time when the original image data was generated = time the picture was taken
    #  DateTime            **      time the file was changed
    #  DateTimeDigitized   *       time when the image was stored as digital data
    # Get exposure time (in seconds) of file given in parameter from exif information
    # Times are always from the BEGINNING of the capture (make total sense for long exposures)
    # To determine: picture 4029 STARTED to be taken at 2016-11-06 00:30:~49 and ENDED at 00:31:~19
    # DateTimeOriginal = 2016:11:06 00:30:48
    # DateTimeDigitized = 2016:11:06 00:30:48
    # DateTime = 2016:11:06 00:30:48
    _EXIF_TIME_FORMAT = '%Y:%m:%d %H:%M:%S'
    exif_of_file_name = get_exif_as_dict(file_name)
    try:
        dt_original_unicode = exif_of_file_name['DateTimeOriginal']
    except KeyError:
        log.error(f"│ KeyError occurred when trying to find exif parameter 'DateTimeOriginal' "
                  f"│ from file '{file_name}' (returning None as datetime).")
        return None
    # log.info("get_date_time_original_from_exif(%r)" % fileName)
    # log.info("    dt_original_unicode = %r" % dt_original_unicode)
    ret = datetime.strptime(dt_original_unicode, _EXIF_TIME_FORMAT)
    # Would it be better:
    # ret = datetime.st_mtime
    return ret


def get_files(photo_folder):
    # Obtain list of raw files:
    folder_content = sorted(os.listdir(photo_folder))
    log.info(f"{len(folder_content)} files found in folder {photo_folder}")

    # Create list of RAW files:
    raw_files = list()
    rendered_files = list()
    other_files = list()
    gpx_file = ''
    for file_name in folder_content:
        file_ext = file_name[-4:].lower()
        if file_ext in RAW_EXTENSIONS:
            raw_files.append(file_name)
        elif file_ext in RENDERED_EXTENSIONS:
            rendered_files.append(file_name)
        else:
            log.warning(f"│ Unable to determine type of file '{file_name}'.")
            other_files.append(file_name)

    return raw_files, rendered_files, other_files


def create_avif_for_raw(photo_folder, raw_file):
    log.info(f"│\t  ↳ Create avif from file '{raw_file}'")
    # FIXME Work in progress: method to be implemented yet!
    src_image_fullpath = photo_folder + FOLDER_FOR_RAWS + "/" + raw_file
    log.debug(f"│\t    opening file '{src_image_fullpath}'...")
    split_filename = os.path.splitext(raw_file)
    basename = split_filename[0]
    extension: str = split_filename[1]
    # if extension.lower() in {"exif"}:
    #    # Pillow accepted formats: https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html
    if extension.lower() == "cr3":
        image_cr3 = None  # TODO Implementation on hold...
    # Below command allowed to create a jpg:
    # python parse_cr3.py /media/thomas/deimos.photo/2025/2025-02-02\ Sun\ -\ Panorama\ St\ Benoit\ et\ étang\ Laurent/IMG_8441.CR3 -v 1 -x
    image = Image.open(src_image_fullpath)
    # In order to get cr3 info., see https://github.com/lclevy/canon_cr3
    basename = os.path.splitext(raw_file)[0]
    image.save(photo_folder + basename + ".jpg")
    image.save(photo_folder + basename + ".avif")
    image.save(photo_folder + basename + ".tif")
    return


def move_raws(photo_folder, raw_files, rendered_files, other_files):
    log_title("Search for raw files for moving to adequate folder")

    if len(raw_files) == 0:
        log.info("│ No raw file found.")
    else:
        create_folder_for_raws(photo_folder)

        log.info("│ Moving raw images to their folder...")
        for raw_file in raw_files:
            log.debug(f"│\tMoving file '{raw_file}'...")
            os.rename(photo_folder + raw_file, photo_folder + FOLDER_FOR_RAWS + "/" + raw_file)
            # check if any file associated with raw exists (like processing profiles .pp3 or others):
            for other_file in other_files:
                if other_file.startswith(raw_file):
                    log.debug(f"│\t(and associate file '{other_file}'...)")
                    os.rename(photo_folder + other_file, photo_folder + FOLDER_FOR_RAWS + "/" + other_file)
            basename = os.path.splitext(raw_file)[0]
            found_rendered_file = False
            for rendered_file in rendered_files:
                if rendered_file.startswith(basename):
                    found_rendered_file = True
                    log.debug("FOUND RENDERED FILE")
                    pass
            if not found_rendered_file:
                create_avif_for_raw(photo_folder, raw_file)


def create_processed_images(photo_folder, files):
    log.debug("START .create_processed_images()")
    for file_entry in files:
        if file_entry.raw_filename and not file_entry.processed_filename:
            if file_entry.group_id:
                log.debug(f" ├→ Create png from '{file_entry.raw_filename}' for group '{file_entry.group_id}'")
                file_entry = create_tiff_16bit_from_raw(photo_folder, file_entry)
            else:  # no group_id for current file_entry
                log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' for individual image")
                # TODO Implement creation of avif in main folder
    return files


def create_tiff_16bit_from_raw(photo_folder, file_entry):
    """
    Create a 16-bit ITFF image from a RAW file for panorama processing.
    Uses dcraw to extract the RAW data.
    
    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): file entry containing required info (input file name, group, ...)
    """
    log.debug(f" ┊   └→ Creating 16-bit TIFF from RAW file '{file_entry.raw_filename}'")

    # Construct full path to RAW file (assuming it's in the RAW subfolder)
    raw_file_path = os.path.join(photo_folder, file_entry.raw_relative_path, file_entry.raw_filename)

    # Extract basename without extension for output filename
    basename = os.path.splitext(file_entry.raw_filename)[0]

    # Create group folder if it doesn't exist
    group_folder = os.path.join(photo_folder, file_entry.group_id)
    if not os.path.exists(group_folder):
        os.makedirs(group_folder)
        log.debug(f" ┊      Created group folder: {group_folder}")

    # Output PNG filename
    png_filename = f"{basename}.png"
    png_output_path = os.path.join(group_folder, png_filename)

    try:
        # Use dcraw to extract 16-bit TIFF data from RAW file
        # -T: output TIFF format
        # -4: 16-bit linear output
        # -o 0: output colorspace sRGB
        # -q 3: high quality interpolation
        # -w: use camera white balance

        # dcraw_cmd = [
        #     'dcraw',
        #     '-T',           # Output TIFF format
        #     '-4',           # 16-bit linear output
        #     '-o', '0',      # sRGB output colorspace
        #     '-q', '3',      # High quality interpolation
        #     '-w',           # Use camera white balance
        #     '-c',           # Write to stdout
        #     raw_file_path
        # ]
        # Given that upper caused issues with Canon CR3 images, we prefer the below:
        dcraw_cmd = [
            'dcraw_emu',  # Using libraw directly
            # '-v',           # verbose
            '-T',  # Output TIFF format (keep metadata, etc...)
            '-6',  # 16-bit linear output
            # '-o', '1',      # sRGB D65 (default)
            '-o', '4',  # Kodak ProPhoto RGB D65 (for max gamut and compatibility)
            '-o', '3',  # Wide Gamut RGB D65 (fall-back if upper fails)
            '-q', '3',  # High quality interpolation
            '-w',  # Use camera white balance
            # '-H', '2',      # Maybe the best in 8 bits but loses details in highlights in 16 bits
            '-H', '1',  # Keep as much information as possible (highlights managed after merging)
            '-fbdd', '1',  # noise reduction (?)
            raw_file_path
        ]

        log.debug(f" ┊      Running dcraw command: {' '.join(dcraw_cmd)}")
        # Execute dcraw and capture output
        with open(f"/tmp/dcraw_{file_entry.basename}.log", 'wb') as temp_file:
            result = subprocess.run(dcraw_cmd, stdout=temp_file, stderr=subprocess.PIPE, check=True)
            log.debug(f" ┊      dcraw command result: {result}")
        log.debug(f" ┊      dcraw command output: see file '{temp_file}'")

        expected_tiff_out = raw_file_path + ".tiff"
        shutil.move(
            expected_tiff_out,
            os.path.join(group_folder, f"{basename}.tiff")
        )

    except subprocess.CalledProcessError as e:
        log.error(f" ┊    → dcraw failed for {file_entry.raw_filename}: {e.stderr.decode()}")
        log.error(" ┊")
        log.error(" ┊      STRONGLY ADVISE TO INSTALL dcraw !")
        log.error(" ┊        MacOS: `brew install dcraw`")
        log.error(" ┊        Linux: `sudo apt install dcraw` (Ubuntu), ...")
        log.error(" ┊        ...")
        log.error(" ┊")
        # Fallback: try using PIL directly (may not be 16-bit)
        try:
            with Image.open(raw_file_path) as img:
                img.save(png_output_path, 'PNG')
                log.warning(f" ┊    → Created PNG using PIL fallback (may not be 16-bit): {png_output_path}")
        except Exception as pil_error:
            log.error(f" ┊    → Both dcraw and PIL failed for {file_entry.raw_filename}: {pil_error}")

    except FileNotFoundError:
        log.error(f" ┊    → dcraw not found. Please install dcraw for RAW processing.")
        # Fallback to PIL
        try:
            with Image.open(raw_file_path) as img:
                img.save(png_output_path, 'PNG')
                log.warning(f" ┊    → Created PNG using PIL fallback (may not be 16-bit): {png_output_path}")
        except Exception as pil_error:
            log.error(f" ┊    → PIL fallback also failed for {file_entry.raw_filename}: {pil_error}")

    except Exception as e:
        log.error(f"Unexpected error processing {file_entry.raw_filename}: {e}")


def geotag_move_backups(photo_folder):
    # sleep(5)  # Wait a bit for files to be effectively written (if not waiting, files *_original are not moved)
    # Si des sauvegardes (backup) des photos ont été crées, on les déplace vers un nouveau dossier
    backup_files = glob.glob(photo_folder + '*_original')  # get backup files within the directory
    log.info(f"│ Found Backup files by geo-tag: {backup_files}")
    while len(backup_files) > 0:
        log.info(f"│ Backup files by geo-tag: {backup_files}")
        # Create 'Backups' folder is not existing yet:
        if os.path.isdir(photo_folder + FOLDER_BACKUP_GPS):
            log.info(f"│ Folder '{FOLDER_BACKUP_GPS}' for backup pictures already exists.")
        else:
            log.info(f"│ Create folder '{FOLDER_BACKUP_GPS}' for backup pictures...")
            os.mkdir(photo_folder + FOLDER_BACKUP_GPS)

        log.info("│ Moving original files (before modifying with geo-tag):")
        for backup_file in backup_files:
            log.info(f"│ \tMoving '{backup_file}'...")
            # try:
            os.rename(backup_file, backup_file.replace(photo_folder, photo_folder + FOLDER_BACKUP_GPS + "/"))
        # Due to some files being missed sometime, we're getting again list of original files that may have been missed
        # in first call (just over 'while'):
        backup_files = glob.glob(photo_folder + '*_original')  # get backup files within the directory


def get_time_shift():
    # +2 for CEST (summer time for Paris, ...)
    # +1 for CET (winter time for Paris, ...)
    # +1 for summer time in Portugal
    # +0 for winter time in Portugal
    # -3 for summer time in Brasil/Curitiba
    return input('Please inform time offset (timezone) as "+n" ("+1" will be used as default): ') or "+1"


def geotag_pictures(photo_folder: str, file_gpx: str, files: List = None):
    """
    Geotag photos using a GPX track file.

    Args:
        photo_folder: Directory containing photos to geotag
        file_gpx: Path to GPX file (priority), or empty string for automatic search
        files: List of ImageFile objects with timestamps for matching GPX

    TODO: Skip photos that already have GPS data (use files[].has_gps to filter)
    """
    log_title("geo-tagging jpegs pictures")

    if file_gpx and file_gpx != '':
        # Command line argument has priority
        file_gpx_with_folder = file_gpx
    else:
        # Automatic search based on photo timestamp
        reference_timestamp = None

        if files:
            # Get timestamp from first photo that has one
            for f in files:
                if f.timestamp:
                    reference_timestamp = f.timestamp
                    log.info(f"│ Using reference timestamp from '{f.basename}': {reference_timestamp}")
                    break

        if reference_timestamp:
            file_gpx_with_folder = find_matching_gpx(photo_folder, reference_timestamp)
        else:
            log.warning("│ No photo with timestamp found, falling back to newest GPX in parent")
            file_gpx_with_folder = get_newest_gpx_in_parent(photo_folder)

    if file_gpx_with_folder:  # variable defined (not None)

        offset = get_time_shift()
        # gpysync_cmd = "python ~/Applications/GPicSync/src/gpicsync.py" \
        #              + " --directory='" + photo_folder \
        #              + "' --gpx='" + file_gpx_with_folder \
        #              + "' --offset=" + offset \
        #              + " --time-range=3000"
        # subprocess.call(
        #     gpysync_cmd,
        #     shell=True)
        log.info("│ Launching GPicSync...")
        child = subprocess.Popen(
            ["python", "/home/thomas/Applications/GPicSync/src/gpicsync.py",
             "--directory=" + photo_folder,
             "--gpx=" + file_gpx_with_folder,
             "--offset=" + offset,
             "--time-range=3000"]
            # , stdout=subprocess.PIPE
        )
        streamdata = child.communicate()[0]  # Unused value but required to wait for end of process ?
        log.debug("│ Waiting for Return Code from GPicSync...")
        rc = child.returncode
        # log.debug("GPicSync stdout:")  # .format(str(streamdata)))
        # print(streamdata)
        if rc == 0:
            log.info(f"│ GPicSync -> return code = {rc}")
        else:
            log.warning(f"│ {ERROR}GPicSync -> return code = {rc}")

        # According to
        # http://stackoverflow.com/questions/3781851/run-a-python-script-from-another-python-script-passing-in-args
        # it would be better to use the __main__ from gpicsync.py
        # => Tentative below, but unsure about GPicSync implementation...

        # options_dir = photo_folder  # --directory
        # options_gpx = [file_gpx_with_folder]  # --gpx
        # options_offset = offset  # --offset=
        # options_timerange = 3000  # --time-range
        #
        # options_qr_time_image = None
        #
        # log.debug("Launching GPicSync with GPX file '{0}'".format(options_gpx))
        # geo = gpicsync.GpicSync(gpxFile=options_gpx,
        #                         # tcam_l=options_tcam,
        #                         # tgps_l=options_tgps,
        #                         UTCoffset=float(options_offset),
        #                         timerange=int(options_timerange),
        #                         # timezone=options_timezone,
        #                         qr_time_image=options_qr_time_image)
        #
        # log.debug("Launching GPicSync with FileList from '{0}'".format(options_dir))
        # files = list(gpicsync.getFileList(options_dir))
        #
        # if options_qr_time_image is not None:
        #     qr_time_images = [(options_qr_time_image, options_qr_time_image)]
        #     if options_qr_time_image == 'auto':
        #         qr_time_images = files
        #     geo.parseQrTime(qr_time_images)
        #
        # for fileName, filePath in files:
        #     print("\nFound fileName ", fileName, " Processing now ...")
        #     geo.syncPicture(filePath)[0]

    else:  # from "if file_gpx_with_folder" => file_gpx_with_folder is None
        log.info("│ No GPX file usable => GPicSync not launched.")

    geotag_move_backups(photo_folder)


def create_sub_folder_name(serie_of_photos, serie_is_hdr):
    # create sub-folder name:
    first_picture = serie_of_photos[0]  # Added [:-4] used to be there to drop the extension
    last_picture = serie_of_photos[len(serie_of_photos) - 1]
    subfolder_name = first_picture + "-"  # Starts with the name of first picture
    i = 0
    while True:
        if first_picture[i] == last_picture[i]:
            # i-th char of each file is identical
            i = i + 1
            if i >= len(first_picture) or i >= len(last_picture):
                break
        else:
            # i-th char of each file is different => exiting the loop
            break
    subfolder_name = subfolder_name + last_picture[i:] + "_" + str(len(serie_of_photos))
    if serie_is_hdr:
        subfolder_name = subfolder_name + "_HDR"
    return subfolder_name


def group_pictures_by_time(photo_folder, rendered_files):
    log_title("Manage remaining jpegs")

    rendered_files.sort()
    # os.stat_float_times(False)
    time_org_capture_began = list()
    time_org_capture_ended = list()
    time_from_previous_image = list()
    series_of_photos = list()

    log.info("│ Pict.\tSerie\tFile name   \ttimestamp \ttime since")
    log.info("│ Nr   \tNr   \t            \t          \tprevious")
    for rendered_file in rendered_files:
        i = len(time_from_previous_image)
        # time_org_capture_began.append(os.stat(photoFolder+rendered_file).st_mtime)
        date_time_org_image = get_date_time_original_from_exif(photo_folder + rendered_file)
        time_org_capture_began.append(date_time_org_image)

        # FIXME Once get_exposure_time_from_exif() is fixed, uncomment and resolve below:
        # time_org_capture_ended.append(date_time_org_image + get_exposure_time_from_exif(photo_folder + rendered_file))
        time_org_capture_ended.append(date_time_org_image)

        if i == 0:
            # First picture => time from previous = 9999 sec.
            time_from_previous_image.append(9999)
        elif (time_org_capture_began[i] is None) or (time_org_capture_ended[i - 1] is None):
            time_from_previous_image.append(9999)
        else:
            #  EXACT time between 2 pictures =
            #           (time for picture n)
            #       - [ (time from picture n-1) + (ExposureTime from picture n-1) ]
            #  ExposureTime
            time_from_previous_image.append((time_org_capture_began[i] - time_org_capture_ended[i - 1]).total_seconds())

        if time_from_previous_image[i] > MIN_TIME_BETWEEN_PANOS:
            # Then we have a new set of picture:
            series_of_photos.append(list())
            division_mark = "^^^^^^^^^^"
        else:
            # else... current picture is part of the same serie as previous image (=> panorama or HDR)
            division_mark = "          "
        series_of_photos[len(series_of_photos) - 1].append(rendered_file)
        # log.info("%d\t%d\t%s\t%d\t%d\t%s" %
        # (i, len(series_of_photos), rendered_file,
        #  time_org_capture_began[i], time_from_previous_image[i], division_mark ))
        log.info("│ %d\t%d\t%s\t%s\t%s\t%s" % (i,
                                               len(series_of_photos),
                                               rendered_file,
                                               time_org_capture_began[i],
                                               time_from_previous_image[i],
                                               division_mark))

    for serie_of_photos in series_of_photos:
        n_photo_in_serie = len(serie_of_photos)
        serie_is_hdr = False
        if n_photo_in_serie >= 3:
            # Then we have a series of pictures (considered as a panorama)
            log.info("│ Current set: %d pictures => panorama or HDR" % n_photo_in_serie)
            if n_photo_in_serie == 3:  # ! We consider that HDR are only 3 set of pictures... should be improved later
                # Exception case for HDR: only the 2 pictures over and under-exposed are moved. We keep the "middle" one
                log.info("│ Reading EXIF parameter 'ExposureTime' to deduce if it is an HDR...")
                exposure_time = list()
                for i in range(0, n_photo_in_serie):
                    exposure_time.append(get_exposure_time_from_exif(photo_folder + serie_of_photos[i]))
                if exposure_time[0] != exposure_time[1] \
                        and exposure_time[0] != exposure_time[2] \
                        and exposure_time[1] != exposure_time[2]:
                    # Then we have 3 different exposure values => HDR
                    serie_is_hdr = True

            subfolder_name = create_sub_folder_name(serie_of_photos, serie_is_hdr)

            if os.path.isdir(photo_folder + subfolder_name):
                log.warning(f"│ Folder '{subfolder_name}' already exists")
            else:
                log.info(f"│ Creating sub-folder '{subfolder_name}'...")
                os.mkdir(photo_folder + subfolder_name)

            log.info(f"│ Moving pictures to '{subfolder_name}'...")
            is_first_file = True
            for rendered_file in serie_of_photos:
                if serie_is_hdr and is_first_file:
                    # If this is an HDR serie, the first file (normal exposure +0EV) is left in folder
                    log.info(f"│ \tFile '{rendered_file}' from HDR set is left in main folder...")
                else:
                    log.info(f"│ \tMoving file '{rendered_file}'...")
                    os.rename(photo_folder + rendered_file, photo_folder + subfolder_name + "/" + rendered_file)
                is_first_file = False

            # The set of pictures is now in folder, ready to be computed
            if not serie_is_hdr:
                create_panos_script = photo_folder + "create_panos_script.sh"
                if os.path.exists(create_panos_script):
                    append_write = 'a'  # append if already exists
                else:
                    append_write = 'w'  # make a new file if not

                with open(create_panos_script, append_write) as create_panos_script:
                    create_panos_script.writelines(
                        "nice -n 19 ~/python/create_panorama.py \"" + photo_folder + subfolder_name + "/\"\n"
                    )

        else:
            # we have a "serie" of 1 or 2 pictures: ignore and proceed with next pictures
            log.info(f"│ Current set: only {n_photo_in_serie} picture(s) => ignored")

    log.info("│ Terminated.")
    log.info("│ ")
    log.info("│ Panorama may have been added in:")
    log.info("│ cat /home/thomas/Images/2017\\ PegASUS\\ seul/create_panos_script.sh")


def create_avif_for_raws(photo_folder):
    """Check if a raw that is not part of a panorama has a jpeg/avif picture in photo folder.
    If none, a default one will be created from raw."""
    return


def sort_photos(photo_folder: str, gpx_file: str) -> None:
    """
    Organise pictures in the folder.

    :param photo_folder: Photo folder to be sorted
    :param gpx_file: GPS tracker file to geo-localize picture (if not already)
    :return: exit code 0 if no errors
    """

    files = scan_directory(photo_folder)
    log.info(f"Found {len(files)} files in {photo_folder}")
    log_files(files, photo_folder)

    # Extract metadata from images:
    files = extract_image_metadata(photo_folder, files)
    log_files(files, photo_folder)

    # Geo-tag pictures using GPX file
    geotag_pictures(photo_folder, gpx_file, files)

    # Consolidate files with the same basename:
    files = consolidate_images(files)
    log_files(files, photo_folder)

    # Identify image groups
    files = identify_advanced_image_groups(files)

    files = confirm_groups(files)

    # files = create_processed_images(files)

    files = move_raws_to_folder(photo_folder, files)
    log_files(files, photo_folder)

    files = create_processed_images(photo_folder, files)

    log_title("EXIT")
    log_files(files, photo_folder)
    exit(-100)

    # Older method being deprecated:
    (raw_files, rendered_files, other_files) = get_files(photo_folder)

    move_raws(photo_folder, raw_files, rendered_files, other_files)

    # if no gpx_file defined, pick a gpx file from other_files if any...
    geotag_pictures(photo_folder, gpx_file, None)

    group_pictures_by_time(photo_folder, rendered_files)

    # TODO create_avif_for_raws(photo_folder)

    return


if __name__ == "__main__":

    # TODO Checks "panostart" command
    # panostart --output Makefile --projection 0 --fov 50 --nostacks --loquacious *.JPG

    argList = list(argv)
    nbArg = len(argList) - 1
    log.info(f"{nbArg} arguments reçus: {argList}")
    # Tests arguments validity:
    if nbArg < 1 or nbArg > 2:
        log.critical(ERROR)
        log.critical(f"{argList[0]} requires 1 or 2 arguments:")
        log.critical(f"{argList[0]} pictures_path [gpx_path]")
        log.critical("Where:")
        log.critical("- pictures_path is the path where the pictures to sort are")
        log.critical("- gpx_path is the path of the gpx track file (optional)")
        exit(RC_ATTRIBUTES_ERROR)

    # Repertoire à analyser:
    photo_folder_arg = argList[1]
    if nbArg > 1:
        gpx_file_arg = argList[2]
    else:
        gpx_file_arg = None

    if not os.path.isdir(photo_folder_arg):
        log.error(f"Folder '{photo_folder_arg}' given as argument is not detected as valid.")
        exit(RC_PATH_ERROR)

    sort_photos(photo_folder_arg, gpx_file_arg)
