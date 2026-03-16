"""
Shared LibreOffice PDF conversion utility.

Provides a single function to convert .docx → .pdf via LibreOffice headless,
using a unique temporary user profile directory so the process works under
service accounts (gunicorn) without conflicting user profiles.
"""
import logging
import os
import shutil
import subprocess
import uuid

logger = logging.getLogger(__name__)

# Candidate binary names, tried in order
_LO_CANDIDATES = ["soffice", "libreoffice", "/usr/bin/soffice", "/usr/bin/libreoffice"]


def _find_libreoffice():
    """Return the first working LibreOffice binary name, or None."""
    for candidate in _LO_CANDIDATES:
        try:
            subprocess.run([candidate, "--version"], capture_output=True, timeout=5)
            return candidate
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    return None


def convert_docx_to_pdf(docx_path, outdir, timeout=120):
    """Convert a .docx file to PDF using LibreOffice headless.

    Creates a unique temporary user profile so LibreOffice can run under
    service accounts without profile lock conflicts.

    Args:
        docx_path: Absolute path to the .docx file.
        outdir: Directory where the .pdf will be written.
        timeout: Subprocess timeout in seconds (default 120).

    Returns:
        subprocess.CompletedProcess result, or None if LibreOffice not found.

    Raises:
        RuntimeError: If LibreOffice is not installed.
    """
    lo_bin = _find_libreoffice()
    if not lo_bin:
        raise RuntimeError(
            "LibreOffice is not installed. "
            "Install with: sudo apt-get install -y libreoffice-writer"
        )

    profile_dir = f"/tmp/libreoffice_profile_{uuid.uuid4().hex}"
    os.makedirs(profile_dir, exist_ok=True)

    try:
        result = subprocess.run(
            [
                lo_bin,
                f"-env:UserInstallation=file://{profile_dir}",
                "--headless",
                "--norestore",
                "--convert-to", "pdf",
                "--outdir", outdir,
                docx_path,
            ],
            capture_output=True,
            timeout=timeout,
        )
        return result
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
