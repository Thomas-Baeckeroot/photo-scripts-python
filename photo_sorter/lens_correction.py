# -*- coding: utf-8 -*-
"""
Lens distortion, vignetting, and TCA correction using lensfunpy.

lensfun is an open-source library with a community-sourced database of lens
calibration profiles. Given a camera+lens+focal length+aperture, it computes
coordinate remapping tables (for distortion/TCA) and per-pixel multipliers
(for vignetting) that correct optical defects.

Three types of corrections are applied when available:
    1. Vignetting — radial brightness fall-off at the corners.
       Applied on LINEAR data (before gamma), because the light fall-off
       is a physical phenomenon proportional to light intensity, not to
       the perceptual (gamma-encoded) value.
    2. Distortion — barrel/pincushion geometric warping.
    3. TCA (Transverse Chromatic Aberration) — per-channel lateral shift
       causing colored fringes at high-contrast edges.

Distortion and TCA are handled together via subpixel remapping: lensfun
produces per-channel coordinate maps, and cv2.remap() resamples each R/G/B
channel independently with Lanczos interpolation.

Functions:
    apply_lens_correction() — Main entry point called from develop_raw()
"""

import logging

import cv2
import lensfunpy
import numpy as np

from photo_sorter.dcp_profile import _bt709_linearize, _bt709_encode

log = logging.getLogger(__name__)

# Camera name as it appears in the lensfun database.
# lensfun stores Canon cameras with a "Canon" prefix in the model name.
CAMERA_MAKER = "Canon"
CAMERA_MODEL = "Canon EOS R7"

# Lazy-loaded singleton: the lensfun database is ~2MB of XML files parsed once.
_db = None


def _get_database():
    """Return the lensfun Database singleton (lazy-loaded on first call)."""
    global _db
    if _db is None:
        _db = lensfunpy.Database()
    return _db


def _find_camera_and_lens(lens_model):
    """
    Look up the camera and lens in the lensfun database.

    The camera is fixed (Canon EOS R7). The lens is searched by the EXIF
    LensModel string — lensfun performs fuzzy matching against its database.

    Args:
        lens_model: EXIF LensModel string (e.g. "RF-S18-45mm F4.5-6.3 IS STM")

    Returns:
        (camera, lens) tuple, or (None, None) if not found.
    """
    db = _get_database()

    cameras = db.find_cameras(CAMERA_MAKER, CAMERA_MODEL)
    if not cameras:
        # Fallback: search without the "Canon " prefix in model
        cameras = [c for c in db.cameras
                   if "EOS R7" in c.model and "Canon" in c.maker]
    if not cameras:
        log.warning(f" ┊      Camera '{CAMERA_MAKER} {CAMERA_MODEL}' "
                    f"not found in lensfun database")
        return None, None

    camera = cameras[0]

    # Split the EXIF lens model into maker + model for better matching.
    # Canon lenses start with "RF" or "EF", third-party lenses have their
    # maker name in the EXIF string (e.g. "TAMRON 18-200mm ...").
    lens_maker = ""
    if lens_model:
        first_word = lens_model.split()[0].upper()
        if first_word in ("TAMRON", "SIGMA", "TOKINA", "SAMYANG", "ZEISS"):
            lens_maker = first_word.capitalize()

    lenses = db.find_lenses(camera, lens_maker, lens_model)
    if not lenses:
        # Try without maker for broader search
        lenses = db.find_lenses(camera, "", lens_model)
    if not lenses:
        return camera, None

    # find_lenses() returns results sorted by match score (best first)
    return camera, lenses[0]


def apply_lens_correction(rgb, lens_model, focal_length, aperture,
                          distance=10.0, input_linear=False):
    """
    Apply lens distortion, vignetting, and TCA correction to an RGB image.

    The full pipeline is:
        1. Look up the lens in lensfun's database
        2. If BT.709 input: linearize gamma (vignetting must be corrected in
           linear light). If already linear: skip this step.
        3. Apply vignetting correction (per-pixel brightness multiplier)
        4. Apply geometric distortion + TCA (per-channel coordinate remap)
        5. If BT.709 input: re-encode BT.709 gamma
        6. Return the corrected image in the same format as the input

    If the lens is not found in the database, the image is returned unchanged
    with a warning. Missing individual correction types (e.g. no vignetting
    data but distortion available) are skipped gracefully.

    Args:
        rgb:            numpy array (H, W, 3), dtype uint16.
        lens_model:     EXIF LensModel string (e.g. "RF-S18-45mm F4.5-6.3 IS STM")
        focal_length:   Focal length in mm (from EXIF)
        aperture:       F-number (from EXIF)
        distance:       Subject distance in meters (default 10.0 — lensfun uses
                        this for distortion models that vary with focus distance;
                        10m is a reasonable default for general photography).
        input_linear:   If True, input is already linear sRGB (from rawpy with
                        gamma=(1,1)). If False (default), input is BT.709-encoded
                        and will be linearized/re-encoded internally.

    Returns:
        numpy array (H, W, 3), same dtype as input, with corrections applied.
    """
    if not lens_model or not focal_length or not aperture:
        log.debug(" ┊      Lens correction skipped: missing lens metadata")
        return rgb

    camera, lens = _find_camera_and_lens(lens_model)
    if lens is None:
        if camera is not None:
            log.info(f" ┊      Lens '{lens_model}' not found in lensfun database "
                     f"— skipping lens correction")
        return rgb

    log.info(f" ┊      Lens correction: '{lens.model}' "
             f"@ {focal_length}mm f/{aperture}")

    height, width = rgb.shape[:2]
    original_dtype = rgb.dtype

    # Determine the max pixel value for the image dtype
    if original_dtype == np.uint16:
        max_val = 65535.0
    elif original_dtype == np.uint8:
        max_val = 255.0
    else:
        log.warning(f" ┊      Unsupported dtype {original_dtype} for lens correction")
        return rgb

    # Initialize lensfun modifier with the lens parameters.
    # scale=0.0 means auto-scale: lensfun computes the largest inscribed
    # rectangle without black borders after distortion correction, matching
    # the Canon in-camera behavior (slight crop at the edges).
    mod = lensfunpy.Modifier(lens, camera.crop_factor, width, height)
    mod.initialize(focal_length, aperture, distance, scale=0.0,
                   pixel_format=np.float64)

    # --- Step 1: Get linear RGB for vignetting correction ---
    # Vignetting is a light fall-off (physical, proportional to intensity).
    # Correcting it in gamma-encoded space would over-correct shadows and
    # under-correct highlights.
    rgb_float = rgb.astype(np.float64) / max_val
    if input_linear:
        rgb_linear = rgb_float
    else:
        rgb_linear = _bt709_linearize(rgb_float)
    del rgb_float  # free memory

    # --- Step 2: Vignetting correction (linear space) ---
    # apply_color_modification() works on float64 arrays in [0, 1] range.
    # It multiplies each pixel by a radial correction factor, in-place.
    did_vignetting = mod.apply_color_modification(rgb_linear)
    if did_vignetting:
        rgb_linear = np.clip(rgb_linear, 0.0, 1.0)
        log.debug(" ┊      Applied vignetting correction")
    else:
        log.debug(" ┊      No vignetting data for this lens")

    # --- Step 3: Geometric distortion + TCA correction ---
    # apply_subpixel_geometry_distortion() returns per-channel coordinate
    # maps of shape (H, W, 3, 2) — for each pixel, the (x, y) source
    # coordinate in the original image, separately for R, G, B.
    # This handles both barrel/pincushion distortion AND lateral chromatic
    # aberration in a single remap pass.
    undist_coords = mod.apply_subpixel_geometry_distortion()
    if undist_coords is not None:
        # Convert to float32 for cv2.remap (required by OpenCV)
        rgb_linear_f32 = rgb_linear.astype(np.float32)

        corrected = np.empty_like(rgb_linear_f32)
        for ch in range(3):
            # Each channel gets its own coordinate map (TCA shifts R/B
            # relative to G). cv2.INTER_LANCZOS4 gives the best quality
            # resampling (8x8 Lanczos kernel).
            corrected[:, :, ch] = cv2.remap(
                rgb_linear_f32[:, :, ch],
                undist_coords[:, :, ch, :],
                None,
                cv2.INTER_LANCZOS4)

        rgb_linear = corrected.astype(np.float64)
        rgb_linear = np.clip(rgb_linear, 0.0, 1.0)
        del corrected, rgb_linear_f32
        log.debug(" ┊      Applied distortion + TCA correction")
    else:
        log.debug(" ┊      No distortion/TCA data for this lens")

    if not did_vignetting and undist_coords is None:
        log.info(" ┊      No corrections available — image unchanged")
        return rgb

    # --- Step 4: Re-encode gamma (if needed) and convert back to integer ---
    if input_linear:
        # Input was linear — output stays linear
        result = np.clip(rgb_linear * max_val, 0, max_val).astype(original_dtype)
        del rgb_linear
    else:
        # Input was BT.709 — re-encode gamma to match
        rgb_gamma = _bt709_encode(rgb_linear)
        del rgb_linear
        result = np.clip(rgb_gamma * max_val, 0, max_val).astype(original_dtype)
        del rgb_gamma

    return result
