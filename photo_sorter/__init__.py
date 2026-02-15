# -*- coding: utf-8 -*-
"""
photo_sorter — Automated photo organisation after camera download.

Public API:
    load_configuration()  → AppConfig
    sort_photos()         → None
    create_panorama()     → bool
    AppConfig             → dataclass
"""

from photo_sorter.config import AppConfig, load_configuration
from photo_sorter.panorama import create_panorama
from photo_sorter.pipeline import sort_photos

__all__ = ['AppConfig', 'create_panorama', 'load_configuration', 'sort_photos']
