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

# ANSI escape code for error messages
ERROR = '\033[1;31mError:\033[0m '
