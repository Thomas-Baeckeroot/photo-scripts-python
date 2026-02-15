# -*- coding: utf-8 -*-
"""
photo_sorter — Automated photo organisation after camera download.

Public API:
    load_configuration()  → AppConfig
    sort_photos()         → None
    AppConfig             → dataclass
"""

from photo_sorter.config import AppConfig, load_configuration
from photo_sorter.pipeline import sort_photos

__all__ = ['AppConfig', 'load_configuration', 'sort_photos']
