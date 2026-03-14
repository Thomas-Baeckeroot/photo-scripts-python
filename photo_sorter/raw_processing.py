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
from photo_sorter.constants import DEFAULT_DCP_PROFILE_DIR
from photo_sorter.lens_correction import apply_lens_correction
from photo_sorter.metadata import copy_exif_from_raw
from photo_sorter.dcp_profile import (apply_tone_curve, parse_dcp_tone_curve,
                                       apply_lookup_table, parse_dcp_lookup_table,
                                       apply_color_correction,
                                       compute_color_correction_matrix,
                                       parse_dcp_color_matrices,
                                       DcpProfileCache)
from photo_sorter.display import log_title

log = logging.getLogger(__name__)


# Mapping of colorspace names to rawpy constants
COLORSPACE_MAP = {
    'srgb': rawpy.ColorSpace.sRGB,           # Standard for web/screen display
    'prophoto': rawpy.ColorSpace.ProPhoto,    # Wide gamut (available but not used by default)
}


def develop_raw(raw_file_path, output_path, output_format="avif",
                output_bps=8, output_colorspace="srgb",
                dark_frame_path=None, tone_curve=None, lookup_table=None,
                color_correction_matrix=None,
                lens_correction_params=None):
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
        6. Optionally apply Phase 3 color correction (illuminant-interpolated ColorMatrix)
        7. Optionally apply DCP 3D LookTable (Phase 2) for fine-grained HSV corrections
        8. Optionally apply DCP tone curve (Phase 1) as contrast/color enhancement
        9. Downsample to requested bit depth (10-bit for AVIF, 16-bit for TIFF)
       10. Save as AVIF (imagecodecs for 10-bit, Pillow for 8-bit) or TIFF

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
        lookup_table:       Optional (90, 16, 16, 3) numpy array of HSV corrections
                            (from dcp_profile.parse_dcp_lookup_table). Applied BEFORE
                            tone curve via trilinear interpolation in HSV space.
        color_correction_matrix: Optional (3, 3) numpy array for Phase 3 illuminant
                            correction. Compensates for rawpy using D65-only ColorMatrix
                            when the scene illuminant is non-D65 (e.g. tungsten 3700K).
                            Applied in linear sRGB space. None at D65 (no correction needed).
        lens_correction_params: Optional dict with keys 'lens_model', 'focal_length',
                            'aperture' for lensfun-based lens correction (distortion,
                            vignetting, TCA). None to skip lens correction.

    Returns:
        True if the image was created successfully, False otherwise.
    """
    log.debug(f" ┊   └→ develop_raw: '{raw_file_path}'")
    log.debug(f" ┊                 → '{output_path}'")
    log.debug(f" ┊                 (format={output_format}, bps={output_bps}, "
              f"colorspace={output_colorspace}, "
              f"tone_curve={'yes' if tone_curve is not None else 'no'}, "
              f"ccm={'yes' if color_correction_matrix is not None else 'no'})")

    colorspace = COLORSPACE_MAP.get(output_colorspace)
    if colorspace is None:
        log.error(f" ┊      Unknown colorspace '{output_colorspace}'. "
                  f"Valid values: {list(COLORSPACE_MAP.keys())}")
        return False

    try:
        with rawpy.imread(raw_file_path) as raw:

            # Use 16-bit internal processing whenever any DCP correction is applied
            # (tone curve, LUT, or color correction matrix). This preserves maximum
            # precision through the correction pipeline before final downsampling.
            #
            # When a DCP tone curve is provided, the "DCP on BT.709" approach
            # produces brightness and contrast that closely match the camera's
            # in-body JPEG rendering ("Camera Standard").
            # Tested against Canon EOS R7 reference JPEGs: mean luminance within 3%.
            has_corrections = (tone_curve is not None or lookup_table is not None
                               or color_correction_matrix is not None)
            if has_corrections:
                internal_bps = 16
            else:
                internal_bps = output_bps

            gamma = (2.222, 4.5)        # BT.709
            no_auto_bright = False

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

        # Lens correction: distortion, vignetting, TCA (before color processing).
        # Applied here because: (a) distortion remap must happen before any color
        # corrections to avoid interpolating across color-corrected boundaries,
        # (b) vignetting operates on linear light which is recovered internally.
        if lens_correction_params is not None:
            rgb = apply_lens_correction(rgb, **lens_correction_params)
            log.debug(f" ┊                 After lens correction: {rgb.shape}, dtype={rgb.dtype}")

        # DCP processing order follows the DNG specification:
        #   1. LookTable (Phase 2) — fine-grained HSV corrections in LINEAR space
        #   2. ToneCurve (Phase 1) — contrast/color S-curve on BT.709 data
        #
        # The LookTable must be applied BEFORE the ToneCurve because:
        # - The DNG spec applies LUT on linear data, then ToneCurve converts to perceptual
        # - Applying LUT after ToneCurve causes over-brightening (corrections compound
        #   with the S-curve's contrast boost in doubly-nonlinear space)
        #
        # Since rawpy outputs BT.709-encoded data, we linearize (invert BT.709 gamma)
        # before the LUT, then re-encode BT.709 for the ToneCurve.

        # Phase 3 + Phase 2: Apply in linear sRGB space (before tone curve).
        # Phase 3 (color correction matrix) is applied inside apply_lookup_table()
        # when a LUT is present, or standalone via apply_color_correction() otherwise.
        # Both operations happen in linear sRGB space (BT.709 linearized).
        if lookup_table is not None:
            rgb = apply_lookup_table(rgb, lookup_table, linearize_bt709=True,
                                     color_correction_matrix=color_correction_matrix)
            log.debug(f" ┊                 After Phase 3+2 (linear): {rgb.shape}, dtype={rgb.dtype}")
        elif color_correction_matrix is not None:
            # Phase 3 only (no LUT): apply color correction standalone
            rgb = apply_color_correction(rgb, color_correction_matrix,
                                         linearize_bt709=True)
            log.debug(f" ┊                 After Phase 3 (standalone): {rgb.shape}, dtype={rgb.dtype}")

        # Phase 1: Apply DCP tone curve as contrast/color enhancement on BT.709 data.
        # The S-curve adds depth and saturation matching the camera manufacturer's
        # in-body JPEG rendering ("Camera Standard" profile).
        if tone_curve is not None:
            rgb = apply_tone_curve(rgb, tone_curve)
            log.debug(f" ┊                 After tone curve: {rgb.shape}, dtype={rgb.dtype}")

        # Downsample from internal 16-bit to requested output depth if needed
        if internal_bps > output_bps:
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


def create_tiff_16bit_from_raw(photo_folder, file_entry, app_config,
                               lookup_table=None, color_correction_matrix=None):
    """
    Create a 16-bit TIFF image from a RAW file for panorama/HDR processing.
    Uses develop_raw() with 16-bit sRGB settings. When DCP corrections are
    provided (Phase 2 LookTable + Phase 3 ColorMatrix), they are applied to
    produce device-independent colors with correct white balance.
    Phase 1 (ToneCurve) is intentionally omitted to keep data suitable for
    blending (linear response avoids seam artifacts in enblend).

    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): File entry containing required info (input file name, group, ...)
        app_config (AppConfig): Application configuration (reads dark_frame_path)
        lookup_table: Optional (90, 16, 16, 3) numpy array of HSV corrections (Phase 2)
        color_correction_matrix: Optional (3, 3) numpy array for Phase 3 illuminant
                                 correction. None for D65 (daylight) scenes.

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
        output_colorspace="srgb",       # sRGB: corrections pipeline is designed for sRGB
        dark_frame_path=app_config.dark_frame_path,
        lookup_table=lookup_table,
        color_correction_matrix=color_correction_matrix,
    )

    if success:
        file_entry.processed_relative_path = file_entry.group_id
        file_entry.processed_filename = tiff_filename

    return file_entry


def create_avif_from_raw_file(photo_folder, file_entry, app_config,
                             tone_curve=None, lookup_table=None,
                             color_correction_matrix=None,
                             lens_correction_params=None):
    """
    Create an AVIF image from a RAW file for individual images (not in groups).

    Without DCP: 8-bit sRGB AVIF via Pillow (BT.709 gamma only).
    With DCP tone curve alone: 10-bit sRGB AVIF via imagecodecs, with the camera
    manufacturer's contrast curve applied on top of BT.709.
    With DCP tone curve + lookup table: 10-bit sRGB AVIF with Phase 2 HSV corrections.
    With color correction matrix (Phase 3): illuminant-based ColorMatrix correction
    for non-D65 scene lighting (e.g. tungsten). Applied in linear sRGB before Phase 2.

    Args:
        photo_folder (str): Base photo folder path
        file_entry (ImageFile): File entry containing required info (input file name, paths, ...)
        app_config (AppConfig): Application configuration (reads dark_frame_path)
        tone_curve: Optional Nx2 numpy array of DCP tone curve control points (Phase 1)
        lookup_table: Optional (90, 16, 16, 3) numpy array of HSV corrections (Phase 2)
        color_correction_matrix: Optional (3, 3) numpy array for Phase 3 illuminant
                                 correction. None for D65 (daylight) scenes.
        lens_correction_params: Optional dict with 'lens_model', 'focal_length', 'aperture'
                                for lensfun-based lens correction. None to skip.

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
        lookup_table=lookup_table,
        color_correction_matrix=color_correction_matrix,
        lens_correction_params=lens_correction_params,
    )

    if success:
        file_entry.processed_relative_path = "."
        file_entry.processed_filename = avif_filename
        copy_exif_from_raw(raw_file_path, avif_output_path)

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

    def _build_lens_params(file_entry):
        """Build lens correction params dict from ImageFile fields, or None."""
        if (file_entry.lens_model and file_entry.focal_length
                and file_entry.aperture):
            return dict(lens_model=file_entry.lens_model,
                        focal_length=file_entry.focal_length,
                        aperture=file_entry.aperture)
        return None

    # DCP profile loading strategy:
    # - Explicit path in config → single profile for all images (existing behavior)
    # - Auto-detect (no explicit path) → per-image selection by EXIF PictureStyle
    # DCP corrections applied:
    #   AVIF (screen viewing): Phase 1 (ToneCurve) + Phase 2 (LUT) + Phase 3 (CCM)
    #   TIFF (panorama/HDR):   Phase 2 (LUT) + Phase 3 (CCM) — no ToneCurve to keep
    #                          data suitable for blending (linear response)

    # Mode 1: Explicit DCP profile path → single profile for all images
    if app_config.dcp_profile_path:
        tone_curve = parse_dcp_tone_curve(app_config.dcp_profile_path)
        lookup_table = None
        cm1, cm2, temp1, temp2 = None, None, None, None
        if tone_curve is None:
            log.warning(" ├→ DCP profile configured but tone curve could not be loaded. "
                        "Falling back to BT.709 gamma.")
        else:
            lookup_table = parse_dcp_lookup_table(app_config.dcp_profile_path)
            cm1, cm2, temp1, temp2 = parse_dcp_color_matrices(app_config.dcp_profile_path)

        for file_entry in files:
            if file_entry.raw_filename and not file_entry.processed_filename:
                # Phase 3: compute per-image color correction from scene temperature
                ccm = None
                if (cm1 is not None and cm2 is not None
                        and temp1 is not None and temp2 is not None
                        and file_entry.color_temperature is not None):
                    ccm = compute_color_correction_matrix(
                        cm1, cm2, temp1, temp2,
                        file_entry.color_temperature)

                if file_entry.group_id:
                    log.debug(f" ├→ Create tiff from '{file_entry.raw_filename}' "
                              f"for group '{file_entry.group_id}' "
                              f"(color_temp={file_entry.color_temperature}K, "
                              f"ccm={'yes' if ccm is not None else 'no'})")
                    file_entry = create_tiff_16bit_from_raw(
                        photo_folder, file_entry, app_config,
                        lookup_table=lookup_table,
                        color_correction_matrix=ccm)
                else:
                    lcp = _build_lens_params(file_entry)
                    log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' "
                              f"for individual image "
                              f"(color_temp={file_entry.color_temperature}K, "
                              f"ccm={'yes' if ccm is not None else 'no'}, "
                              f"lens={'yes' if lcp else 'no'})")
                    create_avif_from_raw_file(photo_folder, file_entry, app_config,
                                             tone_curve=tone_curve,
                                             lookup_table=lookup_table,
                                             color_correction_matrix=ccm,
                                             lens_correction_params=lcp)

    # Mode 2: Auto-detect → per-image DCP profile based on EXIF PictureStyle
    elif os.path.isdir(DEFAULT_DCP_PROFILE_DIR):
        dcp_cache = DcpProfileCache(DEFAULT_DCP_PROFILE_DIR)
        log.info(f" ├→ DCP auto-detect: per-image profile selection from '{DEFAULT_DCP_PROFILE_DIR}'")

        for file_entry in files:
            if file_entry.raw_filename and not file_entry.processed_filename:
                style = file_entry.picture_style or "Standard"
                tc, lut, cm1, cm2, temp1, temp2 = dcp_cache.get(style)

                # Phase 3: compute per-image color correction from scene temperature
                ccm = None
                if (cm1 is not None and cm2 is not None
                        and temp1 is not None and temp2 is not None
                        and file_entry.color_temperature is not None):
                    ccm = compute_color_correction_matrix(
                        cm1, cm2, temp1, temp2,
                        file_entry.color_temperature)

                if file_entry.group_id:
                    log.debug(f" ├→ Create tiff from '{file_entry.raw_filename}' "
                              f"for group '{file_entry.group_id}' "
                              f"(PictureStyle='{style}', "
                              f"color_temp={file_entry.color_temperature}K, "
                              f"ccm={'yes' if ccm is not None else 'no'})")
                    file_entry = create_tiff_16bit_from_raw(
                        photo_folder, file_entry, app_config,
                        lookup_table=lut,
                        color_correction_matrix=ccm)
                else:
                    lcp = _build_lens_params(file_entry)
                    log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' "
                              f"(PictureStyle='{style}', "
                              f"color_temp={file_entry.color_temperature}K, "
                              f"ccm={'yes' if ccm is not None else 'no'}, "
                              f"lens={'yes' if lcp else 'no'})")
                    create_avif_from_raw_file(photo_folder, file_entry, app_config,
                                             tone_curve=tc, lookup_table=lut,
                                             color_correction_matrix=ccm,
                                             lens_correction_params=lcp)

    # Mode 3: No DCP available → BT.709 only (no tone curve, no LUT)
    else:
        for file_entry in files:
            if file_entry.raw_filename and not file_entry.processed_filename:
                if file_entry.group_id:
                    log.debug(f" ├→ Create tiff from '{file_entry.raw_filename}' "
                              f"for group '{file_entry.group_id}'")
                    file_entry = create_tiff_16bit_from_raw(photo_folder, file_entry, app_config)
                else:
                    lcp = _build_lens_params(file_entry)
                    log.debug(f" ├→ Create avif from '{file_entry.raw_filename}' "
                              f"for individual image (no DCP, "
                              f"lens={'yes' if lcp else 'no'})")
                    create_avif_from_raw_file(photo_folder, file_entry, app_config,
                                             lens_correction_params=lcp)

    return files
