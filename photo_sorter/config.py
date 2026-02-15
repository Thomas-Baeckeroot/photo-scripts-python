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


@dataclass
class AppConfig:
    """Application configuration, loaded from config file and/or CLI arguments."""
    root_folder: str = "~/Images"
    folder_for_raws: str = "RAW"
    dark_frame_path: Optional[str] = None


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
            'dark_frame': ''    # Path to dark frame file (PGM) for hot pixel subtraction.
                                # Empty = disabled. Can be overridden by --dark-frame CLI argument.
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

    return AppConfig(
        root_folder=root_folder,
        folder_for_raws=folder_for_raws,
        dark_frame_path=dark_frame,
    )
