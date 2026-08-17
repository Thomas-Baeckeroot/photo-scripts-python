# -*- coding: utf-8 -*-
"""
HDR exposure fusion using enfuse (Enblend/Enfuse suite).

Fuses a bracketed set of exposures (developed to display-referred 16-bit TIFFs
by raw_processing) into a single natural-looking image, then converts it to
AVIF for viewing.

Unlike a true HDR radiance-map + tone-mapping workflow, enfuse blends the
already-developed frames directly using a per-pixel quality weighting (well-
exposedness, local contrast, saturation). Consequences:
    - No camera response curve and no radiance map are needed.
    - No intermediate .hdr/.exr file is produced or kept (nothing huge on disk).
    - There is no tone-mapping step, so the result is free of the halos and
      "HDR look" artifacts typical of tone-mapping operators.
This makes enfuse a robust, parameter-free default for automatic batch runs.

The input TIFFs must be gamma-encoded (display-referred): enfuse's exposure
weighting assumes mid-gray near 0.5, so the linear TIFFs produced for panorama
blending would skew the weighting. raw_processing develops HDR-group frames
with the DCP tone curve + BT.709 encoding (display_referred=True) for this reason.

External tools (align_image_stack, enfuse) are called via subprocess.

Functions:
    create_hdr()          — Main entry point: aligned enfuse fusion → AVIF
    check_prerequisites() — Verify align_image_stack and enfuse are available
    collect_hdr_images()  — Gather the bracketed TIFFs/JPGs from an HDR folder
    align_exposures()     — Run align_image_stack on the bracket
    fuse_exposures()      — Run enfuse to blend the aligned frames
"""

import glob
import logging
import os
import shutil
import subprocess

import numpy as np
from PIL import Image

from photo_sorter.display import log_title

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HDR_TIFF_EXTENSION = '.tiff'
MIN_IMAGES_FOR_HDR = 2

# enfuse "detailed" weighting: full exposure weight plus a touch of saturation
# and local contrast, with a hard mask for crisp per-pixel frame selection.
# Empirically the best natural-looking balance on high-contrast scenes
# (validated on backlit sunset brackets): opened shadows, preserved highlights,
# no halos.
ENFUSE_EXPOSURE_WEIGHT = "1.0"
ENFUSE_SATURATION_WEIGHT = "0.4"
ENFUSE_CONTRAST_WEIGHT = "0.3"
ENFUSE_EXPOSURE_SIGMA = "0.2"

AVIF_QUALITY = 90


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites():
    """
    Verify that the required external tools are available.

    Returns:
        bool: True if all prerequisites are met.
    """
    for tool in ("align_image_stack", "enfuse"):
        if not shutil.which(tool):
            log.error(f"Required tool '{tool}' not found in PATH.")
            log.error("Install with: sudo apt install hugin-tools enblend")
            return False
    return True


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_hdr_images(hdr_folder):
    """
    Collect the bracketed image files from *hdr_folder*, sorted alphabetically.

    Prefers 16-bit display-referred TIFFs (from RAW processing). Falls back to
    camera JPEGs when no TIFFs are present (RAW-less bracket).

    Args:
        hdr_folder: Path to the HDR subfolder.

    Returns:
        list[str]: Sorted list of absolute image paths, or empty list on error.
    """
    for pattern_ext in (HDR_TIFF_EXTENSION, '.jpg', '.jpeg', '.JPG', '.JPEG'):
        pattern = os.path.join(hdr_folder, f"*{pattern_ext}")
        files = sorted(glob.glob(pattern))
        if len(files) >= MIN_IMAGES_FOR_HDR:
            log.info(f"Found {len(files)} {pattern_ext.upper()} files "
                     f"in '{hdr_folder}':")
            for f in files:
                log.info(f"  {os.path.basename(f)}")
            return files

    log.error(f"Need at least {MIN_IMAGES_FOR_HDR} images (TIFF or JPG) "
              f"in '{hdr_folder}', found none.")
    return []


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def align_exposures(image_files, work_prefix):
    """
    Align the bracketed exposures with align_image_stack (Hugin).

    Corrects the small hand-held/tripod shifts between frames — and the parallax
    from any aperture change during AEB — before fusion. Produces files named
    ``<work_prefix>NNNN.tif``.

    Args:
        image_files: Sorted list of input image paths (the bracket).
        work_prefix: Output path prefix for aligned frames (e.g. ".../aligned_").

    Returns:
        list[str]: Sorted list of aligned frame paths, or empty list on failure.
    """
    log.info("Aligning exposures with align_image_stack...")
    # -m: optimise field of view / photometric; -C: auto-crop to common area;
    # -c 25: control points per grid; -t 2: remove CP outliers above 2σ.
    result = subprocess.run(
        ["align_image_stack", "-a", work_prefix, "-m", "-C", "-c", "25", "-t", "2"]
        + image_files,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"align_image_stack failed (exit {result.returncode}):")
        if result.stderr:
            log.error(result.stderr.strip())
        return []

    aligned = sorted(glob.glob(work_prefix + "*.tif"))
    log.info(f"Aligned {len(aligned)} frames.")
    return aligned


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

def fuse_exposures(aligned_files, output_path):
    """
    Fuse the aligned exposures into a single image with enfuse.

    Uses the "detailed" weighting (see module constants): exposure fusion with a
    hard mask and mild saturation/contrast weighting.

    Args:
        aligned_files: List of aligned frame paths (from align_exposures).
        output_path: Path for the fused 16-bit TIFF.

    Returns:
        bool: True if enfuse succeeded.
    """
    log.info(f"Running enfuse → '{output_path}'...")
    result = subprocess.run(
        ["enfuse",
         f"--exposure-weight={ENFUSE_EXPOSURE_WEIGHT}",
         f"--saturation-weight={ENFUSE_SATURATION_WEIGHT}",
         f"--contrast-weight={ENFUSE_CONTRAST_WEIGHT}",
         "--hard-mask",
         f"--exposure-sigma={ENFUSE_EXPOSURE_SIGMA}",
         "--depth=16",
         "-o", output_path] + aligned_files,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"enfuse failed (exit {result.returncode}):")
        if result.stderr:
            log.error(result.stderr.strip())
        return False

    log.info(f"enfuse completed: '{output_path}'")
    if result.stderr:
        log.debug(result.stderr.strip())
    return True


# ---------------------------------------------------------------------------
# TIFF → AVIF conversion
# ---------------------------------------------------------------------------

def convert_tiff_to_avif(tiff_path, avif_path, quality=AVIF_QUALITY):
    """
    Convert the fused TIFF (16-bit) to an 8-bit AVIF for viewing.

    Args:
        tiff_path: Path to the input TIFF file.
        avif_path: Path for the output AVIF file.
        quality: AVIF quality (0–100).

    Returns:
        bool: True if conversion succeeded.
    """
    try:
        Image.MAX_IMAGE_PIXELS = None
        img = Image.open(tiff_path)
        log.debug(f"Read TIFF: mode={img.mode}, size={img.size}")

        if img.mode != 'RGB':
            img = img.convert('RGB')

        img_data = np.array(img)
        if img_data.dtype == np.uint16:
            img_data = (img_data / 256).astype(np.uint8)
            img = Image.fromarray(img_data)

        img.save(avif_path, 'AVIF', quality=quality, speed=6)
        log.info(f"Converted to AVIF: '{avif_path}'")
        return True

    except Exception as e:
        log.error(f"TIFF→AVIF conversion failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup_intermediate_files(files):
    """Remove intermediate files (aligned frames)."""
    for f in files:
        try:
            os.remove(f)
            log.debug(f"Removed intermediate file: {f}")
        except OSError as e:
            log.warning(f"Could not remove '{f}': {e}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def create_hdr(hdr_folder):
    """
    Fuse the bracketed exposures in *hdr_folder* into a single AVIF image.

    The folder contains display-referred 16-bit TIFFs (generated from RAW by
    raw_processing with the DCP tone curve + BT.709 encoding) or camera JPGs
    (when no RAW is available).

    File naming:
        Input folder:  ``IMG_2845-7_3_HDR/`` (contains .tiff or .jpg files)
        Final output:  ``IMG_2845-7_3_HDR.avif`` (in the parent directory)

    Args:
        hdr_folder: Path to the HDR subfolder.

    Returns:
        bool: True if the HDR image was created successfully.
    """
    log_title("HDR exposure fusion")
    hdr_folder = os.path.abspath(hdr_folder)
    folder_name = os.path.basename(hdr_folder)
    parent_dir = os.path.dirname(hdr_folder)

    # --- Prerequisites ---
    if not check_prerequisites():
        return False

    # --- Collect bracketed frames (TIFFs or JPGs) ---
    image_files = collect_hdr_images(hdr_folder)
    if not image_files:
        return False

    # --- Align ---
    work_prefix = os.path.join(hdr_folder, "aligned_")
    aligned = align_exposures(image_files, work_prefix)
    if not aligned:
        log.warning("Alignment failed; fusing the unaligned frames instead.")
        aligned = image_files

    # --- Fuse ---
    fused_tiff = os.path.join(parent_dir, f"{folder_name}{HDR_TIFF_EXTENSION}")
    if not fuse_exposures(aligned, fused_tiff):
        log.error("HDR fusion failed.")
        if aligned is not image_files:
            cleanup_intermediate_files(aligned)
        return False

    # --- Cleanup aligned intermediates (never delete the source frames) ---
    if aligned is not image_files:
        cleanup_intermediate_files(aligned)

    # --- Convert TIFF → AVIF ---
    avif_path = os.path.join(parent_dir, f"{folder_name}.avif")
    if convert_tiff_to_avif(fused_tiff, avif_path):
        os.remove(fused_tiff)
        log.debug(f"Removed intermediate fused TIFF: '{fused_tiff}'")
        output_path = avif_path
    else:
        log.warning(f"AVIF conversion failed. Keeping fused TIFF: '{fused_tiff}'")
        output_path = fused_tiff

    log.info(f"HDR created: '{output_path}'")
    return True
