# -*- coding: utf-8 -*-
"""
File system operations: scanning directories and moving files.

Functions:
    scan_directory()        — Recursive scan, creates ImageFile objects
    consolidate_images()    — Merge RAW + processed versions by basename
    create_folder_for_raws() — Create the RAW subfolder if needed
    move_raws_to_folder()   — Move RAW files to their dedicated subfolder
"""

import logging
import os
import shutil

from photo_sorter.config import AppConfig
from photo_sorter.constants import RAW_EXTENSIONS, RENDERED_EXTENSIONS
from photo_sorter.display import log_title
from photo_sorter.models import ImageFile

log = logging.getLogger(__name__)


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


def create_folder_for_raws(photo_folder, app_config):
    """
    Create the subfolder for RAW files if it does not already exist.

    Args:
        photo_folder (str): Base photo folder path
        app_config (AppConfig): Application configuration (reads folder_for_raws)

    Returns:
        str: Full path to the RAW folder
    """
    raw_folder = os.path.join(photo_folder, app_config.folder_for_raws)
    if os.path.isdir(raw_folder):
        log.debug(f"│ Folder '{app_config.folder_for_raws}' already exists")
    else:
        log.info(f"│ Creating folder '{app_config.folder_for_raws}'...")
        os.mkdir(raw_folder)
    return raw_folder


def move_raws_to_folder(photo_folder, files, app_config):
    """
    Moves RAW files associated with ImageFile objects to the dedicated RAW subfolder.

    Args:
        photo_folder (str): The base folder where the images are stored.
        files (list[ImageFile]): The images metadata (that contains raw file paths).
        app_config (AppConfig): Application configuration (reads folder_for_raws)

    Returns:
        list[ImageFile]: Updated list with new raw_relative_path values
    """
    log_title("Move raw files to adequate folder")

    for file in files:
        if file.raw_filename:
            source_folder = os.path.join(photo_folder, file.raw_relative_path)
            log.debug(f"source_folder = '{source_folder}'")
            destination_folder = create_folder_for_raws(photo_folder, app_config)
            if source_folder != destination_folder:
                source_file = os.path.join(source_folder, file.raw_filename)
                destination_file = os.path.join(destination_folder, file.raw_filename)
                log.debug(f"│\tMoving file '{source_file}'")
                log.debug(f"│\t         to '{destination_file}' ...")
                shutil.move(source_file, destination_file)
                file.raw_relative_path = app_config.folder_for_raws
    return files
