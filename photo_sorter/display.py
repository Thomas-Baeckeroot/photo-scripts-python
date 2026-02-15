# -*- coding: utf-8 -*-
"""
Console display helpers using box-drawing characters.

Functions:
    log_files()  — Tabular display of ImageFile list
    log_title()  — Decorated section title
"""

import logging

from photo_sorter.models import ImageFile

log = logging.getLogger(__name__)


def log_files(files, folder):
    """Log a table of ImageFile objects at DEBUG level, with a summary at INFO level."""
    if files:
        log.info(f"Found {len(files)} files in '{folder}':")
    else:
        log.warning(f"Did not find any files in '{folder}':")
    log.debug(
        "┌────────────────────────────────────────────┬───────────────────────────────┬───────────────────────────────┬─────────────────────┬───────┬─────┬──────────────┐")
    log.debug(
        "| basename             (original_filename)   | raw                           | processed                     |      timestamp      |exp.(s)| gps | group (type) |")
    previous_group_id = "STARTING"
    if files:
        for file in files:
            if file.group_id != previous_group_id:
                log.debug(
                    "├────────────────────────────────────────────┼───────────────────────────────┼───────────────────────────────┼─────────────────────┼───────┼─────┼──────────────┤")
                previous_group_id = file.group_id
            raw_file_with_path = (
                f"'{file.raw_relative_path}'/'{file.raw_filename}'"
                if file.raw_filename else "  -"
            )
            processed_file_with_path = (
                f"'{file.processed_relative_path}'/'{file.processed_filename}'"
                if file.processed_filename else "  -"
            )
            log.debug(
                f"| {file.basename:<20} ({file.original_filename:<20})"
                f"| {raw_file_with_path:<30}"
                f"| {processed_file_with_path:<30}"
                f"| {f'{file.timestamp}' if file.timestamp is not None else '---- -- -- --:--:--'} "
                f"| {f'{file.exposure_time:.3f}' if file.exposure_time is not None else '-.---'} "
                f"| {'yes' if file.has_gps else 'no '} "
                f"| {file.group_id or '-'} ({file.group_type or '-'}) "
            )
    log.debug(
        "└────────────────────────────────────────────┴───────────────────────────────┴───────────────────────────────┴─────────────────────┴───────┴─────┴──────────────┘")


def log_title(title):
    """Log a decorated section title with box-drawing characters."""
    log.info(" " * 20 + "┌" + "─" * (2 + len(title)) + "┐")
    log.info("╒" + "═" * 19 + "╡ " + title + " ╞" + "═" * 19 + "╕")
    log.info("│" + " " * 19 + "└" + "─" * (2 + len(title)) + "┘" + " " * 19 + "│")
