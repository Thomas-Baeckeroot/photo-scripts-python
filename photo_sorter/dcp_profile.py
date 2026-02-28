# -*- coding: utf-8 -*-
"""
DCP (DNG Camera Profile) tone curve extraction and application.

DCP files are TIFF-based camera profiles created by Adobe containing color
matrices, tone curves, and HSL look tables that define how a camera's RAW
sensor data should be rendered to match the camera manufacturer's look.

This module implements both phases of the DCP integration:

**Phase 1**: ProfileToneCurve (tag 50940) — 1D S-curve applying contrast/saturation.
  - Corrects ~80% of the "flat" rendering from rawpy/libraw
  - Replaces default BT.709 gamma with camera manufacturer's curve

**Phase 2**: ProfileLookTableData (tag 50982) — 3D HSV lookup table.
  - Fine-grained color corrections (hue, saturation, value deltas)
  - Trilinear interpolation in HSV space
  - Applied after tone curve for maximum fidelity

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
TAG_PROFILE_LOOKUP_TABLE = 50982


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


def parse_dcp_lookup_table(dcp_path):
    """
    Extract the ProfileLookTableData (3D HSV LUT) from a DCP file (tag 50982).

    The lookup table is stored as a flat array of 69,120 float32 values
    (for 90×16×16×3 dimensions) representing corrections in HSV space:
        - 90 hue divisions (0° to 360°)
        - 16 saturation divisions (0.0 to 1.0)
        - 16 value/brightness divisions (0.0 to 1.0)
        - 3 correction channels: [ΔHue, ΔSaturation, ΔValue]

    Each voxel contains small correction deltas to be added to the HSV values
    during color processing (after tone curve is applied).

    Args:
        dcp_path: Absolute path to the .dcp file.

    Returns:
        numpy array of shape (90, 16, 16, 3) with dtype float32, or None
        if the lookup table cannot be found or read.
    """
    try:
        with tifffile.TiffFile(dcp_path) as tif:
            page = tif.pages[0]

            if TAG_PROFILE_LOOKUP_TABLE not in page.tags:
                log.debug(f" ┊      No ProfileLookTableData (tag {TAG_PROFILE_LOOKUP_TABLE}) "
                          f"in DCP file (Phase 2 not available)")
                return None

            raw_values = np.array(page.tags[TAG_PROFILE_LOOKUP_TABLE].value,
                                  dtype=np.float32)

            # Verify size: should be 90 × 16 × 16 × 3 = 69,120
            expected_size = 90 * 16 * 16 * 3
            if raw_values.size != expected_size:
                log.error(f" ┊      Invalid LookTableData: {raw_values.size} values "
                          f"(expected {expected_size})")
                return None

            # Reshape into 4D array
            lut = raw_values.reshape(90, 16, 16, 3)

            log.debug(f" ┊      3D LookTable loaded: shape={lut.shape}, "
                      f"ΔH=[{lut[..., 0].min():.2f}..{lut[..., 0].max():.2f}], "
                      f"ΔS=[{lut[..., 1].min():.4f}..{lut[..., 1].max():.4f}], "
                      f"ΔV=[{lut[..., 2].min():.4f}..{lut[..., 2].max():.4f}]")

            return lut

    except Exception as e:
        log.error(f" ┊      Failed to read DCP lookup table from '{dcp_path}': {e}")
        return None


def _rgb_to_hsv(rgb):
    """
    Convert RGB to HSV color space (vectorized, full image).

    Args:
        rgb: (H, W, 3) numpy array with R, G, B in [0, 1] (float).

    Returns:
        (H, W, 3) array with H in [0, 360), S and V in [0, 1].
    """
    rgb = np.asarray(rgb, dtype=np.float64)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta = max_c - min_c

    # Value
    v = max_c

    # Saturation
    s = np.where(max_c > 1e-10, delta / max_c, 0.0)

    # Hue (avoiding division by zero)
    h = np.zeros_like(r)
    delta_safe = np.where(delta > 1e-10, delta, 1.0)  # Avoid div/0

    mask_r = (max_c == r) & (delta > 1e-10)
    h = np.where(mask_r, 60 * (((g - b) / delta_safe) % 6), h)

    mask_g = (max_c == g) & (delta > 1e-10)
    h = np.where(mask_g, 60 * ((b - r) / delta_safe + 2), h)

    mask_b = (max_c == b) & (delta > 1e-10)
    h = np.where(mask_b, 60 * ((r - g) / delta_safe + 4), h)

    # Ensure h is in [0, 360)
    h = h % 360.0

    hsv = np.stack([h, s, v], axis=-1)
    return hsv


def _hsv_to_rgb(hsv):
    """
    Convert HSV to RGB color space (vectorized, full image).

    Args:
        hsv: (H, W, 3) numpy array with H in [0, 360), S and V in [0, 1].

    Returns:
        (H, W, 3) array with R, G, B in [0, 1].
    """
    hsv = np.asarray(hsv, dtype=np.float64)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    c = v * s  # chroma
    h_prime = h / 60.0
    x = c * (1.0 - np.abs(h_prime % 2.0 - 1.0))

    # Determine which sextant of the color wheel
    h_prime_int = np.floor(h_prime).astype(np.int32) % 6

    r = np.zeros_like(h)
    g = np.zeros_like(h)
    b = np.zeros_like(h)

    mask0 = h_prime_int == 0
    r = np.where(mask0, c, r)
    g = np.where(mask0, x, g)

    mask1 = h_prime_int == 1
    r = np.where(mask1, x, r)
    g = np.where(mask1, c, g)

    mask2 = h_prime_int == 2
    g = np.where(mask2, c, g)
    b = np.where(mask2, x, b)

    mask3 = h_prime_int == 3
    g = np.where(mask3, x, g)
    b = np.where(mask3, c, b)

    mask4 = h_prime_int == 4
    r = np.where(mask4, x, r)
    b = np.where(mask4, c, b)

    mask5 = h_prime_int == 5
    r = np.where(mask5, c, r)
    b = np.where(mask5, x, b)

    m = v - c
    r = r + m
    g = g + m
    b = b + m

    rgb = np.stack([r, g, b], axis=-1)
    return rgb


def apply_lookup_table_to_hsv(hsv, lut):
    """
    Apply 3D HSV lookup table with trilinear interpolation.

    For each HSV pixel, normalizes to LUT coordinates, performs trilinear
    interpolation across the 8 corner voxels, and applies the corrected
    delta values (ΔH, ΔS, ΔV).

    Args:
        hsv: (H, W, 3) array with H in [0, 360), S, V in [0, 1].
        lut: (90, 16, 16, 3) array of HSV correction deltas.

    Returns:
        (H, W, 3) array with corrected HSV values, clipped to valid ranges.
    """
    hsv = np.asarray(hsv, dtype=np.float64)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # Normalize HSV to LUT indices
    h_norm = (h / 360.0) * 89.0  # [0, 89]
    s_norm = s * 15.0             # [0, 15]
    v_norm = v * 15.0             # [0, 15]

    # Decompose into integer and fractional parts
    h_floor = np.floor(h_norm).astype(np.int32)
    h_frac = h_norm - h_floor

    s_floor = np.floor(s_norm).astype(np.int32)
    s_frac = s_norm - s_floor

    v_floor = np.floor(v_norm).astype(np.int32)
    v_frac = v_norm - v_floor

    # Clamp indices to valid ranges
    h_floor = np.clip(h_floor, 0, 89)
    s_floor = np.clip(s_floor, 0, 15)
    v_floor = np.clip(v_floor, 0, 15)

    # Ceiling indices with wrapping for hue (circular)
    h_ceil = (h_floor + 1) % 90
    s_ceil = np.clip(s_floor + 1, 0, 15)
    v_ceil = np.clip(v_floor + 1, 0, 15)

    # Fetch 8 corner voxels (vectorized)
    c000 = lut[h_floor, s_floor, v_floor, :]  # (H, W, 3)
    c100 = lut[h_ceil,  s_floor, v_floor, :]
    c010 = lut[h_floor, s_ceil,  v_floor, :]
    c110 = lut[h_ceil,  s_ceil,  v_floor, :]
    c001 = lut[h_floor, s_floor, v_ceil,  :]
    c101 = lut[h_ceil,  s_floor, v_ceil,  :]
    c011 = lut[h_floor, s_ceil,  v_ceil,  :]
    c111 = lut[h_ceil,  s_ceil,  v_ceil,  :]

    # Trilinear interpolation along H (hue)
    c00 = (1 - h_frac[..., np.newaxis]) * c000 + h_frac[..., np.newaxis] * c100
    c10 = (1 - h_frac[..., np.newaxis]) * c010 + h_frac[..., np.newaxis] * c110
    c01 = (1 - h_frac[..., np.newaxis]) * c001 + h_frac[..., np.newaxis] * c101
    c11 = (1 - h_frac[..., np.newaxis]) * c011 + h_frac[..., np.newaxis] * c111

    # Trilinear interpolation along S (saturation)
    c0 = (1 - s_frac[..., np.newaxis]) * c00 + s_frac[..., np.newaxis] * c10
    c1 = (1 - s_frac[..., np.newaxis]) * c01 + s_frac[..., np.newaxis] * c11

    # Trilinear interpolation along V (value)
    delta = (1 - v_frac[..., np.newaxis]) * c0 + v_frac[..., np.newaxis] * c1

    # Apply corrections to HSV
    # Note: Hue correction is additive (degrees), while S/V corrections are multiplicative
    # (per Adobe DCP spec: dH added to H, dS multiplied by S, dV multiplied by V)
    h_corrected = h + delta[..., 0]
    s_corrected = s * delta[..., 1]
    v_corrected = v * delta[..., 2]

    # Clamp to valid ranges
    h_corrected = h_corrected % 360.0      # Hue wraps around
    s_corrected = np.clip(s_corrected, 0, 1)
    v_corrected = np.clip(v_corrected, 0, 1)

    hsv_corrected = np.stack([h_corrected, s_corrected, v_corrected], axis=-1)
    return hsv_corrected


def apply_lookup_table(rgb, lut):
    """
    Apply 3D HSV lookup table to an RGB image.

    Orchestrates the complete process:
        1. Normalize RGB from [0, max] to [0, 1]
        2. Convert RGB → HSV
        3. Apply trilinear LUT interpolation
        4. Convert HSV → RGB
        5. Denormalize RGB back to original range
        6. Clamp and return

    Args:
        rgb: (H, W, 3) numpy array, dtype uint8 or uint16.
        lut: (90, 16, 16, 3) lookup table from parse_dcp_lookup_table().

    Returns:
        (H, W, 3) array, same dtype as input, with LUT corrections applied.
    """
    if rgb.dtype == np.uint8:
        max_val = 255.0
    elif rgb.dtype == np.uint16:
        max_val = 65535.0
    else:
        log.error(f" ┊      Unsupported dtype for LUT: {rgb.dtype}")
        return rgb

    original_dtype = rgb.dtype

    # Normalize RGB to [0, 1]
    rgb_norm = rgb.astype(np.float64) / max_val

    # Convert to HSV
    hsv = _rgb_to_hsv(rgb_norm)

    # Apply LUT
    hsv_corrected = apply_lookup_table_to_hsv(hsv, lut)

    # Convert back to RGB
    rgb_corrected = _hsv_to_rgb(hsv_corrected)

    # Denormalize and clamp
    rgb_out = np.clip(rgb_corrected * max_val, 0, max_val)

    # Cast to original dtype
    return rgb_out.astype(original_dtype)
