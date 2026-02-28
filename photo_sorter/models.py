# -*- coding: utf-8 -*-
"""
Data models (dataclasses) for the photo_sorter package.

These structures carry data through the pipeline. They have no dependencies
on other photo_sorter modules.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(order=True)
class ImageFile:
    """Represents a single image (possibly with both a RAW and a processed version)."""
    basename: str
    original_filename: str
    raw_relative_path: str
    raw_filename: Optional[str] = None
    processed_relative_path: Optional[str] = None
    processed_filename: Optional[str] = None
    timestamp: Optional[datetime] = None
    exposure_time: Optional[float] = None
    has_gps: bool = False
    group_id: Optional[str] = None      # Group identifier (panorama, HDR, etc.)
    group_type: Optional[str] = None    # Group type: "panorama", "hdr", "focus", "group"
    picture_style: Optional[str] = None # Canon PictureStyle (Standard, Portrait, Landscape, ...)


@dataclass(order=True)
class GroupInfo:
    """Represents a detected group of related images (panorama, HDR, etc.)."""
    group_id: int
    first_image: str
    last_image: str
    n_images: int
    # todo To be used later to determine the type of group:
    # group_type: str = "group"  # "group" meaning undetermined. Changed after to "panorama", "hdr", "focus", ...
    # total_shooting_time: float = 0.0
    # min_ev: float = None  # with EV = ISO * exposition_time / (f-stop)^2
    # max_ev: float = None
