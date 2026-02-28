# -*- coding: utf-8 -*-
"""
Application configuration: loading from config file and AppConfig dataclass.

AppConfig replaces the former global variables (root_folder, FOLDER_FOR_RAWS,
dark_frame_path) with an explicit object passed to functions that need it.
"""

import configparser
import os
from dataclasses import dataclass
from typing import Optional

# Note: DEFAULT_DCP_PROFILE_DIR is used by raw_processing.py for per-PictureStyle
# DCP profile auto-detection. config.py no longer auto-selects a specific DCP path.


@dataclass
class AppConfig:
    """Application configuration, loaded from config file and/or CLI arguments."""
    root_folder: str = "~/Images"
    folder_for_raws: str = "RAW"
    dark_frame_path: Optional[str] = None
    dcp_profile_path: Optional[str] = None   # Path to DCP file for tone curve (None = auto-detect)


def load_configuration() -> AppConfig:
    """
    Load configuration from ~/.config/sort_photo.conf (INI format).

    Default values are used for any missing keys. The config file is optional.

    Returns:
        AppConfig with values from config file merged over defaults.
    """
    config = configparser.ConfigParser()
    # Define default values:
    config.read_dict({
        'Folders': {
            'root': '~/Images',
            'raw': 'RAW'
        },
        'Processing': {
            'dark_frame': '',   # Path to dark frame file (PGM) for hot pixel subtraction.
                                # Empty = disabled. Can be overridden by --dark-frame CLI argument.
            'dcp_profile': '',  # Path to DCP camera profile for tone curve rendering.
                                # Empty = auto-detect Canon EOS R7 Camera Standard if installed.
                                # Set to "none" to disable DCP tone curve.
        }
    })

    config_path = os.path.expanduser('~/.config/sort_photo.conf')
    config.read(config_path)

    root_folder = os.path.expanduser(config['Folders']['root'])
    folder_for_raws = config['Folders']['raw']

    # Dark frame path for hot/dead pixel subtraction (empty string = disabled)
    dark_frame = config['Processing']['dark_frame']
    if dark_frame:
        dark_frame = os.path.expanduser(dark_frame)
    else:
        dark_frame = None

    # DCP profile path for tone curve rendering
    # "none" = explicitly disabled
    # empty  = auto-detect per PictureStyle (create_processed_images handles this)
    # path   = force this specific DCP profile for all images
    dcp_profile = config['Processing']['dcp_profile']
    if dcp_profile.lower() == 'none':
        dcp_profile_path = None
    elif dcp_profile:
        dcp_profile_path = os.path.expanduser(dcp_profile)
    else:
        # Auto mode: leave dcp_profile_path as None
        # create_processed_images() will select per-image based on EXIF PictureStyle
        dcp_profile_path = None

    return AppConfig(
        root_folder=root_folder,
        folder_for_raws=folder_for_raws,
        dark_frame_path=dark_frame,
        dcp_profile_path=dcp_profile_path,
    )
