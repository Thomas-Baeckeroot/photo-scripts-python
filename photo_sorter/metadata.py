# -*- coding: utf-8 -*-
"""
EXIF metadata extraction via exiftool.

Functions:
    get_exif_with_exiftool()  — Low-level exiftool JSON call
    get_exif()                — Unified EXIF access (currently delegates to exiftool)
    parse_exposure_time()     — Convert EXIF exposure value to float seconds
    extract_image_metadata()  — Batch extraction of timestamp/exposure/GPS for ImageFile list
"""

import json
import logging
import os
import subprocess
from datetime import datetime

from photo_sorter.display import log_title

log = logging.getLogger(__name__)


def get_exif_with_exiftool(filepath):
    """
    Extract EXIF metadata from a file using exiftool (JSON output).

    Args:
        filepath (str): Full path to the image file

    Returns:
        dict: EXIF tag/value dictionary, or empty dict on failure
    """
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
    """
    Unified EXIF access point. Currently delegates to exiftool.

    If EXIF data should be extracted without exiftool in the future,
    an alternative backend can be added here.
    """
    return get_exif_with_exiftool(filepath)


def parse_exposure_time(value):
    """
    Parse an EXIF exposure time value and return it as a float (seconds).

    Handles:
        - Tuple (numerator, denominator) from PIL EXIF
        - String fraction "1/80"
        - Direct float or string representation

    Returns:
        float or None on parse failure
    """
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

    Args:
        photo_folder (str): Root location of pictures in 'files'
        files (list[ImageFile]): List of ImageFile objects to process

    Returns:
        list[ImageFile]: Updated list of ImageFile objects with extracted metadata
    """
    log_title("Extracting image metadata")
    # Define EXIF tag constants
    EXIF_DATETIME_ORIGINAL = 'DateTimeOriginal'
    EXIF_DATETIME = 'DateTime'
    EXIF_EXPOSURE_TIME = 'ExposureTime'
    EXIF_GPS_INFO = 'GPSInfo'
    EXIF_PICTURE_STYLE = 'PictureStyle'
    EXIF_COLOR_TEMPERATURE = 'ColorTemperature'

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

            # Extract picture style (Canon: Standard, Portrait, Landscape, ...)
            if EXIF_PICTURE_STYLE in exif:
                file.picture_style = exif[EXIF_PICTURE_STYLE]

            # Extract color temperature (for illuminant-correct color rendering, Phase 3)
            if EXIF_COLOR_TEMPERATURE in exif:
                try:
                    file.color_temperature = int(exif[EXIF_COLOR_TEMPERATURE])
                except (ValueError, TypeError):
                    log.warning(f"Invalid ColorTemperature in "
                                f"{file.original_filename}: "
                                f"{exif[EXIF_COLOR_TEMPERATURE]}")

            log.debug(f"File '{file.original_filename}': "
                      f"timestamp={file.timestamp}; "
                      f"exposure={file.exposure_time}; "
                      f"has GPS info = {file.has_gps}; "
                      f"picture style = {file.picture_style}; "
                      f"color temp = {file.color_temperature}")
        else:
            log.debug(f"File '{file.original_filename}' "
                      f"not identified as image (=> not checking EXIF data for timestamp, exposure time, or GPS info).")

    return files
