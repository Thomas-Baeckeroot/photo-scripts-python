# -*- coding: utf-8 -*-
"""
Unit tests for DCP Phase 2: 3D Lookup Table (tag 50982) and HSV operations.
"""

import numpy as np
import pytest

from photo_sorter.dcp_profile import (
    parse_dcp_lookup_table,
    apply_lookup_table_to_hsv,
    apply_lookup_table,
    _rgb_to_hsv,
    _hsv_to_rgb,
)

DCP_PATH = "/Library/Application Support/Adobe/CameraRaw/CameraProfiles/Camera/Canon EOS R7/Canon EOS R7 Camera Standard.dcp"


class TestParseDCPLookupTable:
    """Test LUT extraction from DCP file."""

    def test_parse_lut_shape(self):
        """Verify LUT is parsed with correct shape."""
        lut = parse_dcp_lookup_table(DCP_PATH)
        assert lut is not None, "Failed to parse LUT from DCP file"
        assert lut.shape == (90, 16, 16, 3), f"Expected (90,16,16,3), got {lut.shape}"
        assert lut.dtype == np.float32, f"Expected float32, got {lut.dtype}"

    def test_lut_value_ranges(self):
        """Verify LUT values are in reasonable ranges."""
        lut = parse_dcp_lookup_table(DCP_PATH)
        # Hue: typically ±180°
        assert lut[..., 0].min() >= -180 and lut[..., 0].max() <= 180, "Hue out of range"
        # Saturation multiplier: typically 0.5-2.0
        assert lut[..., 1].min() > 0 and lut[..., 1].max() < 3, "Saturation multiplier out of range"
        # Value multiplier: typically 0.5-1.5
        assert lut[..., 2].min() > 0 and lut[..., 2].max() < 2, "Value multiplier out of range"

    def test_missing_lut_returns_none(self):
        """Verify graceful fallback when LUT not found."""
        lut = parse_dcp_lookup_table("/nonexistent/path.dcp")
        assert lut is None


class TestRGBHSVConversion:
    """Test RGB ↔ HSV color space conversions."""

    def test_rgb_to_hsv_pure_red(self):
        """Test RGB to HSV for pure red."""
        rgb = np.array([[[1.0, 0.0, 0.0]]], dtype=np.float64)
        hsv = _rgb_to_hsv(rgb)
        h, s, v = hsv[0, 0, :]
        assert abs(h) < 1, f"Red should have H≈0°, got {h:.1f}°"
        assert abs(s - 1.0) < 0.01, f"Red should have S≈1.0, got {s:.2f}"
        assert abs(v - 1.0) < 0.01, f"Red should have V≈1.0, got {v:.2f}"

    def test_rgb_to_hsv_pure_green(self):
        """Test RGB to HSV for pure green."""
        rgb = np.array([[[0.0, 1.0, 0.0]]], dtype=np.float64)
        hsv = _rgb_to_hsv(rgb)
        h, s, v = hsv[0, 0, :]
        assert abs(h - 120) < 1, f"Green should have H≈120°, got {h:.1f}°"
        assert abs(s - 1.0) < 0.01, f"Green should have S≈1.0, got {s:.2f}"

    def test_rgb_to_hsv_pure_blue(self):
        """Test RGB to HSV for pure blue."""
        rgb = np.array([[[0.0, 0.0, 1.0]]], dtype=np.float64)
        hsv = _rgb_to_hsv(rgb)
        h, s, v = hsv[0, 0, :]
        assert abs(h - 240) < 1, f"Blue should have H≈240°, got {h:.1f}°"
        assert abs(s - 1.0) < 0.01, f"Blue should have S≈1.0, got {s:.2f}"

    def test_rgb_to_hsv_gray(self):
        """Test RGB to HSV for grays (neutral colors)."""
        for val in [0.0, 0.5, 1.0]:
            rgb = np.array([[[val, val, val]]], dtype=np.float64)
            hsv = _rgb_to_hsv(rgb)
            h, s, v = hsv[0, 0, :]
            assert abs(s) < 0.01, f"Gray should have S≈0, got {s:.2f}"
            assert abs(v - val) < 0.01, f"Gray should have V≈{val}, got {v:.2f}"

    def test_hsv_to_rgb_round_trip(self):
        """Test round-trip conversion: RGB → HSV → RGB."""
        # Test various colors
        test_colors = [
            [1.0, 0.0, 0.0],  # Red
            [0.0, 1.0, 0.0],  # Green
            [0.0, 0.0, 1.0],  # Blue
            [1.0, 1.0, 0.0],  # Yellow
            [0.5, 0.5, 0.5],  # Gray
            [0.8, 0.2, 0.3],  # Random
        ]
        for rgb_in in test_colors:
            rgb_in = np.array([[rgb_in]], dtype=np.float64)
            hsv = _rgb_to_hsv(rgb_in)
            rgb_out = _hsv_to_rgb(hsv)
            # Allow small precision loss due to float operations
            assert np.allclose(rgb_in, rgb_out, atol=1e-10), \
                f"RGB round-trip failed for {rgb_in.flatten()}"


class TestTrilinearInterpolation:
    """Test 3D LUT trilinear interpolation."""

    def test_lut_corner_values(self):
        """Verify interpolation at LUT corners returns exact values."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        # Create an HSV image with pixels at exact LUT corners
        # Example: H at 0°, S at 0.0, V at 0.0
        hsv = np.array([[[0.0, 0.0, 0.0]]], dtype=np.float64)
        hsv_out = apply_lookup_table_to_hsv(hsv, lut)

        # The output should reflect the LUT value at this corner
        expected_dh = lut[0, 0, 0, 0]
        expected_ds = lut[0, 0, 0, 1]
        expected_dv = lut[0, 0, 0, 2]

        h_out, s_out, v_out = hsv_out[0, 0, :]

        # Hue is additive
        assert abs(h_out - (0.0 + expected_dh)) % 360 < 0.1, \
            f"Hue mismatch at corner"
        # Saturation and Value are multiplicative
        assert abs(s_out - (0.0 * expected_ds)) < 0.01, \
            f"Saturation mismatch at corner"
        assert abs(v_out - (0.0 * expected_dv)) < 0.01, \
            f"Value mismatch at corner"

    def test_hue_wrapping(self):
        """Test hue wrapping at boundaries."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        # Hue near 360° (which is 0°)
        hsv = np.array([[[359.0, 0.5, 0.5]]], dtype=np.float64)
        hsv_out = apply_lookup_table_to_hsv(hsv, lut)

        h_out = hsv_out[0, 0, 0]
        # Should wrap around [0, 360)
        assert 0 <= h_out < 360, f"Hue not in valid range: {h_out}"

    def test_saturation_value_clipping(self):
        """Test clipping of S and V to [0, 1]."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        # Create HSV with high S and V that might go out of bounds
        hsv = np.array([[[180.0, 0.9, 0.9]]], dtype=np.float64)
        hsv_out = apply_lookup_table_to_hsv(hsv, lut)

        _, s_out, v_out = hsv_out[0, 0, :]
        assert 0 <= s_out <= 1, f"Saturation out of range: {s_out}"
        assert 0 <= v_out <= 1, f"Value out of range: {v_out}"


class TestApplyLookupTable:
    """Test full LUT application to RGB images."""

    def test_lut_application_changes_output(self):
        """Verify LUT application modifies pixel values."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        # Create a simple RGB image
        rgb_in = np.array([[[128, 100, 150], [200, 180, 220]]], dtype=np.uint8)
        rgb_out = apply_lookup_table(rgb_in, lut)

        # Verify output is different (LUT should modify pixels)
        assert not np.array_equal(rgb_in, rgb_out), \
            "LUT application should modify pixel values"

        # Verify output dtype matches input
        assert rgb_out.dtype == rgb_in.dtype, \
            f"Output dtype {rgb_out.dtype} != input dtype {rgb_in.dtype}"

    def test_lut_application_uint16(self):
        """Test LUT application on 16-bit images."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        rgb_in = np.array([[[30000, 20000, 40000]]], dtype=np.uint16)
        rgb_out = apply_lookup_table(rgb_in, lut)

        assert rgb_out.dtype == np.uint16
        assert not np.array_equal(rgb_in, rgb_out)

    def test_lut_no_nan_inf(self):
        """Verify LUT output contains no NaN or inf values."""
        lut = parse_dcp_lookup_table(DCP_PATH)

        # Create a larger test image
        rgb_in = np.random.randint(0, 256, size=(100, 100, 3), dtype=np.uint8)
        rgb_out = apply_lookup_table(rgb_in, lut)

        assert not np.any(np.isnan(rgb_out)), "Output contains NaN"
        assert not np.any(np.isinf(rgb_out)), "Output contains inf"
        assert np.all(rgb_out <= 255), "Output exceeds 8-bit range"


class TestPhase2Integration:
    """Integration tests for Phase 2 in the context of image processing."""

    def test_lookup_table_after_tone_curve(self):
        """Verify LUT can be applied after tone curve."""
        from photo_sorter.dcp_profile import (
            parse_dcp_tone_curve,
            apply_tone_curve,
        )

        tone_curve = parse_dcp_tone_curve(DCP_PATH)
        lookup_table = parse_dcp_lookup_table(DCP_PATH)

        assert tone_curve is not None, "Failed to load tone curve"
        assert lookup_table is not None, "Failed to load lookup table"

        # Simulate image development
        rgb = np.random.randint(0, 65536, size=(100, 100, 3), dtype=np.uint16)

        # Phase 1
        rgb = apply_tone_curve(rgb, tone_curve)
        rgb_after_phase1 = rgb.copy()

        # Phase 2
        rgb = apply_lookup_table(rgb, lookup_table)

        # Verify Phase 2 modified the image
        assert not np.array_equal(rgb_after_phase1, rgb), \
            "Phase 2 should modify image after Phase 1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
