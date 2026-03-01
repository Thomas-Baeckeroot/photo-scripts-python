# -*- coding: utf-8 -*-
"""
Constants shared across the photo_sorter package.

These values are used by multiple modules and should not be modified at runtime.
"""

# File extension sets for image type identification
RAW_EXTENSIONS = {'.crw', '.cr2', '.cr3', '.nef'}
RENDERED_EXTENSIONS = {'.jpg', '.jpeg', '.heif', '.avif'}

# MAXIMUM time allowed between each picture of a panorama (in seconds):
MIN_TIME_BETWEEN_PANOS = 15  # Can be corrected between 19 and 7.5...
# 13.5 was considered good for a good time
# For room panorama, up to 40 s. were needed between 2 shots.

# Folder to which backups of photos will be moved before applying geo-tagging
# (relative to photo_folder, without final '/'):
FOLDER_BACKUP_GPS = "BackupBeforeGPS!AE!"

# Return codes
RC_ATTRIBUTES_ERROR = -1
RC_PATH_ERROR = -2

# Default DCP profile directory (Canon EOS R7, installed by Adobe Camera Raw / Lightroom).
# Contains per-style DCP profiles: Camera Standard.dcp, Camera Portrait.dcp, etc.
DEFAULT_DCP_PROFILE_DIR = (
    "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/"
    "Camera/Canon EOS R7/"
)

# Default DCP profile path (Canon EOS R7 "Camera Standard").
# Used as fallback when PictureStyle is unknown or no per-style DCP found.
DEFAULT_DCP_PROFILE_PATH = (
    DEFAULT_DCP_PROFILE_DIR + "Canon EOS R7 Camera Standard.dcp"
)

# DNG CalibrationIlluminant code → approximate correlated color temperature (Kelvin).
# From EXIF LightSource enum (used by the DNG specification for CalibrationIlluminant tags).
ILLUMINANT_TEMP = {
    17: 2856,   # Standard Light A (tungsten)
    18: 4874,   # Standard Light B (direct sunlight)
    19: 6774,   # Standard Light C (overcast)
    20: 5503,   # D55
    21: 6504,   # D65
    22: 7504,   # D75
    23: 5003,   # D50
}

# ANSI escape code for error messages
ERROR = '\033[1;31mError:\033[0m '
