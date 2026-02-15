#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
CLI entry point for automatic panorama creation from a folder of images.

TODO: Rewrite using hsi (Hugin Python bindings) for native panorama automation.
      Target platform: Linux x86_64 with hugin-tools package (provides hsi module).
      See previous implementation for the Hugin CLI pipeline:
      cpfind → autooptimiser → pano_modify → cpclean → nona → enblend

Usage:
    ./create_panorama.py /path/to/panorama/folder/
"""

import logging
import sys

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s\t%(levelname)s\t%(filename)s:%(lineno)d\t%(message)s')
log = logging.getLogger("create_panorama.py")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        log.critical(f"Usage: {sys.argv[0]} /path/to/panorama/folder/")
        sys.exit(1)

    pano_folder = sys.argv[1]
    log.warning("create_panorama.py is a placeholder — panorama automation not yet implemented.")
    log.warning(f"Target folder: {pano_folder}")
    log.info("This script will be rewritten using hsi (Hugin Python bindings) for Linux x86_64.")
    sys.exit(0)
