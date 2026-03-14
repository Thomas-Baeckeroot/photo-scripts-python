# -*- coding: utf-8 -*-
"""
EXIF metadata extraction via exiftool.

Functions:
    get_exif_with_exiftool()  — Low-level exiftool JSON call
    get_exif()                — Unified EXIF access (currently delegates to exiftool)
    parse_exposure_time()     — Convert EXIF exposure value to float seconds
    extract_image_metadata()  — Batch extraction of timestamp/exposure/GPS for ImageFile list
    copy_exif_from_raw()      — Copy all EXIF metadata from a RAW file to a processed output
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


def copy_exif_from_raw(raw_path, output_path):
    """
    Copy all EXIF/IPTC/XMP metadata from a RAW file to a processed output file.

    Uses exiftool -TagsFromFile to transfer camera metadata (Make, Model, Lens,
    Aperture, ExposureTime, ISO, WhiteBalance, Flash, GPS, etc.) so that image
    viewers and DAM software (e.g. DigiKam) can display photo properties for
    files generated from RAW (AVIF, TIFF).

    Tags that are not writable in the target format are silently skipped by
    exiftool. The output file is modified in-place (-overwrite_original).

    Args:
        raw_path (str):    Full path to the source RAW file (.CR3, .CR2, .NEF, etc.)
        output_path (str): Full path to the processed output file (.avif, .tiff, etc.)

    Returns:
        True on success, False on failure.
    """
    try:
        result = subprocess.run(
            ["exiftool",
             "-TagsFromFile", raw_path,
             "-all:all",
             "-overwrite_original",
             output_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        if result.returncode == 0:
            log.debug(f" ┊      EXIF copied from '{os.path.basename(raw_path)}' "
                      f"→ '{os.path.basename(output_path)}'")
            return True
        else:
            log.warning(f" ┊      exiftool returned code {result.returncode} "
                        f"copying EXIF to '{output_path}': {result.stderr.strip()}")
            return False
    except Exception as e:
        log.error(f" ┊      Failed to copy EXIF from '{raw_path}' to '{output_path}': {e}")
        return False


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
    EXIF_LENS_MODEL = 'LensModel'
    EXIF_FOCAL_LENGTH = 'FocalLength'
    EXIF_FNUMBER = 'FNumber'

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

            # Extract lens information (for lens distortion correction)
            if EXIF_LENS_MODEL in exif:
                file.lens_model = exif[EXIF_LENS_MODEL]

            if EXIF_FOCAL_LENGTH in exif:
                try:
                    # exiftool may return "45.0 mm" or just 45.0
                    fl_value = exif[EXIF_FOCAL_LENGTH]
                    if isinstance(fl_value, str):
                        fl_value = fl_value.split()[0]  # "45.0 mm" → "45.0"
                    file.focal_length = float(fl_value)
                except (ValueError, TypeError, IndexError):
                    log.warning(f"Invalid FocalLength in "
                                f"{file.original_filename}: "
                                f"{exif[EXIF_FOCAL_LENGTH]}")

            if EXIF_FNUMBER in exif:
                try:
                    file.aperture = float(exif[EXIF_FNUMBER])
                except (ValueError, TypeError):
                    log.warning(f"Invalid FNumber in "
                                f"{file.original_filename}: "
                                f"{exif[EXIF_FNUMBER]}")

            log.debug(f"File '{file.original_filename}': "
                      f"timestamp={file.timestamp}; "
                      f"exposure={file.exposure_time}; "
                      f"has GPS info = {file.has_gps}; "
                      f"picture style = {file.picture_style}; "
                      f"color temp = {file.color_temperature}; "
                      f"lens = {file.lens_model}; "
                      f"focal = {file.focal_length}mm; "
                      f"aperture = f/{file.aperture}")
        else:
            log.debug(f"File '{file.original_filename}' "
                      f"not identified as image (=> not checking EXIF data for timestamp, exposure time, or GPS info).")

    return files
