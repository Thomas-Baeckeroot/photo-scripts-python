# -*- coding: utf-8 -*-
"""
RAW image development engine using rawpy (Python binding for libraw).

Functions:
    develop_raw()                — Central RAW-to-image conversion function
    create_tiff_16bit_from_raw() — 16-bit TIFF for panorama/HDR processing
    create_avif_from_raw_file()  — 8-bit AVIF for screen viewing
    create_processed_images()    — Batch processing of all images needing a processed version
"""

import logging
import os

import numpy as np
import rawpy
import tifffile
from PIL import Image

from photo_sorter.config import AppConfig
from photo_sorter.display import log_title

log = logging.getLogger(__name__)


# Mapping of colorspace names to rawpy constants
COLORSPACE_MAP = {
    'srgb': rawpy.ColorSpace.sRGB,           # Standard for web/screen display
    'prophoto': rawpy.ColorSpace.ProPhoto,    # Wide gamut, ideal for HDR/panorama merging
}


def develop_raw(raw_file_path, output_path, output_format="avif",
                output_bps=8, output_colorspace="srgb",
                dark_frame_path=None):
    """
    Develop a RAW file using rawpy (Python binding for libraw).

    This is the central function for all RAW-to-image conversions. It replaces
    the previous subprocess calls to dcraw_emu with a native Python approach,
    giving direct control over demosaicing parameters.

    The processing pipeline is:
        1. Load RAW Bayer data (rawpy.imread)
        2. Optionally subtract dark frame (hot/dead pixel correction)
        3. Demosaic with AHD algorithm (same as dcraw -q 3)
        4. Apply camera white balance
        5. Convert to target colorspace
        6. Output at requested bit depth
        7. Save via PIL in the requested format

    Args:
        raw_file_path:      Full path to the RAW file (.CR3, .CR2, .NEF, etc.)
        output_path:        Full path for the output file (including extension)
        output_format:      "avif" for 8-bit viewing images,
                            "tiff" for 16-bit panorama/HDR processing
        output_bps:         Bits per sample: 8 (AVIF/viewing) or 16 (TIFF/processing)
        output_colorspace:  "srgb" (standard display) or "prophoto" (wide gamut)
        dark_frame_path:    Optional path to a dark frame PGM file. This file contains
                            the sensor's thermal noise pattern, captured with the lens cap on
                            at the same ISO/exposure/temperature. Subtracted from raw Bayer
                            data before demosaicing to eliminate hot/dead pixels.

    Returns:
        True if the image was created successfully, False otherwise.
    """
    log.debug(f" ┊   └→ develop_raw: '{raw_file_path}'")
    log.debug(f" ┊                 → '{output_path}'")
    log.debug(f" ┊                 (format={output_format}, bps={output_bps}, colorspace={output_colorspace})")

    colorspace = COLORSPACE_MAP.get(output_colorspace)
    if colorspace is None:
        log.error(f" ┊      Unknown colorspace '{output_colorspace}'. "
                  f"Valid values: {list(COLORSPACE_MAP.keys())}")
        return False

    try:
        with rawpy.imread(raw_file_path) as raw:

            # Build postprocess parameters
            #
            # Gamma: we use the default BT.709 curve (2.222, 4.5).
            # RAW sensor data is linear (photon count), but human vision is non-linear.
            # The gamma curve redistributes values so the output looks natural on screen.
            # BT.709 was tested as the most faithful to the original CR3 rendering.
            # Note: sRGB (2.4, 12.92) is slightly brighter; may be revisited if DCP
            # camera profiles are applied in the future (Canon EOS R7 profiles exist
            # at /Library/Application Support/Adobe/CameraRaw/CameraProfiles/).
            params = rawpy.Params(
                use_camera_wb=True,                     # -w : use the white balance recorded by the camera
                highlight_mode=rawpy.HighlightMode.Clip,  # -H 1 : clip highlights cleanly (no color shift)
                output_color=colorspace,                # -o : target colorspace
                output_bps=output_bps,                  # -4/-6 : bits per sample in output
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # -q 3 : Adaptive Homogeneity-Directed
                # no_auto_bright=True,                    # disable auto-brightness (preserve original exposure)
                fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Light,  # -fbdd 1 : light noise reduction
                # gamma defaults to BT.709 (2.222, 4.5) — most faithful to CR3 original
            )

            # Dark frame subtraction: applied on raw Bayer data before demosaicing
            if dark_frame_path:
                log.info(f" ┊      Applying dark frame subtraction: '{dark_frame_path}'")
                params = rawpy.Params(
                    use_camera_wb=True,
                    highlight_mode=rawpy.HighlightMode.Clip,
                    output_color=colorspace,
                    output_bps=output_bps,
                    demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,
                    # no_auto_bright=True,
                    fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Light,
                    # gamma defaults to BT.709 (2.222, 4.5)
                    dark_frame=dark_frame_path,         # -K : subtract dark frame before demosaicing
                )

            # Demosaic: convert Bayer pattern to RGB image
            rgb = raw.postprocess(params)

        # rgb is a numpy array of shape (height, width, 3), dtype uint8 or uint16
        log.debug(f" ┊                 Demosaiced image: {rgb.shape}, dtype={rgb.dtype}")

        if output_format == "avif":
            # 8-bit RGB → Pillow handles this fine
            img = Image.fromarray(rgb)
            img.save(output_path, 'AVIF', quality=80, speed=6)
        elif output_format == "tiff":
            # 16-bit RGB → Pillow cannot save uint16 RGB, use tifffile instead
            tifffile.imwrite(output_path, rgb, photometric='rgb')
        else:
            log.error(f" ┊      Unknown output format '{output_format}'")
            return False

        log.info(f" ┊                 ✓ Created {output_format.upper()}")
        return True

    except rawpy.LibRawError as e:
        log.error(f" ┊    → libraw error processing '{raw_file_path}': {e}")
        return False

    except Exception as e:
        log.error(f" ┊    → Unexpected error developing '{raw_file_path}': {e}")
        return False


def create_tiff_16bit_from_raw(photo_folder, file_entry, app_config):
    """
    Create a 16-bit TIFF image from a RAW file for panorama/HDR processing.
    Uses develop_raw() with 16-bit ProPhoto RGB settings to preserve maximum
    color gamut and dynamic range for subsequent merging operations.

    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): File entry containing required info (input file name, group, ...)
        app_config (AppConfig): Application configuration (reads dark_frame_path)

    Returns:
        ImageFile: Updated file entry with processed file info
    """
    raw_file_path = os.path.join(photo_folder, file_entry.raw_relative_path, file_entry.raw_filename)
    basename = os.path.splitext(file_entry.raw_filename)[0]

    # Create group folder (panorama/HDR images go into their group subfolder)
    group_folder = os.path.join(photo_folder, file_entry.group_id)
    if not os.path.exists(group_folder):
        os.makedirs(group_folder)
        log.debug(f" ┊      Created group folder: {group_folder}")

    tiff_filename = f"{basename}.tiff"
    tiff_output_path = os.path.join(group_folder, tiff_filename)

    success = develop_raw(
        raw_file_path,
        tiff_output_path,
        output_format="tiff",
        output_bps=16,                  # 16-bit for maximum dynamic range
        output_colorspace="prophoto",   # ProPhoto RGB: widest gamut, ideal for merging
        dark_frame_path=app_config.dark_frame_path,
    )

    if success:
        file_entry.processed_relative_path = file_entry.group_id
        file_entry.processed_filename = tiff_filename

    return file_entry


def create_avif_from_raw_file(photo_folder, file_entry, app_config):
    """
    Create an AVIF image from a RAW file for individual images (not in groups).
    Uses develop_raw() with 8-bit sRGB settings optimized for screen viewing.

    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): File entry containing required info (input file name, paths, ...)
        app_config (AppConfig): Application configuration (reads dark_frame_path)

    Returns:
        ImageFile: Updated file entry with processed file info
    """
    raw_file_path = os.path.join(photo_folder, file_entry.raw_relative_path, file_entry.raw_filename)
    basename = os.path.splitext(file_entry.raw_filename)[0]
    avif_filename = f"{basename}.avif"
    avif_output_path = os.path.join(photo_folder, avif_filename)

    success = develop_raw(
        raw_file_path,
        avif_output_path,
        output_format="avif",
        output_bps=8,
        output_colorspace="srgb",
        dark_frame_path=app_config.dark_frame_path,
    )

    if success:
        file_entry.processed_relative_path = "."
        file_entry.processed_filename = avif_filename

    return file_entry


def create_processed_images(photo_folder, files, app_config):
    """
    Batch-process all images that have a RAW file but no processed version.
    Groups get 16-bit TIFF; individual images get 8-bit AVIF.

    Args:
        photo_folder (str): Base photo folder path
        files (list[ImageFile]): List of image entries
        app_config (AppConfig): Application configuration

    Returns:
        list[ImageFile]: Updated list
    """
    log.debug("START .create_processed_images()")
    for file_entry in files:
        if file_entry.raw_filename and not file_entry.processed_filename:
            if file_entry.group_id:
                log.debug(f" ├→ Create png from '{file_entry.raw_filename}' for group '{file_entry.group_id}'")
                file_entry = create_tiff_16bit_from_raw(photo_folder, file_entry, app_config)
            else:  # no group_id for current file_entry
                log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' for individual image")
                create_avif_from_raw_file(photo_folder, file_entry, app_config)
    return files
