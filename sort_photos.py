#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLI entry point for sorting a single photo folder.

Usage:
    ./sort_photos.py pictures_path [gpx_path] [--dark-frame path]

This thin wrapper parses CLI arguments, loads configuration, and delegates
to the photo_sorter package for the actual processing pipeline.
"""

import logging
import os
from sys import argv

from photo_sorter.config import load_configuration
from photo_sorter.constants import ERROR, RC_ATTRIBUTES_ERROR, RC_PATH_ERROR
from photo_sorter.pipeline import sort_photos

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(filename)s:%(lineno)d\t%(message)s')
log = logging.getLogger("sort_photos.py")


if __name__ == "__main__":

    # Load configuration from ~/.config/sort_photo.conf
    app_config = load_configuration()

    # Parse command line arguments:
    # Positional: pictures_path [gpx_path]
    # Optional:   --dark-frame /path/to/dark_frame.pgm
    positional_args = []
    dark_frame_arg = None

    arg_list = list(argv[1:])  # skip script name
    i = 0
    while i < len(arg_list):
        if arg_list[i] == '--dark-frame':
            if i + 1 < len(arg_list):
                dark_frame_arg = arg_list[i + 1]
                i += 2
            else:
                log.critical("--dark-frame requires a path argument")
                exit(RC_ATTRIBUTES_ERROR)
        else:
            positional_args.append(arg_list[i])
            i += 1

    log.info(f"{len(positional_args)} positional arguments: {positional_args}")

    if len(positional_args) < 1 or len(positional_args) > 2:
        log.critical(ERROR)
        log.critical(f"{argv[0]} pictures_path [gpx_path] [--dark-frame path]")
        log.critical("Where:")
        log.critical("- pictures_path is the path where the pictures to sort are")
        log.critical("- gpx_path is the path of the gpx track file (optional)")
        log.critical("- --dark-frame path to a dark frame PGM file for hot pixel subtraction (optional)")
        exit(RC_ATTRIBUTES_ERROR)

    photo_folder_arg = positional_args[0]
    gpx_file_arg = positional_args[1] if len(positional_args) > 1 else None

    # CLI --dark-frame overrides config file value
    if dark_frame_arg:
        app_config.dark_frame_path = os.path.expanduser(dark_frame_arg)

    if app_config.dark_frame_path and not os.path.isfile(app_config.dark_frame_path):
        log.warning(f"Dark frame file '{app_config.dark_frame_path}' not found. "
                    "Proceeding without dark frame subtraction.")
        app_config.dark_frame_path = None

    if not os.path.isdir(photo_folder_arg):
        log.error(f"Folder '{photo_folder_arg}' given as argument is not detected as valid.")
        exit(RC_PATH_ERROR)

    sort_photos(photo_folder_arg, gpx_file_arg, app_config)
