# -*- coding: utf-8 -*-
"""
Main processing pipeline: orchestrates the full photo sorting workflow.

This module ties together all domain modules in the correct order.
It replaces the former sort_photos() function from the monolithic script.
"""

import logging

from photo_sorter.config import AppConfig
from photo_sorter.display import log_files, log_title
from photo_sorter.file_ops import consolidate_images, move_raws_to_folder, scan_directory
from photo_sorter.geotag import geotag_pictures
from photo_sorter.grouping import confirm_groups, identify_advanced_image_groups
from photo_sorter.metadata import extract_image_metadata
from photo_sorter.raw_processing import create_processed_images

log = logging.getLogger(__name__)


def sort_photos(photo_folder, gpx_file, app_config):
    """
    Organise pictures in the folder.

    Pipeline steps:
        1. Scan directory for image files
        2. Extract EXIF metadata (timestamp, exposure, GPS)
        3. Geotag pictures using GPX file
        4. Consolidate RAW + processed versions by basename
        5. Identify image groups (panoramas, HDR, etc.)
        6. Confirm groups interactively with user
        7. Move RAW files to dedicated subfolder
        8. Generate processed images (AVIF/TIFF) from RAW files

    Args:
        photo_folder (str): Photo folder to be sorted
        gpx_file (str): GPS tracker file to geo-localize pictures (if not already), or None
        app_config (AppConfig): Application configuration
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

    files = move_raws_to_folder(photo_folder, files, app_config)
    log_files(files, photo_folder)

    files = create_processed_images(photo_folder, files, app_config)

    log_title("EXIT")
    log_files(files, photo_folder)
