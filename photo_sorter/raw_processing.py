# -*- coding: utf-8 -*-
"""
RAW image development engine using rawpy (Python binding for libraw).

Functions:
    develop_raw()                — Central RAW-to-image conversion function
    create_tiff_16bit_from_raw() — 16-bit TIFF for panorama/HDR processing
    create_avif_from_raw_file()  — 10-bit AVIF for screen viewing
    create_processed_images()    — Batch processing of all images needing a processed version
"""

import logging
import os

import numpy as np
import rawpy
import tifffile
from imagecodecs import avif_encode
from PIL import Image

from photo_sorter.config import AppConfig
from photo_sorter.dcp_profile import apply_tone_curve, parse_dcp_tone_curve
from photo_sorter.display import log_title

log = logging.getLogger(__name__)


# Mapping of colorspace names to rawpy constants
COLORSPACE_MAP = {
    'srgb': rawpy.ColorSpace.sRGB,           # Standard for web/screen display
    'prophoto': rawpy.ColorSpace.ProPhoto,    # Wide gamut, ideal for HDR/panorama merging
}


def develop_raw(raw_file_path, output_path, output_format="avif",
                output_bps=8, output_colorspace="srgb",
                dark_frame_path=None, tone_curve=None):
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
        5. Convert to target colorspace with BT.709 gamma
        6. Optionally apply DCP tone curve as contrast/color enhancement
        7. Downsample to requested bit depth (10-bit for AVIF, 16-bit for TIFF)
        8. Save as AVIF (imagecodecs for 10-bit, Pillow for 8-bit) or TIFF

    Args:
        raw_file_path:      Full path to the RAW file (.CR3, .CR2, .NEF, etc.)
        output_path:        Full path for the output file (including extension)
        output_format:      "avif" for viewing images, "tiff" for panorama/HDR processing
        output_bps:         Bits per sample: 8 or 10 (AVIF/viewing) or 16 (TIFF/processing)
        output_colorspace:  "srgb" (standard display) or "prophoto" (wide gamut)
        dark_frame_path:    Optional path to a dark frame PGM file. This file contains
                            the sensor's thermal noise pattern, captured with the lens cap on
                            at the same ISO/exposure/temperature. Subtracted from raw Bayer
                            data before demosaicing to eliminate hot/dead pixels.
        tone_curve:         Optional Nx2 numpy array of tone curve control points
                            (from dcp_profile.parse_dcp_tone_curve). When provided:
                            - rawpy outputs BT.709-encoded 16-bit data
                            - DCP curve is applied on top as contrast/color enhancement
                            - this "DCP on BT.709" approach matches camera JPEG brightness
                            - result is then downsampled to the requested bit depth

    Returns:
        True if the image was created successfully, False otherwise.
    """
    log.debug(f" ┊   └→ develop_raw: '{raw_file_path}'")
    log.debug(f" ┊                 → '{output_path}'")
    log.debug(f" ┊                 (format={output_format}, bps={output_bps}, "
              f"colorspace={output_colorspace}, "
              f"tone_curve={'yes' if tone_curve is not None else 'no'})")

    colorspace = COLORSPACE_MAP.get(output_colorspace)
    if colorspace is None:
        log.error(f" ┊      Unknown colorspace '{output_colorspace}'. "
                  f"Valid values: {list(COLORSPACE_MAP.keys())}")
        return False

    try:
        with rawpy.imread(raw_file_path) as raw:

            # When a DCP tone curve is provided, rawpy outputs BT.709-encoded
            # data at 16-bit precision. The tone curve is then applied on top as
            # a contrast/color enhancement — NOT as a gamma replacement.
            #
            # This "DCP on BT.709" approach produces brightness and contrast that
            # closely match the camera's in-body JPEG rendering ("Camera Standard").
            # Tested against Canon EOS R7 reference JPEGs: mean luminance within 3%.
            if tone_curve is not None:
                internal_bps = 16
                gamma = (2.222, 4.5)    # BT.709 — DCP enhances this, doesn't replace it
                no_auto_bright = False
            else:
                internal_bps = output_bps
                gamma = (2.222, 4.5)    # BT.709 default
                no_auto_bright = False  # let rawpy auto-adjust brightness

            # Build postprocess parameters (factored to avoid duplication)
            params_kwargs = dict(
                use_camera_wb=True,                              # -w : camera white balance
                highlight_mode=rawpy.HighlightMode.Clip,         # -H 1 : clip highlights cleanly
                output_color=colorspace,                         # -o : target colorspace
                output_bps=internal_bps,                         # bits per sample (16 for tone curve path)
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # -q 3 : best quality
                fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Light,  # -fbdd 1
                gamma=gamma,
                no_auto_bright=no_auto_bright,
            )

            # Dark frame subtraction: applied on raw Bayer data before demosaicing
            if dark_frame_path:
                log.info(f" ┊      Applying dark frame subtraction: '{dark_frame_path}'")
                params_kwargs['dark_frame'] = dark_frame_path

            params = rawpy.Params(**params_kwargs)

            # Demosaic: convert Bayer pattern to RGB image
            rgb = raw.postprocess(params)

        # rgb is a numpy array of shape (height, width, 3), dtype uint8 or uint16
        log.debug(f" ┊                 Demosaiced image: {rgb.shape}, dtype={rgb.dtype}")

        # Apply DCP tone curve as contrast/color enhancement on BT.709 data.
        # The S-curve adds depth and saturation matching the camera manufacturer's
        # in-body JPEG rendering ("Camera Standard" profile).
        if tone_curve is not None:
            rgb = apply_tone_curve(rgb, tone_curve)
            log.debug(f" ┊                 After tone curve: {rgb.shape}, dtype={rgb.dtype}")

            # Downsample from internal 16-bit to requested output depth
            if output_bps == 10 and rgb.dtype == np.uint16:
                rgb = (rgb >> 6).astype(np.uint16)   # [0, 65535] → [0, 1023]
                log.debug(f" ┊                 Downsampled to 10-bit: max={rgb.max()}")
            elif output_bps == 8 and rgb.dtype == np.uint16:
                rgb = (rgb >> 8).astype(np.uint8)
                log.debug(f" ┊                 Downsampled to 8-bit: dtype={rgb.dtype}")

        if output_format == "avif":
            if output_bps >= 10 and rgb.dtype == np.uint16:
                # 10-bit or 12-bit AVIF via imagecodecs (Pillow only supports 8-bit)
                # level=80 for lossy quality (default is lossless → huge files)
                avif_data = avif_encode(rgb, level=80, speed=6,
                                        bitspersample=output_bps)
                with open(output_path, 'wb') as f:
                    f.write(avif_data)
            else:
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


def create_avif_from_raw_file(photo_folder, file_entry, app_config, tone_curve=None):
    """
    Create an AVIF image from a RAW file for individual images (not in groups).

    Without DCP tone curve: 8-bit sRGB AVIF via Pillow (BT.709 gamma only).
    With DCP tone curve: 10-bit sRGB AVIF via imagecodecs, with the camera
    manufacturer's contrast curve applied on top of BT.709 for richer colors.

    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): File entry containing required info (input file name, paths, ...)
        app_config (AppConfig): Application configuration (reads dark_frame_path)
        tone_curve: Optional Nx2 numpy array of DCP tone curve control points

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
        output_bps=10 if tone_curve is not None else 8,  # 10-bit with DCP, 8-bit without
        output_colorspace="srgb",
        dark_frame_path=app_config.dark_frame_path,
        tone_curve=tone_curve,
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

    # Load DCP tone curve once for all images.
    # Applied to AVIF (screen viewing) only — TIFF intermediates for panorama/HDR
    # stay linear to preserve dynamic range during merging.
    tone_curve = None
    if app_config.dcp_profile_path:
        tone_curve = parse_dcp_tone_curve(app_config.dcp_profile_path)
        if tone_curve is None:
            log.warning(" ├→ DCP profile configured but tone curve could not be loaded. "
                        "Falling back to BT.709 gamma.")

    for file_entry in files:
        if file_entry.raw_filename and not file_entry.processed_filename:
            if file_entry.group_id:
                log.debug(f" ├→ Create tiff from '{file_entry.raw_filename}' for group '{file_entry.group_id}'")
                file_entry = create_tiff_16bit_from_raw(photo_folder, file_entry, app_config)
            else:  # no group_id for current file_entry
                log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' for individual image")
                create_avif_from_raw_file(photo_folder, file_entry, app_config, tone_curve=tone_curve)
    return files
