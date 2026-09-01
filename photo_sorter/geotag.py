# -*- coding: utf-8 -*-
"""
Geolocation: GPX parsing and geotagging via exiftool.

Functions:
    get_newest_gpx_in_parent()  — Find most recent GPX in parent directory
    parse_gpx_time_range()      — Extract time range from a GPX file
    search_gpx_upward()         — Search for GPX files upward through directories
    find_matching_gpx()         — Find GPX file matching a photo timestamp
    get_time_shift()            — Ask user for timezone offset
    geotag_pictures()           — Apply GPX track to photos via exiftool
    geotag_move_backups()       — Move exiftool backup files to a subfolder
"""

import glob
import logging
import os
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import List, Optional, Tuple

from photo_sorter.constants import ERROR, FOLDER_BACKUP_GPS
from photo_sorter.display import log_title

log = logging.getLogger(__name__)


def get_newest_gpx_in_parent(folder):
    """Find the most recent GPX file in the parent directory of the given folder."""
    list_of_files = glob.glob(folder + '../*.gpx')
    log.debug(list_of_files)
    if len(list_of_files) == 0:
        log.warning("│ \nlatest_file: No GPX file found!")
        return None
    else:
        latest_file = max(list_of_files, key=os.path.getctime)
        log.info(f"│ \nlatest_file: '{latest_file}'")
        return str(latest_file)


def parse_gpx_time_range(gpx_path):
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


def search_gpx_upward(start_folder):
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


def find_matching_gpx(photo_folder, reference_timestamp):
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


def get_time_shift():
    """
    Ask the user for the timezone offset between camera time and GPS (UTC) time.

    Returns:
        str: Timezone offset string (e.g. "+2", "+1", "-3")
    """
    # +2 for CEST (summer time for Paris, ...)
    # +1 for CET (winter time for Paris, ...)
    # +1 for summer time in Portugal
    # +0 for winter time in Portugal
    # -3 for summer time in Brasil/Curitiba
    return input('Please inform time offset (timezone) as "+n" ("+1" will be used as default): ') or "+1"


def geotag_move_backups(photo_folder):
    """
    Move exiftool backup files (*_original) to a dedicated subfolder.

    exiftool creates *_original files when modifying EXIF data.
    This function moves them out of the way.
    """
    backup_files = glob.glob(os.path.join(photo_folder, '*_original'))
    log.info(f"│ Found Backup files by geo-tag: {backup_files}")
    while len(backup_files) > 0:
        log.info(f"│ Backup files by geo-tag: {backup_files}")
        # Create 'Backups' folder if not existing yet:
        backup_folder = os.path.join(photo_folder, FOLDER_BACKUP_GPS)
        if os.path.isdir(backup_folder):
            log.info(f"│ Folder '{FOLDER_BACKUP_GPS}' for backup pictures already exists.")
        else:
            log.info(f"│ Create folder '{FOLDER_BACKUP_GPS}' for backup pictures...")
            os.mkdir(backup_folder)

        log.info("│ Moving original files (before modifying with geo-tag):")
        for backup_file in backup_files:
            log.info(f"│ \tMoving '{backup_file}'...")
            dest_file = os.path.join(backup_folder, os.path.basename(backup_file))
            os.rename(backup_file, dest_file)
        # Re-check in case some files were missed in the first pass:
        backup_files = glob.glob(os.path.join(photo_folder, '*_original'))


def geotag_pictures(photo_folder, file_gpx, files=None):
    """
    Geotag photos using a GPX track file via exiftool.

    Args:
        photo_folder: Directory containing photos to geotag
        file_gpx: Path to GPX file (priority), or empty string for automatic search
        files: List of ImageFile objects with timestamps for matching GPX

    TODO: Skip photos that already have GPS data (use files[].has_gps to filter)
    """
    log_title("geo-tagging pictures with exiftool")

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

    if file_gpx_with_folder:

        offset = get_time_shift()
        # Convert offset format: "+1" -> "+1:00" for exiftool
        if ':' not in offset:
            offset = offset + ":00"

        log.info(f"│ Launching exiftool geotag with GPX '{file_gpx_with_folder}'...")
        log.info(f"│ Timezone offset: {offset}")

        # exiftool -geotag creates _original backup files (handled by geotag_move_backups)
        cmd = [
            "exiftool",
            "-v1",  # enables verbose mode to show progress for each file
            "-geotag", file_gpx_with_folder,
            "-geosync=" + offset,
            photo_folder
        ]
        log.debug(f"│ Command: {' '.join(cmd)}")

        try:
            # Don't capture output to show progress in real-time
            result = subprocess.run(cmd)

            if result.returncode == 0:
                log.info(f"│ exiftool -> success")
            else:
                log.warning(f"│ {ERROR}exiftool -> return code = {result.returncode}")

        except FileNotFoundError:
            log.error(f"│ {ERROR}exiftool not found. Please install exiftool.")
            log.error("│   Ubuntu/Debian: sudo apt install libimage-exiftool-perl")
            log.error("│   macOS: brew install exiftool")

    else:
        log.info("│ No GPX file usable => geotagging skipped.")

    geotag_move_backups(photo_folder)
