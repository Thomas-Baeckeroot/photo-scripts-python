# -*- coding: utf-8 -*-
"""
Panorama assembly using hsi (Hugin Python bindings).

Assembles TIFF files produced by raw_processing into a stitched panorama.
Uses hsi for project management, control point filtering, and optimisation.
External tools (cpfind, nona, enblend) are called via subprocess for steps
that have no hsi equivalent.

Platform: Linux x86_64 only (hugin-tools package provides the hsi module).

Functions:
    create_panorama()       — Main entry point: full pipeline from TIFFs to panorama
    check_prerequisites()   — Verify hsi and external tools are available
    collect_tiff_files()    — Gather input TIFFs from a panorama folder
    compute_hfov()          — Calculate horizontal field of view from focal length
    get_lens_parameters()   — Read focal length and crop factor from EXIF
    create_panorama_project() — Build an hsi.Panorama from TIFF files
    find_control_points()   — Run cpfind to detect control points
    filter_control_points() — Remove high-error CPs using hsi
    optimize_panorama()     — Multi-pass optimisation using hsi
    configure_output()      — Set projection, size, and crop via hsi
    remap_images()          — Run nona to remap images
    blend_panorama()        — Run enblend to merge remapped images
"""

import glob
import logging
import math
import os
import shutil
import subprocess

from photo_sorter.display import log_title
from photo_sorter.metadata import get_exif_with_exiftool

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PANO_TIFF_EXTENSION = '.tiff'
MIN_IMAGES_FOR_PANORAMA = 2
MIN_CONTROL_POINTS_PER_PAIR = 4
CP_ERROR_THRESHOLD = 5.0            # Pixels — CPs above this are removed
CPFIND_LINEARMATCH_LEN = 2          # Each image matched with its N neighbours
DEFAULT_HFOV = 10.5                 # Canon EOS R7 + RF-S 18-150mm @ 150mm
DEFAULT_CROP_FACTOR = 1.6           # APS-C


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

def check_prerequisites():
    """
    Verify that hsi and the required external tools are available.

    Returns:
        bool: True if all prerequisites are met.
    """
    try:
        import hsi  # noqa: F401
    except ImportError:
        log.error("Cannot import 'hsi' (Hugin Python bindings).")
        log.error("Install with: sudo apt install hugin-tools")
        log.error("Then recreate your venv with --system-site-packages")
        return False

    for tool in ("cpfind", "nona", "enblend"):
        if not shutil.which(tool):
            log.error(f"Required tool '{tool}' not found in PATH.")
            log.error("Install with: sudo apt install hugin-tools enblend")
            return False

    return True


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_tiff_files(pano_folder):
    """
    Collect TIFF files from *pano_folder*, sorted alphabetically.

    Args:
        pano_folder: Path to the panorama subfolder containing TIFFs.

    Returns:
        list[str]: Sorted list of absolute TIFF paths, or empty list on error.
    """
    pattern = os.path.join(pano_folder, f"*{PANO_TIFF_EXTENSION}")
    tiff_files = sorted(glob.glob(pattern))

    if len(tiff_files) < MIN_IMAGES_FOR_PANORAMA:
        log.error(f"Need at least {MIN_IMAGES_FOR_PANORAMA} TIFF files in "
                  f"'{pano_folder}', found {len(tiff_files)}.")
        return []

    log.info(f"Found {len(tiff_files)} TIFF files in '{pano_folder}':")
    for f in tiff_files:
        log.info(f"  {os.path.basename(f)}")

    return tiff_files


# ---------------------------------------------------------------------------
# Lens parameters
# ---------------------------------------------------------------------------

def compute_hfov(focal_length_mm, crop_factor):
    """
    Compute horizontal field of view in degrees.

    Uses the rectilinear formula: HFOV = 2 * atan(36 / (2 * crop * focal))
    where 36 mm is the full-frame sensor width.

    Args:
        focal_length_mm: Focal length in millimetres.
        crop_factor: Sensor crop factor (1.6 for APS-C).

    Returns:
        float: HFOV in degrees.
    """
    sensor_width = 36.0  # mm, full-frame reference
    hfov = 2.0 * math.degrees(
        math.atan(sensor_width / (2.0 * crop_factor * focal_length_mm))
    )
    log.debug(f"Computed HFOV: {hfov:.2f}° "
              f"(focal={focal_length_mm}mm, crop={crop_factor})")
    return hfov


def get_lens_parameters(pano_folder, tiff_files):
    """
    Read focal length and crop factor from the RAW file corresponding to the
    first TIFF.  Falls back to defaults with a warning if EXIF is unavailable.

    Expects the RAW files to live in ``../RAW/`` relative to *pano_folder*.

    Args:
        pano_folder: Path to panorama subfolder.
        tiff_files: List of TIFF paths (used to derive RAW filename).

    Returns:
        tuple[float, float]: (hfov_degrees, crop_factor)
    """
    # Derive RAW path: IMG_0010.tiff → ../RAW/IMG_0010.cr3
    raw_folder = os.path.join(os.path.dirname(pano_folder), "RAW")
    tiff_basename = os.path.splitext(os.path.basename(tiff_files[0]))[0]

    focal_length = None
    crop_factor = None

    # Try common RAW extensions
    for ext in ('.cr3', '.cr2', '.nef', '.crw'):
        raw_path = os.path.join(raw_folder, tiff_basename + ext)
        if os.path.isfile(raw_path):
            exif = get_exif_with_exiftool(raw_path)
            focal_length = exif.get('FocalLength')
            crop_factor = exif.get('ScaleFactor35efl')

            # FocalLength may be a string like "150.0 mm"
            if isinstance(focal_length, str):
                focal_length = float(focal_length.split()[0])
            elif focal_length is not None:
                focal_length = float(focal_length)

            if isinstance(crop_factor, str):
                crop_factor = float(crop_factor.split()[0])
            elif crop_factor is not None:
                crop_factor = float(crop_factor)

            if focal_length:
                log.info(f"EXIF from '{raw_path}': "
                         f"focal={focal_length}mm, crop={crop_factor}")
                break

    if not focal_length:
        log.warning(f"Could not read focal length from RAW in '{raw_folder}'. "
                    f"Using defaults: HFOV={DEFAULT_HFOV}°")
        return DEFAULT_HFOV, DEFAULT_CROP_FACTOR

    if not crop_factor:
        crop_factor = DEFAULT_CROP_FACTOR
        log.warning(f"Could not read crop factor, using default: {crop_factor}")

    hfov = compute_hfov(focal_length, crop_factor)
    return hfov, crop_factor


# ---------------------------------------------------------------------------
# Project creation (hsi)
# ---------------------------------------------------------------------------

def create_panorama_project(tiff_files, hfov):
    """
    Build an hsi.Panorama object from a list of TIFF files.

    Each image is set to rectilinear projection with the given HFOV.
    If readEXIF() fails (TIFFs from rawpy may lack EXIF), dimensions are
    read with tifffile as a fallback.

    Args:
        tiff_files: List of absolute TIFF paths.
        hfov: Horizontal field of view in degrees.

    Returns:
        hsi.Panorama: The configured panorama project.
    """
    import hsi

    pano = hsi.Panorama()

    for path in tiff_files:
        img = hsi.SrcPanoImage()
        img.setFilename(path)

        # Try to read EXIF; fall back to tifffile for dimensions
        try:
            img.readEXIF()
            img.applyEXIFValues()
        except Exception:
            log.debug(f"readEXIF failed for '{os.path.basename(path)}', "
                      "reading dimensions with tifffile.")
            import tifffile
            with tifffile.TiffFile(path) as tif:
                page = tif.pages[0]
                w, h = page.shape[1], page.shape[0]
            img.setSize(hsi.Size2D(w, h))

        img.setProjection(hsi.SrcPanoImage.RECTILINEAR)
        img.setHFOV(hfov)
        pano.addImage(img)
        log.debug(f"Added image: {os.path.basename(path)} (HFOV={hfov:.2f}°)")

    log.info(f"Created panorama project with {pano.getNrOfImages()} images.")
    return pano


# ---------------------------------------------------------------------------
# Control point detection (subprocess)
# ---------------------------------------------------------------------------

def find_control_points(pto_path):
    """
    Run cpfind to detect control points between adjacent image pairs.

    Args:
        pto_path: Path to the .pto project file (updated in place).

    Returns:
        bool: True if cpfind succeeded.
    """
    log.info("Running cpfind for control point detection...")
    result = subprocess.run(
        ["cpfind",
         "--linearmatch",
         "--linearmatchlen", str(CPFIND_LINEARMATCH_LEN),
         "-o", pto_path,
         pto_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"cpfind failed (exit {result.returncode}):")
        if result.stderr:
            log.error(result.stderr.strip())
        return False

    log.info("cpfind completed successfully.")
    if result.stderr:
        log.debug(result.stderr.strip())
    return True


# ---------------------------------------------------------------------------
# Control point filtering (hsi pure)
# ---------------------------------------------------------------------------

def filter_control_points(pano, max_error=CP_ERROR_THRESHOLD):
    """
    Remove control points whose error exceeds *max_error* pixels.

    This replaces the cpclean subprocess with direct hsi manipulation,
    giving fine-grained control over which CPs are kept.

    A quick optimisation pass (y/p/r) is run first so that CP errors
    are computed from a reasonable alignment.

    Args:
        pano: hsi.Panorama with control points loaded.
        max_error: Maximum allowed CP error in pixels.

    Returns:
        int: Number of control points remaining after filtering.
    """
    import hsi

    cps = pano.getCtrlPoints()
    total_before = len(cps)

    if total_before == 0:
        log.warning("No control points to filter.")
        return 0

    # Log CP statistics
    log.info(f"Control points before filtering: {total_before}")
    errors = [cps[i].error for i in range(total_before)]
    if any(e > 0 for e in errors):
        valid_errors = [e for e in errors if e > 0]
        log.info(f"  CP errors — min: {min(valid_errors):.2f}, "
                 f"max: {max(valid_errors):.2f}, "
                 f"mean: {sum(valid_errors) / len(valid_errors):.2f}")

    # Collect indices of bad CPs
    bad_indices = [i for i in range(total_before) if cps[i].error > max_error]

    # Remove in reverse order to preserve indices
    for idx in reversed(bad_indices):
        pano.removeCtrlPoint(idx)

    remaining = len(pano.getCtrlPoints())
    removed = total_before - remaining
    log.info(f"Removed {removed} CPs with error > {max_error}px. "
             f"Remaining: {remaining}")

    return remaining


# ---------------------------------------------------------------------------
# Optimisation (hsi pure)
# ---------------------------------------------------------------------------

def _set_optimize_variables_via_pto(pano, pto_path, variables):
    """
    Set optimisation variables by writing ``v`` lines into the PTO file.

    The hsi SWIG bindings for ``OptimizeVector`` (wrapping
    ``std::vector<std::set<std::string>>``) are broken — neither
    ``push_back`` nor ``__getitem__`` work with Python types.
    This function works around the issue by manipulating the PTO text
    directly and reloading it.

    Args:
        pano: hsi.Panorama (will be written then reloaded).
        pto_path: Path to the .pto file (overwritten in place).
        variables: set of variable names (e.g. {"y", "p", "r", "v", "b"}).
    """
    pano.WritePTOFile(pto_path)

    with open(pto_path, 'r') as f:
        lines = f.readlines()

    # Remove existing v lines
    lines = [line for line in lines if not line.startswith('v ')]

    # Add new v lines (image 0 is the anchor — not optimised)
    n_images = pano.getNrOfImages()
    for i in range(1, n_images):
        v_line = "v " + " ".join(f"{var}{i}" for var in sorted(variables))
        lines.append(v_line + "\n")

    with open(pto_path, 'w') as f:
        f.writelines(lines)

    pano.ReadPTOFile(pto_path)


def optimize_panorama(pano, pto_path):
    """
    Multi-pass panorama optimisation using hsi.

    Pass 1: Basic geometry (y, p, r) via ``AutoOptimise.autoOptimise()``.
    Intermediate: filter outlier CPs after initial alignment.
    Pass 2: Geometry + lens (y, p, r, v, b) via PTO ``v`` lines + PTOptimizer.
    Final: centre, straighten, and fit the panorama.

    Args:
        pano: hsi.Panorama with control points loaded.
        pto_path: Path to the .pto file (used for PTO round-trips).

    Returns:
        bool: True if optimisation produced a usable result.
    """
    import hsi

    # Pass 1 — basic geometry (y/p/r)
    # AutoOptimise.autoOptimise() sets up its own optimize vector internally
    log.info("Optimisation pass 1 (geometry: y/p/r)...")
    try:
        hsi.AutoOptimise.autoOptimise(pano)
    except Exception as e:
        log.warning(f"Optimisation pass 1 failed: {e}")
        return False

    # Intermediate filtering — remove outliers after rough alignment
    remaining = filter_control_points(pano)
    if remaining < MIN_CONTROL_POINTS_PER_PAIR:
        log.warning(f"Only {remaining} CPs remain after filtering — "
                    "result may be poor.")

    # Pass 2 — geometry + lens parameters via PTO v lines
    log.info("Optimisation pass 2 (geometry + lens: y/p/r/v/b)...")
    try:
        _set_optimize_variables_via_pto(
            pano, pto_path, {"y", "p", "r", "v", "b"})
        hsi.PTOptimizer(pano).runAlgorithm()
    except Exception as e:
        log.warning(f"Optimisation pass 2 failed: {e}")

    # Final geometric adjustments
    try:
        hsi.CenterHorizontally(pano).runAlgorithm()
        log.debug("CenterHorizontally applied.")
    except Exception as e:
        log.warning(f"CenterHorizontally failed: {e}")

    try:
        hsi.StraightenPanorama(pano).runAlgorithm()
        log.debug("StraightenPanorama applied.")
    except Exception as e:
        log.warning(f"StraightenPanorama failed: {e}")

    try:
        hsi.FitPanorama(pano).runAlgorithm()
        log.debug("FitPanorama applied.")
    except Exception as e:
        log.warning(f"FitPanorama failed: {e}")

    return True


# ---------------------------------------------------------------------------
# Output configuration (hsi pure)
# ---------------------------------------------------------------------------

def configure_output(pano):
    """
    Configure projection, output size, and crop for the final panorama.

    Projection is chosen based on total horizontal FOV:
      - < 100°  → RECTILINEAR
      - 100–180° → CYLINDRICAL
      - > 180°  → EQUIRECTANGULAR

    Args:
        pano: hsi.Panorama (modified in place).
    """
    import hsi

    opts = pano.getOptions()

    # Calculate total FOV
    try:
        fov_calc = hsi.CalculateFOV(pano)
        fov_calc.runAlgorithm()
        hfov = fov_calc.getResultFOV().x
        vfov = fov_calc.getResultFOV().y
        log.info(f"Total FOV: {hfov:.1f}° x {vfov:.1f}°")
    except Exception as e:
        log.warning(f"CalculateFOV failed: {e}. Using defaults.")
        hfov = 120.0

    # Choose projection
    if hfov < 100:
        opts.setProjection(hsi.PanoramaOptions.RECTILINEAR)
        log.info("Output projection: RECTILINEAR")
    elif hfov <= 180:
        opts.setProjection(hsi.PanoramaOptions.CYLINDRICAL)
        log.info("Output projection: CYLINDRICAL")
    else:
        opts.setProjection(hsi.PanoramaOptions.EQUIRECTANGULAR)
        log.info("Output projection: EQUIRECTANGULAR")

    # Optimal scale → output width
    try:
        scale_calc = hsi.CalculateOptimalScale(pano)
        scale_calc.runAlgorithm()
        width = scale_calc.getResultOptimalWidth()
        opts.setWidth(width)
        log.info(f"Optimal output width: {width}px")
    except Exception as e:
        log.warning(f"CalculateOptimalScale failed: {e}")

    # Optimal ROI (crop black borders)
    try:
        roi_calc = hsi.CalculateOptimalROI(pano)
        roi_calc.runAlgorithm()
        roi = roi_calc.getResultOptimalROI()
        opts.setROI(roi)
        log.info(f"Optimal ROI: {roi}")
    except Exception as e:
        log.warning(f"CalculateOptimalROI failed: {e}")

    # Output format: TIFF with enblend blending
    opts.outputFormat = hsi.PanoramaOptions.TIFF_m
    opts.blendMode = hsi.PanoramaOptions.ENBLEND_BLEND

    pano.setOptions(opts)


# ---------------------------------------------------------------------------
# Remapping and blending (subprocess)
# ---------------------------------------------------------------------------

def remap_images(pto_path, output_prefix):
    """
    Run nona to remap each image into the panorama coordinate system.

    Args:
        pto_path: Path to the optimised .pto file.
        output_prefix: Prefix for remapped output files.

    Returns:
        list[str]: Paths to remapped TIFF files, or empty list on failure.
    """
    log.info("Running nona for image remapping...")
    result = subprocess.run(
        ["nona", "-m", "TIFF_m", "-o", output_prefix, pto_path],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"nona failed (exit {result.returncode}):")
        if result.stderr:
            log.error(result.stderr.strip())
        return []

    # nona produces files like prefix0000.tif, prefix0001.tif, ...
    remapped = sorted(glob.glob(f"{output_prefix}*.tif"))
    if not remapped:
        # Also try .tiff extension
        remapped = sorted(glob.glob(f"{output_prefix}*.tiff"))

    log.info(f"nona produced {len(remapped)} remapped files.")
    if result.stderr:
        log.debug(result.stderr.strip())
    return remapped


def blend_panorama(remapped_files, output_path):
    """
    Run enblend to merge remapped images into the final panorama.

    Args:
        remapped_files: List of remapped TIFF paths from nona.
        output_path: Path for the final stitched panorama TIFF.

    Returns:
        bool: True if enblend succeeded.
    """
    log.info(f"Running enblend → '{output_path}'...")
    result = subprocess.run(
        ["enblend", "--compression=lzw", "-o", output_path] + remapped_files,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        log.error(f"enblend failed (exit {result.returncode}):")
        if result.stderr:
            log.error(result.stderr.strip())
        return False

    log.info(f"enblend completed: '{output_path}'")
    if result.stderr:
        log.debug(result.stderr.strip())
    return True


# ---------------------------------------------------------------------------
# TIFF → AVIF conversion
# ---------------------------------------------------------------------------

def convert_tiff_to_avif(tiff_path, avif_path, quality=80):
    """
    Convert a panorama TIFF (16-bit) to an 8-bit AVIF for viewing.

    Args:
        tiff_path: Path to the input TIFF file.
        avif_path: Path for the output AVIF file.
        quality: AVIF quality (0–100).

    Returns:
        bool: True if conversion succeeded.
    """
    import numpy as np
    from PIL import Image

    try:
        img = Image.open(tiff_path)
        log.debug(f"Read TIFF: mode={img.mode}, size={img.size}")

        # Drop alpha channel if present (enblend may add one)
        if img.mode in ('RGBA', 'LA', 'PA'):
            img = img.convert('RGB')
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # Convert 16-bit to 8-bit if needed
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
    """
    Remove intermediate remapped files produced by nona.

    Args:
        files: List of file paths to delete.
    """
    for f in files:
        try:
            os.remove(f)
            log.debug(f"Removed intermediate file: {f}")
        except OSError as e:
            log.warning(f"Could not remove '{f}': {e}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def create_panorama(pano_folder):
    """
    Assemble TIFF files in *pano_folder* into a stitched panorama.

    The folder is expected to contain 16-bit ProPhoto RGB TIFFs generated
    by raw_processing.create_tiff_16bit_from_raw().

    The .pto project file is always saved (even on failure) so it can be
    opened in Hugin GUI for manual adjustments.

    File naming:
        Input folder:  ``IMG_0010-13_4/`` (contains .tiff files)
        PTO file:      ``IMG_0010-13_4/IMG_0010-13_4.pto``
        Final output:  ``IMG_0010-13_4.tiff`` (in the parent directory)

    Args:
        pano_folder: Path to the panorama subfolder.

    Returns:
        bool: True if the panorama was created successfully.
    """
    import hsi

    log_title("Panorama assembly")
    pano_folder = os.path.abspath(pano_folder)
    folder_name = os.path.basename(pano_folder)
    parent_dir = os.path.dirname(pano_folder)

    # --- Prerequisites ---
    if not check_prerequisites():
        return False

    # --- Collect TIFFs ---
    tiff_files = collect_tiff_files(pano_folder)
    if not tiff_files:
        return False

    # --- Lens parameters ---
    hfov, crop_factor = get_lens_parameters(pano_folder, tiff_files)
    log.info(f"Using HFOV = {hfov:.2f}° (crop factor = {crop_factor})")

    # --- Create hsi project ---
    pano = create_panorama_project(tiff_files, hfov)

    # --- Write initial PTO ---
    pto_path = os.path.join(pano_folder, f"{folder_name}.pto")
    pano.WritePTOFile(pto_path)
    log.info(f"Initial PTO written: '{pto_path}'")

    # --- Detect control points ---
    if not find_control_points(pto_path):
        log.error("Control point detection failed. "
                  f"PTO saved at '{pto_path}' for manual use in Hugin.")
        return False

    # --- Reload PTO with CPs ---
    pano = hsi.Panorama()
    pano.ReadPTOFile(pto_path)
    n_cps = len(pano.getCtrlPoints())
    log.info(f"Loaded {n_cps} control points from cpfind.")

    n_images = pano.getNrOfImages()
    min_cps = MIN_CONTROL_POINTS_PER_PAIR * (n_images - 1)
    if n_cps < min_cps:
        log.error(f"Too few control points ({n_cps}, need >= {min_cps}). "
                  f"PTO saved at '{pto_path}' for manual use in Hugin.")
        return False

    # --- Optimise ---
    if not optimize_panorama(pano, pto_path):
        log.warning("Optimisation failed. Attempting to continue anyway.")

    # --- Configure output ---
    configure_output(pano)

    # --- Write final PTO ---
    pano.WritePTOFile(pto_path)
    log.info(f"Final PTO written: '{pto_path}'")

    # --- Remap ---
    nona_prefix = os.path.join(pano_folder, "nona_")
    remapped = remap_images(pto_path, nona_prefix)
    if not remapped:
        log.error("Image remapping failed. "
                  f"PTO saved at '{pto_path}' for manual use in Hugin.")
        return False

    # --- Blend ---
    output_path = os.path.join(parent_dir, f"{folder_name}{PANO_TIFF_EXTENSION}")
    if not blend_panorama(remapped, output_path):
        log.error("Blending failed. Intermediate files kept for debugging.")
        return False

    # --- Cleanup nona intermediates ---
    cleanup_intermediate_files(remapped)

    # --- Convert TIFF → AVIF ---
    avif_path = os.path.join(parent_dir, f"{folder_name}.avif")
    if convert_tiff_to_avif(output_path, avif_path):
        os.remove(output_path)
        log.info(f"Removed intermediate TIFF: '{output_path}'")
        output_path = avif_path
    else:
        log.warning(f"AVIF conversion failed. Keeping TIFF: '{output_path}'")

    log.info(f"Panorama created: '{output_path}'")
    return True
