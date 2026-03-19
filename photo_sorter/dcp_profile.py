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
import os

import numpy as np
import tifffile

log = logging.getLogger(__name__)

# DNG/DCP TIFF tag codes (from Adobe DNG Specification)
TAG_PROFILE_NAME = 50936
TAG_PROFILE_TONE_CURVE = 50940
TAG_PROFILE_LOOKUP_TABLE = 50982
TAG_COLOR_MATRIX_1 = 50721
TAG_COLOR_MATRIX_2 = 50722
TAG_CALIBRATION_ILLUMINANT_1 = 50778
TAG_CALIBRATION_ILLUMINANT_2 = 50779

# Standard sRGB ↔ CIE XYZ D65 matrices (IEC 61966-2-1).
# Used for Phase 3 color correction matrix computation.
M_SRGB_TO_XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
], dtype=np.float64)

M_XYZ_TO_SRGB = np.array([
    [ 3.2404542, -1.5371385, -0.4985314],
    [-0.9692660,  1.8760108,  0.0415560],
    [ 0.0556434, -0.2040259,  1.0572252],
], dtype=np.float64)

# sRGB linear ↔ ProPhoto linear conversion matrices.
# The DNG/DCP LookTable is designed to operate in ProPhoto RGB (RIMM/ROMM,
# ISO 22028-2) with D50 white point. Since rawpy outputs sRGB (D65), we
# convert to ProPhoto before applying the LUT and back to sRGB after.
# Computed via: M_XYZ_D50_to_ProPhoto @ M_Bradford_D65_to_D50 @ M_sRGB_to_XYZ_D65
M_SRGB_TO_PROPHOTO = np.array([
    [0.5293459, 0.3300728, 0.1405813],
    [0.0983744, 0.8734611, 0.0281647],
    [0.0168832, 0.1176725, 0.8654443],
], dtype=np.float64)

M_PROPHOTO_TO_SRGB = np.array([
    [ 2.0340762, -0.7273343, -0.3067417],
    [-0.2288134,  1.2317302, -0.0029169],
    [-0.0085698, -0.1532866,  1.1618564],
], dtype=np.float64)


# ──────────────────────────────────────────────────────────────────────────────
# Phase 3: ColorMatrix parsing and illuminant-based color correction
# ──────────────────────────────────────────────────────────────────────────────

def _parse_rational_matrix(page, tag_code, tag_name):
    """
    Parse a 3×3 matrix stored as rational pairs in a TIFF tag.

    DCP stores matrices as flat tuples of 18 values:
        (num0, den0, num1, den1, ..., num8, den8)
    Each (num, den) pair represents one matrix element = num / den.

    Args:
        page:     tifffile page object.
        tag_code: TIFF tag number.
        tag_name: Human-readable name for logging.

    Returns:
        numpy (3, 3) float64 array, or None if tag is missing/invalid.
    """
    if tag_code not in page.tags:
        log.debug(f" ┊      No {tag_name} (tag {tag_code}) in DCP file")
        return None

    raw = page.tags[tag_code].value
    if len(raw) != 18:
        log.error(f" ┊      Invalid {tag_name}: expected 18 values "
                  f"(9 rational pairs), got {len(raw)}")
        return None

    # Convert rational pairs to floats: (num0/den0, num1/den1, ...)
    floats = [raw[i] / raw[i + 1] for i in range(0, 18, 2)]
    return np.array(floats, dtype=np.float64).reshape(3, 3)


def parse_dcp_color_matrices(dcp_path):
    """
    Extract ColorMatrix1, ColorMatrix2, and calibration illuminant temperatures
    from a DCP file.

    Each ColorMatrix maps CIE XYZ coordinates to camera-native RGB:
        CM × XYZ = Camera_RGB   (DNG convention)

    CalibrationIlluminant1/2 are EXIF LightSource enum codes resolved to
    approximate correlated color temperatures via ILLUMINANT_TEMP.

    Args:
        dcp_path: Absolute path to the .dcp file.

    Returns:
        Tuple (cm1, cm2, temp1, temp2) where:
            cm1:   numpy (3, 3) ColorMatrix1 (warm illuminant), or None
            cm2:   numpy (3, 3) ColorMatrix2 (D65), or None
            temp1: int, color temperature for Illuminant 1 (Kelvin), or None
            temp2: int, color temperature for Illuminant 2 (Kelvin), or None
        Returns (None, None, None, None) if matrices cannot be parsed.
    """
    from photo_sorter.constants import ILLUMINANT_TEMP

    try:
        with tifffile.TiffFile(dcp_path) as tif:
            page = tif.pages[0]

            # Parse calibration illuminant temperatures
            temp1 = None
            temp2 = None
            if TAG_CALIBRATION_ILLUMINANT_1 in page.tags:
                illum_code = page.tags[TAG_CALIBRATION_ILLUMINANT_1].value
                temp1 = ILLUMINANT_TEMP.get(illum_code)
                if temp1 is None:
                    log.warning(f" ┊      Unknown CalibrationIlluminant1 "
                                f"code: {illum_code}")
            if TAG_CALIBRATION_ILLUMINANT_2 in page.tags:
                illum_code = page.tags[TAG_CALIBRATION_ILLUMINANT_2].value
                temp2 = ILLUMINANT_TEMP.get(illum_code)
                if temp2 is None:
                    log.warning(f" ┊      Unknown CalibrationIlluminant2 "
                                f"code: {illum_code}")

            # Parse ColorMatrix1 (warm illuminant) and ColorMatrix2 (D65)
            cm1 = _parse_rational_matrix(page, TAG_COLOR_MATRIX_1,
                                         "ColorMatrix1")
            cm2 = _parse_rational_matrix(page, TAG_COLOR_MATRIX_2,
                                         "ColorMatrix2")

            if cm1 is not None and cm2 is not None:
                log.debug(f" ┊      Color matrices loaded: "
                          f"CM1 ({temp1}K), CM2 ({temp2}K)")

            return cm1, cm2, temp1, temp2

    except Exception as e:
        log.error(f" ┊      Failed to read color matrices from "
                  f"'{dcp_path}': {e}")
        return None, None, None, None


def compute_color_correction_matrix(cm1, cm2, temp1, temp2, scene_temp):
    """
    Compute a 3×3 color correction matrix for a given scene color temperature.

    The correction compensates for rawpy/libraw always using CM2 (D65)
    internally. Under non-D65 illuminants, the correct ColorMatrix is a
    mired-weighted interpolation of CM1 and CM2. The correction converts
    from the CM2-based rendering to the interpolated-matrix rendering.

    Math:
        CM_interp = w1 × CM1 + (1 - w1) × CM2    (mired interpolation)
        Correction = M_xyz2srgb @ inv(CM_interp) @ CM2 @ M_srgb2xyz

    When scene_temp ≈ D65: w1 ≈ 0 → CM_interp = CM2 → Correction = Identity.

    Args:
        cm1:        numpy (3, 3) ColorMatrix1 (warm illuminant).
        cm2:        numpy (3, 3) ColorMatrix2 (D65).
        temp1:      int, color temperature for CM1 (Kelvin), e.g. 2856.
        temp2:      int, color temperature for CM2 (Kelvin), e.g. 6504.
        scene_temp: int, scene color temperature (Kelvin) from EXIF.

    Returns:
        numpy (3, 3) float64 correction matrix for linear sRGB,
        or None if correction is near-identity (scene close to D65).
    """
    # Compute mired interpolation weight
    mired_scene = 1e6 / scene_temp
    mired_temp1 = 1e6 / temp1
    mired_temp2 = 1e6 / temp2

    w1 = (mired_scene - mired_temp2) / (mired_temp1 - mired_temp2)
    w1 = max(0.0, min(1.0, w1))

    # If weight is near zero, scene is close to D65 → no correction needed
    if w1 < 0.001:
        log.debug(f" ┊      Color correction: scene {scene_temp}K ≈ D65, "
                  f"skipping (w1={w1:.4f})")
        return None

    # Interpolate color matrix
    cm_interp = w1 * cm1 + (1.0 - w1) * cm2

    # Correction = M_xyz2srgb @ CM_interp⁻¹ @ CM2 @ M_srgb2xyz
    cm_interp_inv = np.linalg.inv(cm_interp)
    correction = M_XYZ_TO_SRGB @ cm_interp_inv @ cm2 @ M_SRGB_TO_XYZ

    log.debug(f" ┊      Color correction for {scene_temp}K "
              f"(w1={w1:.4f}, mired={mired_scene:.1f}): "
              f"diag=[{correction[0,0]:.4f}, {correction[1,1]:.4f}, "
              f"{correction[2,2]:.4f}]")

    return correction


def apply_color_correction(rgb, correction_matrix, input_linear=False):
    """
    Apply a 3×3 color correction matrix to an RGB image (standalone).

    Used when Phase 3 correction is needed but no LookTable (Phase 2) is
    available. The matrix operates in linear sRGB space.

    The matrix multiply: pixel_out = correction_matrix @ pixel_in
    where pixel_in/pixel_out are (3,) vectors in linear sRGB.

    Args:
        rgb:                (H, W, 3) numpy array, dtype uint8 or uint16.
        correction_matrix:  (3, 3) numpy float64 array.
        input_linear:       If True, input is already linear sRGB. If False,
                            input is BT.709-encoded: linearize before and
                            re-encode after. Output encoding matches input.

    Returns:
        (H, W, 3) array, same dtype as input, with correction applied.
    """
    if rgb.dtype == np.uint8:
        max_val = 255.0
    elif rgb.dtype == np.uint16:
        max_val = 65535.0
    else:
        log.error(f" ┊      Unsupported dtype for color correction: {rgb.dtype}")
        return rgb

    original_dtype = rgb.dtype
    rgb_norm = rgb.astype(np.float64) / max_val

    if not input_linear:
        rgb_norm = _bt709_linearize(rgb_norm)

    # Apply 3×3 matrix: 'ij,hwj->hwi' = matrix[i,j] × pixel[h,w,j] → result[h,w,i]
    rgb_corrected = np.einsum('ij,hwj->hwi', correction_matrix, rgb_norm)
    rgb_corrected = np.clip(rgb_corrected, 0.0, 1.0)

    if not input_linear:
        rgb_corrected = _bt709_encode(rgb_corrected)

    rgb_out = np.clip(rgb_corrected * max_val, 0, max_val)
    return rgb_out.astype(original_dtype)


# ──────────────────────────────────────────────────────────────────────────────
# DCP profile resolution and caching (per-PictureStyle selection)
# ──────────────────────────────────────────────────────────────────────────────

def resolve_dcp_path(picture_style, dcp_dir):
    """
    Build the DCP profile path for a given Canon PictureStyle.

    Canon EOS R7 DCP files are named: "Canon EOS R7 Camera {Style}.dcp"

    Args:
        picture_style: Style name from EXIF (e.g. "Portrait", "Landscape")
        dcp_dir:       Directory containing DCP files

    Returns:
        Absolute path to the DCP file, or None if the file doesn't exist.
        Falls back to "Standard" if the requested style has no DCP.
    """
    dcp_filename = f"Canon EOS R7 Camera {picture_style}.dcp"
    dcp_path = os.path.join(dcp_dir, dcp_filename)

    if os.path.isfile(dcp_path):
        return dcp_path

    # Fallback to Standard if the requested style doesn't have a DCP
    if picture_style != "Standard":
        log.warning(f" ┊      No DCP profile for PictureStyle '{picture_style}', "
                    f"falling back to 'Standard'")
        fallback_path = os.path.join(dcp_dir, "Canon EOS R7 Camera Standard.dcp")
        if os.path.isfile(fallback_path):
            return fallback_path

    log.warning(f" ┊      DCP profile not found: '{dcp_path}'")
    return None


def load_dcp_profile(dcp_path):
    """
    Load tone curve, lookup table, and color matrices from a DCP file.

    Args:
        dcp_path: Absolute path to the .dcp file.

    Returns:
        Tuple (tone_curve, lookup_table, cm1, cm2, temp1, temp2) where:
            tone_curve:    Nx2 numpy array or None
            lookup_table:  (90, 16, 16, 3) numpy array or None
            cm1, cm2:      (3, 3) numpy arrays or None
            temp1, temp2:  int (Kelvin) or None
    """
    tone_curve = parse_dcp_tone_curve(dcp_path)
    lookup_table = None
    if tone_curve is not None:
        lookup_table = parse_dcp_lookup_table(dcp_path)
    cm1, cm2, temp1, temp2 = parse_dcp_color_matrices(dcp_path)
    return tone_curve, lookup_table, cm1, cm2, temp1, temp2


class DcpProfileCache:
    """
    Lazy-loading cache for parsed DCP profiles, keyed by PictureStyle name.

    Each style's DCP is read from disk only once, then cached as a
    (tone_curve, lookup_table, cm1, cm2, temp1, temp2) tuple.
    This avoids re-parsing the same TIFF file for every image in a
    batch that shares the same style.
    """

    def __init__(self, dcp_dir):
        self._dcp_dir = dcp_dir
        self._cache = {}  # {style_name: (tc, lut, cm1, cm2, temp1, temp2)}

    def get(self, picture_style):
        """
        Return (tone_curve, lookup_table, cm1, cm2, temp1, temp2)
        for the given PictureStyle.

        Loads and caches the profile on first access. Returns a tuple
        of Nones if the profile cannot be found or parsed.
        """
        if picture_style not in self._cache:
            dcp_path = resolve_dcp_path(picture_style, self._dcp_dir)
            if dcp_path:
                result = load_dcp_profile(dcp_path)
                self._cache[picture_style] = result
                tc, lut, cm1 = result[0], result[1], result[2]
                log.info(f" ┊      Loaded DCP profile for PictureStyle "
                         f"'{picture_style}' "
                         f"(tone_curve={'yes' if tc is not None else 'no'}, "
                         f"lookup_table={'yes' if lut is not None else 'no'}, "
                         f"color_matrices={'yes' if cm1 is not None else 'no'})")
            else:
                self._cache[picture_style] = (None, None, None, None, None, None)
        return self._cache[picture_style]


# ──────────────────────────────────────────────────────────────────────────────
# Phase 1: ProfileToneCurve (tag 50940)
# ──────────────────────────────────────────────────────────────────────────────

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
    Apply a DCP ProfileToneCurve to an RGB image.

    Per the DNG specification, the ProfileToneCurve operates on linear
    scene-referred data. It maps linear sensor values to output-referred
    (display-ready) values, replacing the standard gamma encoding with the
    camera manufacturer's own contrast/color curve.

    The curve is applied identically to R, G, B channels via a precomputed
    integer LUT (numpy fancy indexing).

    After this function, the output still needs BT.709 gamma encoding for
    display — the tone curve handles contrast/tone mapping but does not
    include the final gamma encoding.

    Args:
        rgb:                numpy array (H, W, 3), dtype uint8 or uint16.
                            Should contain LINEAR values (rawpy gamma=(1,1))
                            for correct DNG-spec behavior.
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

    # Saturation (use safe denominator to avoid division by zero warning)
    max_c_safe = np.maximum(max_c, 1e-10)
    s = np.where(max_c > 1e-10, delta / max_c_safe, 0.0)

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


def apply_lookup_table_to_hsv(hsv, lut, h_scale=0.0):
    """
    Apply 3D HSV lookup table with trilinear interpolation.

    For each HSV pixel, normalizes to LUT coordinates, performs trilinear
    interpolation across the 8 corner voxels, and applies the corrected
    delta values (ΔH, ΔS, ΔV).

    **Hue correction scaling (h_scale)**: The LUT's ΔH corrections are designed
    for the DNG pipeline's specific color-to-ProPhoto conversion path (using the
    ForwardMatrix). Our pipeline uses a different path (rawpy→sRGB→ProPhoto) which
    produces slightly different starting hues. Applying the full ΔH overcorrects,
    consistently increasing the G/R ratio above the camera target.

    Empirical testing on multiple images (tungsten 3500-3700K) shows h_scale=0.0
    (skip hue corrections entirely) produces the best G/R match to camera JPEG:
      - IMG_2378: G/R error drops from 15.3% (h_scale=1.0) to 6.8% (h_scale=0.0)
      - IMG_0001: G/R error drops from 8.8% to 1.0%

    The S and V corrections remain fully applied as they improve color accuracy
    (especially blue/shadow channels) without degrading the G/R balance.

    Args:
        hsv:     (H, W, 3) array with H in [0, 360), S, V in [0, 1].
        lut:     (90, 16, 16, 3) array of HSV correction deltas.
        h_scale: Scale factor for hue corrections (0.0 = skip H, 1.0 = full H).
                 Default 0.0 because our pipeline's ProPhoto starting hues differ
                 from the DNG pipeline's, causing the ΔH corrections to overcorrect.

    Returns:
        (H, W, 3) array with corrected HSV values, clipped to valid ranges.
    """
    hsv = np.asarray(hsv, dtype=np.float64)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    # Normalize HSV to LUT indices
    # 90 hue divisions = 4° each → h=4° maps to index 1.0, h=360° wraps to 0
    h_norm = (h / 360.0) * 90.0   # [0, 90) — wraps via modulo
    s_norm = s * 15.0              # [0, 15]
    v_norm = v * 15.0              # [0, 15]

    # Decompose into integer and fractional parts
    h_floor = np.floor(h_norm).astype(np.int32)
    h_frac = h_norm - h_floor

    s_floor = np.floor(s_norm).astype(np.int32)
    s_frac = s_norm - s_floor

    v_floor = np.floor(v_norm).astype(np.int32)
    v_frac = v_norm - v_floor

    # Clamp/wrap indices to valid ranges
    h_floor = h_floor % 90         # Hue wraps (circular axis)
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
    # Hue correction is additive (degrees), scaled by h_scale (default 0.0 to skip).
    # S/V corrections are multiplicative (per Adobe DCP spec).
    h_corrected = h + delta[..., 0] * h_scale
    s_corrected = s * delta[..., 1]
    v_corrected = v * delta[..., 2]

    # Clamp to valid ranges
    h_corrected = h_corrected % 360.0      # Hue wraps around
    s_corrected = np.clip(s_corrected, 0, 1)
    v_corrected = np.clip(v_corrected, 0, 1)

    hsv_corrected = np.stack([h_corrected, s_corrected, v_corrected], axis=-1)
    return hsv_corrected


def _bt709_linearize(rgb_float):
    """
    Invert BT.709 gamma encoding: convert [0, 1] gamma-encoded to [0, 1] linear.

    Implements the inverse of the BT.709 OETF (Opto-Electronic Transfer Function).
    Used to recover linear RGB from rawpy's BT.709 output before applying the
    DCP LookTable, which is designed to operate on linear data per the DNG spec.
    """
    threshold = 0.081  # BT.709 breakpoint: 4.5 * 0.018
    return np.where(
        rgb_float < threshold,
        rgb_float / 4.5,
        np.power(np.clip((rgb_float + 0.099) / 1.099, 0, None), 1.0 / 0.45)
    )


def _bt709_encode(rgb_linear):
    """
    Apply BT.709 gamma encoding: convert [0, 1] linear to [0, 1] gamma-encoded.

    Implements the BT.709 OETF (Opto-Electronic Transfer Function).
    Used to re-encode linear RGB after LookTable application, before the
    ToneCurve is applied on BT.709 data.
    """
    threshold = 0.018  # BT.709 linear breakpoint
    return np.where(
        rgb_linear < threshold,
        4.5 * rgb_linear,
        1.099 * np.power(np.clip(rgb_linear, 0, None), 0.45) - 0.099
    )


def apply_lookup_table(rgb, lut, input_linear=False,
                       color_correction_matrix=None):
    """
    Apply 3D HSV lookup table to an RGB image.

    Orchestrates the complete process:
        1. Normalize RGB from [0, max] to [0, 1]
        2. If BT.709 input: linearize (invert gamma) to recover linear RGB
        3. Optionally apply Phase 3 color correction matrix (in linear sRGB)
        4. Convert linear sRGB → linear ProPhoto RGB (correct LUT domain)
        5. Convert ProPhoto RGB → HSV
        6. Apply trilinear LUT interpolation in ProPhoto HSV
        7. Convert HSV → ProPhoto RGB
        8. Compensate mean brightness shift from saturation corrections
        9. Convert linear ProPhoto → linear sRGB
       10. If BT.709 input: re-encode BT.709 gamma
       11. Denormalize RGB back to original range and clamp

    The ProPhoto conversion (steps 4 and 9) is always performed regardless of
    input encoding. The DNG specification defines the LookTable to operate in
    RIMM/ProPhoto RGB (ISO 22028-2, D50 white point). Applying the LUT in sRGB
    produces incorrect hue shifts because the same spectral color has different
    HSV coordinates in sRGB vs ProPhoto. For example, amber (tungsten-lit scenes)
    maps to ~27° hue in sRGB but ~43° in ProPhoto, causing the LUT to look up
    the wrong correction voxels.

    **Phase 3 color correction**: When `color_correction_matrix` is provided,
    it is applied in linear sRGB space (before ProPhoto conversion). This
    corrects for rawpy/libraw's D65-only ColorMatrix under non-D65 illuminants
    (e.g., tungsten at 3700K). At D65, the matrix is None (identity), so
    daylight images are unaffected.

    **Brightness compensation**: The LUT's saturation corrections (desaturation)
    increase apparent RGB luminance after HSV→RGB conversion, because lower
    saturation moves colors toward gray (higher mean RGB). We compensate by
    normalizing the mean brightness back to the pre-LUT level, preserving all
    color corrections while preventing the brightness shift.

    Args:
        rgb: (H, W, 3) numpy array, dtype uint8 or uint16.
        lut: (90, 16, 16, 3) lookup table from parse_dcp_lookup_table().
        input_linear: If True, input is already linear sRGB (from rawpy with
                      gamma=(1,1)). If False, input is BT.709-encoded and will
                      be linearized before processing and re-encoded after.
        color_correction_matrix: Optional (3, 3) numpy array for Phase 3 illuminant
                                 correction. Applied in linear sRGB before ProPhoto
                                 conversion.

    Returns:
        (H, W, 3) array, same dtype as input, with LUT corrections applied.
        Output encoding matches input: linear in → linear out, BT.709 in → BT.709 out.
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

    # If BT.709 input: linearize to recover linear sRGB for color processing
    if not input_linear:
        rgb_norm = _bt709_linearize(rgb_norm)

    # At this point, rgb_norm is always linear sRGB [0, 1]

    # Phase 3: Apply color correction matrix in linear sRGB space.
    # This corrects for rawpy using only the D65 ColorMatrix (CM2) when the
    # scene illuminant requires an interpolated matrix (CM1/CM2 blend).
    # Must be applied before ProPhoto conversion since the matrix is sRGB-based.
    if color_correction_matrix is not None:
        rgb_norm = np.einsum('ij,hwj->hwi', color_correction_matrix, rgb_norm)
        rgb_norm = np.clip(rgb_norm, 0.0, 1.0)
        log.debug(" ┊      Phase 3 color correction applied (in linear sRGB)")

    # Convert linear sRGB → linear ProPhoto RGB for correct LUT domain.
    # Always performed: the DNG LookTable is designed for ProPhoto primaries.
    rgb_prophoto = np.einsum('ij,hwj->hwi', M_SRGB_TO_PROPHOTO, rgb_norm)
    rgb_prophoto = np.clip(rgb_prophoto, 0.0, 1.0)
    log.debug(" ┊      Converted to ProPhoto RGB for LUT application")

    # Record mean brightness before LUT for compensation (in ProPhoto space)
    mean_before = rgb_prophoto.mean()

    # Convert to HSV in ProPhoto space
    hsv = _rgb_to_hsv(rgb_prophoto)

    # Apply LUT (corrections designed for ProPhoto HSV)
    hsv_corrected = apply_lookup_table_to_hsv(hsv, lut)

    # Convert back to RGB (still in ProPhoto space)
    rgb_corrected = _hsv_to_rgb(hsv_corrected)

    # Compensate brightness shift caused by saturation corrections.
    # Desaturation moves colors toward gray, increasing RGB mean luminance.
    # We scale the result to match the original mean brightness so the
    # subsequent ToneCurve operates on data with the correct brightness level.
    mean_after = rgb_corrected.mean()
    if mean_after > 1e-10:
        brightness_ratio = mean_before / mean_after
        rgb_corrected = rgb_corrected * brightness_ratio
        log.debug(f" ┊      LUT brightness compensation: "
                  f"ratio={brightness_ratio:.4f} (1.0 = no change)")

    # Convert linear ProPhoto → linear sRGB
    rgb_corrected = np.einsum('ij,hwj->hwi', M_PROPHOTO_TO_SRGB, rgb_corrected)
    rgb_corrected = np.clip(rgb_corrected, 0.0, 1.0)
    log.debug(" ┊      Converted back to sRGB from ProPhoto")

    # If BT.709 input: re-encode gamma to match input encoding
    if not input_linear:
        rgb_corrected = _bt709_encode(rgb_corrected)

    # Denormalize and clamp
    rgb_out = np.clip(rgb_corrected * max_val, 0, max_val)

    # Cast to original dtype
    return rgb_out.astype(original_dtype)
