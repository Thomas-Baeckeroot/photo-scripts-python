# -*- coding: utf-8 -*-
"""
DCP (DNG Camera Profile) tone curve extraction and application.

DCP files are TIFF-based camera profiles created by Adobe containing color
matrices, tone curves, and HSL look tables that define how a camera's RAW
sensor data should be rendered to match the camera manufacturer's look.

This module implements Phase 1 of the DCP integration: extraction and
application of the ProfileToneCurve (tag 50940). The tone curve alone
corrects ~80% of the "flat" rendering from rawpy/libraw by replacing the
default BT.709 gamma with the camera manufacturer's contrast curve.

Phase 2 (ProfileLookTableData, tag 50982 — 3D HSV LUT) is not yet implemented.

DCP file location (installed by Adobe Camera Raw / Lightroom):
    /Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/

Reference: Adobe DNG Specification 1.6.0.0
"""

import logging

import numpy as np
import tifffile

log = logging.getLogger(__name__)

# DNG/DCP TIFF tag codes (from Adobe DNG Specification)
TAG_PROFILE_NAME = 50936
TAG_PROFILE_TONE_CURVE = 50940


def parse_dcp_tone_curve(dcp_path):
    """
    Extract the ProfileToneCurve from a DCP file.

    The tone curve is stored as TIFF tag 50940: a flat array of float32 values
    representing sequential (X, Y) coordinate pairs, both normalized to [0.0, 1.0].

        X = linear input intensity (sensor data after demosaicing + white balance)
        Y = mapped output intensity (display-ready, includes contrast + gamma)

    For "Camera Standard", this is an S-curve that compresses shadows,
    expands midtones, and gently rolls off highlights — the "Canon look".

    Args:
        dcp_path: Absolute path to the .dcp file.

    Returns:
        numpy array of shape (N, 2) with columns [input, output],
        or None if the curve cannot be read.
    """
    try:
        with tifffile.TiffFile(dcp_path) as tif:
            page = tif.pages[0]

            # Log profile name if available
            if TAG_PROFILE_NAME in page.tags:
                profile_name = page.tags[TAG_PROFILE_NAME].value
                log.info(f" ┊      DCP profile: '{profile_name}'")

            if TAG_PROFILE_TONE_CURVE not in page.tags:
                log.warning(f" ┊      No ProfileToneCurve (tag {TAG_PROFILE_TONE_CURVE}) "
                            f"found in '{dcp_path}'")
                return None

            raw_values = np.array(page.tags[TAG_PROFILE_TONE_CURVE].value,
                                  dtype=np.float64)

            if len(raw_values) < 4 or len(raw_values) % 2 != 0:
                log.error(f" ┊      Invalid tone curve: {len(raw_values)} values "
                          f"(expected even number >= 4)")
                return None

            # Reshape flat [x0, y0, x1, y1, ...] into Nx2 array of (input, output) pairs
            curve_points = raw_values.reshape(-1, 2)

            log.debug(f" ┊      Tone curve: {len(curve_points)} control points, "
                      f"X=[{curve_points[0, 0]:.4f}..{curve_points[-1, 0]:.4f}], "
                      f"Y=[{curve_points[0, 1]:.4f}..{curve_points[-1, 1]:.4f}]")

            return curve_points

    except Exception as e:
        log.error(f" ┊      Failed to read DCP file '{dcp_path}': {e}")
        return None


def build_tone_curve_lut(curve_points, max_value):
    """
    Interpolate tone curve control points into a full integer lookup table.

    Uses piecewise linear interpolation (numpy.interp) over the control points
    to produce an output value for every possible input level.

    Args:
        curve_points: Nx2 numpy array from parse_dcp_tone_curve().
                      Column 0 = input (X), column 1 = output (Y).
        max_value:    Maximum pixel value: 255 for 8-bit, 65535 for 16-bit.

    Returns:
        numpy 1D array of shape (max_value + 1,), dtype uint8 or uint16,
        ready for direct index lookup: output_pixel = lut[input_pixel].
    """
    # Create input levels normalized to [0, 1]
    input_normalized = np.arange(max_value + 1, dtype=np.float64) / max_value

    # Piecewise linear interpolation through the DCP control points
    output_normalized = np.interp(
        input_normalized,
        curve_points[:, 0],   # X coordinates (input)
        curve_points[:, 1],   # Y coordinates (output)
    )

    # Scale back to integer range, clamp, and cast to appropriate dtype
    output_scaled = np.clip(output_normalized * max_value, 0, max_value)

    if max_value <= 255:
        return output_scaled.astype(np.uint8)
    else:
        return output_scaled.astype(np.uint16)


def apply_tone_curve(rgb, tone_curve_points):
    """
    Apply a DCP tone curve to a linear RGB image.

    Builds a LUT at the image's bit depth, then maps every pixel through it
    via numpy fancy indexing (applied identically to R, G, B channels, as
    specified by the DNG standard for ProfileToneCurve).

    The tone curve converts linear sensor values to perceptual (display-ready)
    values, replacing rawpy's default BT.709 gamma with the camera's own
    contrast curve.

    Args:
        rgb:                numpy array (H, W, 3), dtype uint8 or uint16.
                            Must contain LINEAR values (rawpy gamma=(1,1)).
        tone_curve_points:  Nx2 numpy array from parse_dcp_tone_curve().

    Returns:
        numpy array (H, W, 3), same dtype as input, with tone curve applied.
    """
    if rgb.dtype == np.uint8:
        max_value = 255
    elif rgb.dtype == np.uint16:
        max_value = 65535
    else:
        log.error(f" ┊      Unsupported dtype for tone curve: {rgb.dtype}")
        return rgb

    lut = build_tone_curve_lut(tone_curve_points, max_value)
    log.debug(f" ┊      Applying tone curve LUT ({max_value + 1} entries)")

    # Numpy fancy indexing: each pixel value is an index into the LUT.
    # Works on the full (H, W, 3) array at once — same curve for R, G, B.
    return lut[rgb]
