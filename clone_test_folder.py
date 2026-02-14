#!/usr/bin/env python3

"""
Script to clone the test-folder-template to test-folder.

If test-folder already exists, it is renamed to test-folder.{timestamp}
where {timestamp} is the current date and time in format YYYY-MM-DD_HH-MM-SS.
"""

import sys
import shutil
import logging
from datetime import datetime
from pathlib import Path


def setup_logging():
    """Configure logging for the script."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    return logging.getLogger(__name__)


def get_timestamp():
    """Return current timestamp in format YYYY-MM-DD_HH-MM-SS."""
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def clone_test_folder(project_root, logger):
    """
    Clone test-folder-template to test-folder.

    If test-folder already exists, rename it to test-folder.{timestamp}.

    Args:
        project_root: Path to the project root directory
        logger: Logger instance

    Returns:
        bool: True if successful, False otherwise
    """
    template_dir = project_root / "test-folder-template"
    target_dir = project_root / "test-folder"

    # Check if template directory exists
    if not template_dir.exists():
        logger.error(f"Template directory not found: {template_dir}")
        return False

    # If target directory already exists, rename it
    if target_dir.exists():
        timestamp = get_timestamp()
        backup_dir = project_root / f"test-folder.{timestamp}"
        logger.info(f"Renaming existing test-folder to {backup_dir.name}")
        try:
            target_dir.rename(backup_dir)
        except OSError as e:
            logger.error(f"Failed to rename existing directory: {e}")
            return False

    # Copy template directory to target directory
    logger.info(f"Copying {template_dir.name} to {target_dir.name}")
    try:
        shutil.copytree(template_dir, target_dir)
        logger.info("Clone operation completed successfully")
        return True
    except OSError as e:
        logger.error(f"Failed to copy directory: {e}")
        return False


def main():
    """Main entry point for the script."""
    logger = setup_logging()

    try:
        # Get project root directory (assuming script is in project root)
        project_root = Path(__file__).resolve().parent

        logger.info(f"Project root: {project_root}")

        # Clone test folder
        success = clone_test_folder(project_root, logger)

        # Exit with appropriate status code
        sys.exit(0 if success else 1)

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()