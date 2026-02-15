#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLI entry point for batch processing: calls sort_photos on each subfolder
of a root photo directory that contains RAW files but no RAW subfolder yet.

Usage:
    ./sort_photos_from_root_folder.py [root_photo_folder]

If no folder is given, the root_folder from configuration is used.
"""

import logging
import os
from sys import argv

from photo_sorter.config import load_configuration
from photo_sorter.constants import RAW_EXTENSIONS
from photo_sorter.pipeline import sort_photos

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(filename)s:%(lineno)d\t%(message)s')
log = logging.getLogger("sort_photos_from_root_folder.py")


def main(photo_root_folder, app_config):
    """
    Walk through subfolders of photo_root_folder and call sort_photos()
    on each folder that contains RAW files but does not yet have a RAW subfolder.
    """
    log.info(f"Checking folder '{photo_root_folder}'...")

    for dirpath, subdirs, filenames in os.walk(photo_root_folder):
        log.info(f"  🅵 Current folder: '{dirpath}'")

        # Skip if we are inside a RAW folder
        if app_config.folder_for_raws in dirpath:
            log.info("   ↳ inside a RAW folder, skipped")
            continue

        # Check if this folder already has a RAW subfolder
        has_raw_folder = app_config.folder_for_raws in subdirs

        if has_raw_folder:
            log.info(f"   ↳ already has '{app_config.folder_for_raws}/' subfolder, skipped")
            continue

        # Check if this folder contains any RAW files
        has_raw_files = any(
            os.path.splitext(f)[1].lower() in RAW_EXTENSIONS
            for f in filenames
        )

        if has_raw_files:
            columns = os.environ.get('COLUMNS', 80)
            log.info("★" * int(columns))
            log.info(f"Start sorting folder '{dirpath}'")
            sort_photos(dirpath, None, app_config)
            log.info("★" * int(columns))


if __name__ == "__main__":
    app_config = load_configuration()

    if len(argv) > 1:
        photo_folder = argv[1]
    else:
        photo_folder = os.path.expanduser(app_config.root_folder)

    if not os.path.isdir(photo_folder):
        log.error(f"Folder '{photo_folder}' is not a valid directory.")
        exit(1)

    main(photo_folder, app_config)
