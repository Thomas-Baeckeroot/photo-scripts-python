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
from photo_sorter.constants import DCP_PROFILE_DIR_CANDIDATES
from photo_sorter.lens_correction import apply_lens_correction
from photo_sorter.metadata import copy_exif_from_raw
from photo_sorter.dcp_profile import (apply_tone_curve, parse_dcp_tone_curve,
                                       apply_lookup_table, parse_dcp_lookup_table,
                                       apply_color_correction,
                                       compute_color_correction_matrix,
                                       parse_dcp_color_matrices,
                                       _bt709_encode,
                                       DcpProfileCache)
from photo_sorter.display import log_title

log = logging.getLogger(__name__)


# Mapping of colorspace names to rawpy constants
COLORSPACE_MAP = {
    'srgb': rawpy.ColorSpace.sRGB,           # Standard for web/screen display
    'prophoto': rawpy.ColorSpace.ProPhoto,    # Wide gamut (available but not used by default)
}


def _estimate_auto_bright_thr(raw):
    """
    Estimate optimal auto_bright_thr from raw Bayer data.

    rawpy's auto_bright scales the image so that auto_bright_thr fraction of
    pixels are clipped to white. The default (0.01 = 1%) is conservative: for
    high-contrast scenes (dark interior with bright window), the few bright
    window pixels saturate to white early, and auto_bright doesn't boost the
    rest of the image. This leaves 95%+ of pixels severely underexposed.

    Canon's Auto Lighting Optimizer (ALO) solves this by detecting high-contrast
    scenes and applying aggressive shadow lifting. Since ALO is not stored in
    DCP profiles, we compensate by increasing auto_bright_thr for high-contrast
    scenes, allowing more highlight clipping and brighter overall exposure.

    The threshold is computed per-image from the fraction of "outlier bright"
    pixels (>10x the median). This fraction directly measures the bright tail
    size (e.g., window in a dark room), and thr is set to clip most of it.

    Args:
        raw: rawpy.RawPy object (after imread, before postprocess)

    Returns:
        float: recommended auto_bright_thr (0.01 to 0.10)
    """
    visible = raw.raw_image_visible.astype(np.float32)
    black = np.mean(raw.black_level_per_channel)
    white = raw.white_level
    visible = np.clip((visible - black) / (white - black), 0, 1)

    p50 = np.median(visible)

    if p50 < 0.001:
        log.debug(f" ┊                 Scene analysis: p50={p50:.4f} "
                  f"→ extremely dark, auto_bright_thr=0.10")
        return 0.10

    # Compute fraction of pixels that are "outlier bright" (>10x median).
    # For indoor high-contrast scenes, this captures the bright window/sky area.
    # Empirical data:
    #   Outdoor scenes: outlier_frac ≈ 1.5-2% → thr stays at 0.01
    #   Indoor with window: outlier_frac ≈ 4.5-5% → thr ≈ 0.035-0.045
    outlier_frac = (visible > p50 * 10).mean()

    if outlier_frac > 0.02:
        # High-contrast scene: set thr to clip most of the bright outliers.
        # Factor 0.85 is empirically tuned: clipping 85% of the outlier tail
        # provides the right balance between shadow lifting and highlight
        # preservation after the DCP tone curve + BT.709 pipeline.
        thr = min(outlier_frac * 0.85, 0.10)
    else:
        thr = 0.01  # Normal/well-exposed scene

    log.debug(f" ┊                 Scene analysis: p50={p50:.4f}, "
              f"outlier_frac={outlier_frac:.4f} ({outlier_frac*100:.1f}%) "
              f"→ auto_bright_thr={thr:.4f}")
    return thr


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

    Two processing pipelines are available, selected automatically:

    **Linear pipeline** (when DCP corrections are available):
        1. Load RAW Bayer data (rawpy.imread)
        2. Optionally subtract dark frame (hot/dead pixel correction)
        3. Demosaic with AHD algorithm, output LINEAR 16-bit sRGB (gamma=(1,1))
        4. Optionally apply lens correction (distortion, vignetting, TCA)
        5. Phase 3: illuminant-interpolated ColorMatrix correction (linear sRGB)
        6. Phase 2: DCP 3D LookTable in ProPhoto HSV (linear domain)
        7. Phase 1: DCP ProfileToneCurve on LINEAR data (per DNG specification)
        8. BT.709 gamma encoding (explicit, after tone curve)
        9. Downsample to 10-bit and save as AVIF
       For TIFF output (panorama/HDR): steps 7-8 are skipped, data stays linear
       for correct blending behavior in enblend.

    **BT.709 pipeline** (no DCP corrections):
        1-4 same as above, but rawpy outputs with BT.709 gamma encoding
        5. Save directly as AVIF or TIFF

    The linear pipeline follows the DNG specification processing order: the
    ProfileToneCurve operates on linear scene-referred data, mapping to
    output-referred values. Applying it to BT.709-encoded data (as was done
    previously) causes double-compression of highlights and reduced saturation,
    especially visible in high-contrast scenes.

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
                            (from dcp_profile.parse_dcp_tone_curve). When provided,
                            rawpy outputs linear data and the curve is applied per the
                            DNG specification (on linear scene-referred values). BT.709
                            gamma encoding is then applied explicitly.
        lookup_table:       Optional (90, 16, 16, 3) numpy array of HSV corrections
                            (from dcp_profile.parse_dcp_lookup_table). Applied BEFORE
                            tone curve via trilinear interpolation in ProPhoto HSV space.
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

            # When DCP corrections are available, use LINEAR output from rawpy.
            # This is critical for correct tone curve application per the DNG
            # specification: the ProfileToneCurve operates on linear scene-referred
            # data, not on gamma-encoded data. Applying it to BT.709 data causes
            # double-compression of highlights and reduced saturation.
            # Linear output also benefits TIFF panorama/HDR: enblend produces
            # better seam blending on linear data (no gamma-space artifacts).
            has_corrections = (tone_curve is not None or lookup_table is not None
                               or color_correction_matrix is not None)
            if has_corrections:
                internal_bps = 16
                gamma = (1, 1)          # Linear: corrections operate in linear space
                use_linear = True
            else:
                internal_bps = output_bps
                gamma = (2.222, 4.5)    # BT.709: no corrections, encode directly
                use_linear = False

            # Scene-adaptive auto_bright_thr: analyze the raw Bayer data to
            # determine the scene's dynamic range and calculate the optimal
            # highlight clipping threshold for auto-brightness normalization.
            auto_thr = _estimate_auto_bright_thr(raw)

            # Build postprocess parameters
            params_kwargs = dict(
                use_camera_wb=True,                              # -w : camera white balance
                highlight_mode=rawpy.HighlightMode.Clip,         # -H 0 : clip highlights.
                                                                 # Blend (-H 2) causes ~20%
                                                                 # darkening on interior scenes
                                                                 # via auto_bright interaction
                output_color=colorspace,                         # -o : target colorspace
                output_bps=internal_bps,                         # bits per sample
                demosaic_algorithm=rawpy.DemosaicAlgorithm.AHD,  # -q 3 : best quality
                fbdd_noise_reduction=rawpy.FBDDNoiseReductionMode.Light,  # -fbdd 1
                gamma=gamma,
                no_auto_bright=False,                            # auto-bright for proper
                                                                 # histogram normalization
                auto_bright_thr=auto_thr,                        # scene-adaptive threshold
            )

            # Dark frame subtraction: applied on raw Bayer data before demosaicing
            if dark_frame_path:
                log.info(f" ┊      Applying dark frame subtraction: '{dark_frame_path}'")
                params_kwargs['dark_frame'] = dark_frame_path

            params = rawpy.Params(**params_kwargs)

            # Demosaic: convert Bayer pattern to RGB image
            rgb = raw.postprocess(params)

        # rgb is a numpy array of shape (height, width, 3), dtype uint8 or uint16
        log.debug(f" ┊                 Demosaiced image: {rgb.shape}, dtype={rgb.dtype}, "
                  f"pipeline={'linear' if use_linear else 'bt709'}")

        # Lens correction: distortion, vignetting, TCA (before color processing).
        # Applied here because: (a) distortion remap must happen before any color
        # corrections to avoid interpolating across color-corrected boundaries,
        # (b) vignetting correction needs linear light (handled internally).
        if lens_correction_params is not None:
            rgb = apply_lens_correction(rgb, **lens_correction_params,
                                        input_linear=use_linear)
            log.debug(f" ┊                 After lens correction: {rgb.shape}, dtype={rgb.dtype}")

        # DCP processing order follows the DNG specification:
        #   Phase 3: ColorMatrix correction (linear sRGB)
        #   Phase 2: LookTable HSV corrections (linear sRGB → ProPhoto → HSV → back)
        #   Phase 1: ProfileToneCurve (linear → tone-mapped, per DNG spec)
        #   BT.709:  Gamma encoding for display (explicit, after tone curve)
        #
        # In the linear pipeline, all corrections operate natively in linear space
        # (no BT.709 linearize/re-encode round-trips needed).

        # Phase 3 + Phase 2: Apply in linear sRGB space (before tone curve).
        # Phase 3 (color correction matrix) is applied inside apply_lookup_table()
        # when a LUT is present, or standalone via apply_color_correction() otherwise.
        if lookup_table is not None:
            rgb = apply_lookup_table(rgb, lookup_table, input_linear=use_linear,
                                     color_correction_matrix=color_correction_matrix)
            log.debug(f" ┊                 After Phase 3+2: {rgb.shape}, dtype={rgb.dtype}")
        elif color_correction_matrix is not None:
            # Phase 3 only (no LUT): apply color correction standalone
            rgb = apply_color_correction(rgb, color_correction_matrix,
                                         input_linear=use_linear)
            log.debug(f" ┊                 After Phase 3 (standalone): {rgb.shape}, dtype={rgb.dtype}")

        # Phase 1: Apply DCP ProfileToneCurve on LINEAR data (per DNG spec).
        # The curve maps linear scene-referred values to output-referred values,
        # providing the camera manufacturer's contrast and color rendering.
        if tone_curve is not None:
            rgb = apply_tone_curve(rgb, tone_curve)
            log.debug(f" ┊                 After tone curve: {rgb.shape}, dtype={rgb.dtype}")

        # BT.709 gamma encoding: required for display output (AVIF).
        # In the linear pipeline, the tone curve provides tone mapping but not
        # gamma encoding. We add BT.709 encoding explicitly for AVIF output.
        # For TIFF: data stays linear (better for panorama/HDR blending in enblend).
        if use_linear and output_format == "avif":
            rgb_float = rgb.astype(np.float64) / 65535.0
            rgb_float = _bt709_encode(rgb_float)
            rgb = np.clip(rgb_float * 65535.0, 0, 65535).astype(np.uint16)
            log.debug(f" ┊                 After BT.709 encoding: max={rgb.max()}")

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
                # 10-bit AVIF via imagecodecs (Pillow only supports 8-bit).
                # quality=92 for high-fidelity photographic output.
                avif_data = avif_encode(rgb, level=92, speed=6,
                                        bitspersample=output_bps)
                with open(output_path, 'wb') as f:
                    f.write(avif_data)
            else:
                # 8-bit RGB → Pillow handles this fine
                img = Image.fromarray(rgb)
                img.save(output_path, 'AVIF', quality=92, speed=6)
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
    # Search candidate directories for DCP profiles
    elif (dcp_dir := next(
            (os.path.expanduser(d) for d in DCP_PROFILE_DIR_CANDIDATES
             if os.path.isdir(os.path.expanduser(d))),
            None)):
        dcp_cache = DcpProfileCache(dcp_dir)
        log.info(f" ├→ DCP auto-detect: per-image profile selection from '{dcp_dir}'")

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
        log.warning(" ├→ No DCP profile directory found. AVIF output will use BT.709 gamma "
                    "only (no tone curve, no color corrections). Images will appear darker "
                    "and less saturated than camera JPEGs.")
        log.warning(" ├→ To fix: set 'dcp_profile' in [Processing] section of "
                    "~/.config/sort_photo.conf, or place DCP profiles in one of:")
        for candidate in DCP_PROFILE_DIR_CANDIDATES:
            log.warning(f" ├→   {candidate}")
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
