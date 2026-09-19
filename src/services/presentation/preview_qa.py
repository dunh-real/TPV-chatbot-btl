"""
Preview and QA Service for Presentation Generation.
Handles PPTX → PDF → PNG preview generation and deterministic quality checks.
"""

import os
import logging
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _find_libreoffice() -> Optional[str]:
    """Auto-detect LibreOffice (soffice) executable on the system."""
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/libreoffice",
        "/usr/bin/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


LIBREOFFICE_PATH = os.getenv("LIBREOFFICE_PATH", "") or _find_libreoffice()
PREVIEW_DPI = int(os.getenv("PRESENTATION_PREVIEW_DPI", "150"))


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class QAIssue:
    code: str
    severity: str          # "blocker" | "warning"
    message: str
    slide_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "slide_id": self.slide_id,
        }


@dataclass
class QAReport:
    passed: bool
    total_checks: int = 0
    issues: List[QAIssue] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "total_checks": self.total_checks,
            "issues": [i.to_dict() for i in self.issues],
            "blocker_count": sum(1 for i in self.issues if i.severity == "blocker"),
            "warning_count": sum(1 for i in self.issues if i.severity == "warning"),
        }


# ---------------------------------------------------------------------------
# Preview generation
# ---------------------------------------------------------------------------

def generate_preview(pptx_path: str, output_dir: str) -> List[str]:
    """
    Generate per-slide PNG preview images from a PPTX file.

    Strategy:
      1. If LibreOffice is available: PPTX → PDF → PNG (via pypdfium2).
      2. Fallback: return empty list with a warning (no preview).

    Args:
        pptx_path: Path to the generated PPTX file.
        output_dir: Directory to write PNG files into.

    Returns:
        List of absolute paths to the generated PNG files (one per slide).
    """
    os.makedirs(output_dir, exist_ok=True)
    abs_pptx = os.path.abspath(pptx_path)

    if not os.path.isfile(abs_pptx):
        logger.error(f"PPTX file not found: {abs_pptx}")
        return []

    # Step 1: PPTX → PDF via LibreOffice headless
    pdf_path = _convert_pptx_to_pdf(abs_pptx, output_dir)
    if not pdf_path:
        logger.warning("LibreOffice conversion unavailable. Skipping PNG preview.")
        return []

    # Step 2: PDF → per-slide PNG via pypdfium2
    png_paths = _render_pdf_to_pngs(pdf_path, output_dir)
    return png_paths


def _convert_pptx_to_pdf(pptx_path: str, output_dir: str) -> Optional[str]:
    """Convert PPTX to PDF using LibreOffice headless mode."""
    if not LIBREOFFICE_PATH:
        logger.warning(
            "LibreOffice not found on this system. "
            "Install LibreOffice or set LIBREOFFICE_PATH to enable PDF/PNG preview."
        )
        return None

    try:
        cmd = [
            LIBREOFFICE_PATH,
            "--headless",
            "--norestore",
            "--convert-to", "pdf",
            "--outdir", output_dir,
            pptx_path,
        ]
        logger.info(f"Running LibreOffice: {' '.join(cmd)}")
        result = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )

        if result.returncode != 0:
            logger.error(f"LibreOffice conversion failed: {result.stderr}")
            return None

        # LibreOffice outputs the PDF with the same basename
        basename = os.path.splitext(os.path.basename(pptx_path))[0]
        pdf_path = os.path.join(output_dir, f"{basename}.pdf")

        if os.path.isfile(pdf_path):
            logger.info(f"PDF created: {pdf_path}")
            return pdf_path

        logger.error(f"Expected PDF not found at: {pdf_path}")
        return None

    except subprocess.TimeoutExpired:
        logger.error("LibreOffice conversion timed out (120s).")
        return None
    except Exception as e:
        logger.error(f"LibreOffice conversion error: {e}")
        return None


def _render_pdf_to_pngs(pdf_path: str, output_dir: str) -> List[str]:
    """Render each page of a PDF to a PNG image using pypdfium2."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        logger.error("pypdfium2 is not installed. Cannot render PDF to PNG.")
        return []

    png_paths: List[str] = []
    try:
        doc = pdfium.PdfDocument(pdf_path)
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            bitmap = page.render(scale=PREVIEW_DPI / 72)
            pil_image = bitmap.to_pil()

            png_name = f"slide_{page_idx + 1:02d}.png"
            png_path = os.path.join(output_dir, png_name)
            pil_image.save(png_path, "PNG")
            png_paths.append(os.path.abspath(png_path))

            logger.info(f"Rendered preview: {png_name}")
        doc.close()
    except Exception as e:
        logger.error(f"PDF to PNG rendering error: {e}")

    return png_paths


# ---------------------------------------------------------------------------
# Deterministic QA
# ---------------------------------------------------------------------------

def run_basic_qa(pptx_path: str, expected_slide_count: Optional[int] = None) -> QAReport:
    """
    Run deterministic quality checks on a generated PPTX file.

    Checks performed:
      1. File exists and has non-zero size.
      2. File is a valid ZIP archive (OOXML).
      3. Contains expected PPTX internal structure.
      4. Slide count matches expected count (if provided).
      5. No missing relationship targets (broken internal refs).

    Args:
        pptx_path: Path to the PPTX file to validate.
        expected_slide_count: If provided, validate actual slide count matches.

    Returns:
        QAReport with pass/fail status and any issues found.
    """
    issues: List[QAIssue] = []
    checks_run = 0

    # --- Check 1: File existence and size ---
    checks_run += 1
    if not os.path.isfile(pptx_path):
        issues.append(QAIssue(
            code="FILE_NOT_FOUND",
            severity="blocker",
            message=f"PPTX file does not exist: {pptx_path}"
        ))
        return QAReport(passed=False, total_checks=checks_run, issues=issues)

    checks_run += 1
    file_size = os.path.getsize(pptx_path)
    if file_size == 0:
        issues.append(QAIssue(
            code="FILE_EMPTY",
            severity="blocker",
            message="PPTX file is 0 bytes."
        ))
        return QAReport(passed=False, total_checks=checks_run, issues=issues)

    # --- Check 2: Valid ZIP ---
    checks_run += 1
    if not zipfile.is_zipfile(pptx_path):
        issues.append(QAIssue(
            code="INVALID_ZIP",
            severity="blocker",
            message="PPTX file is not a valid ZIP archive."
        ))
        return QAReport(passed=False, total_checks=checks_run, issues=issues)

    # --- Check 3 & 4: Internal structure + slide count ---
    try:
        with zipfile.ZipFile(pptx_path, "r") as zf:
            names = zf.namelist()

            # Check [Content_Types].xml exists
            checks_run += 1
            if "[Content_Types].xml" not in names:
                issues.append(QAIssue(
                    code="MISSING_CONTENT_TYPES",
                    severity="blocker",
                    message="Missing [Content_Types].xml — not a valid OOXML package."
                ))

            # Count slide XMLs
            slide_files = sorted(
                n for n in names
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            actual_slide_count = len(slide_files)

            checks_run += 1
            if actual_slide_count == 0:
                issues.append(QAIssue(
                    code="NO_SLIDES",
                    severity="blocker",
                    message="PPTX contains no slide XML files."
                ))

            # Check expected slide count
            if expected_slide_count is not None:
                checks_run += 1
                if actual_slide_count != expected_slide_count:
                    issues.append(QAIssue(
                        code="SLIDE_COUNT_MISMATCH",
                        severity="blocker",
                        message=(
                            f"Expected {expected_slide_count} slides, "
                            f"but found {actual_slide_count} in PPTX."
                        )
                    ))

            # --- Check 5: Broken relationship refs ---
            checks_run += 1
            rels_files = [n for n in names if n.endswith(".rels")]
            broken_refs = []
            for rels_file in rels_files:
                try:
                    import xml.etree.ElementTree as ET
                    content = zf.read(rels_file).decode("utf-8")
                    root = ET.fromstring(content)
                    ns = "{http://schemas.openxmlformats.org/package/2006/relationships}"
                    for rel in root.findall(f"{ns}Relationship"):
                        target = rel.get("Target", "")
                        rel_type = rel.get("Type", "")
                        # Skip external relationships
                        if rel.get("TargetMode") == "External":
                            continue
                        # Resolve path from rels file location
                        if target and not target.startswith("http"):
                            if target.startswith("/"):
                                # Absolute path within package (e.g. /ppt/charts/chart1.xml)
                                resolved = target.lstrip("/")
                            else:
                                # Relative path from rels file location
                                rels_dir = os.path.dirname(rels_file).replace("_rels/", "").replace("_rels", "")
                                if rels_dir:
                                    resolved = os.path.normpath(os.path.join(rels_dir, target)).replace("\\", "/")
                                else:
                                    resolved = os.path.normpath(target).replace("\\", "/")
                            if resolved not in names and not any(n.startswith(resolved) for n in names):
                                broken_refs.append(f"{rels_file} -> {target}")
                except Exception:
                    pass  # Skip unparseable rels files

            if broken_refs:
                # Only warn, some rels may be benign
                issues.append(QAIssue(
                    code="BROKEN_RELS",
                    severity="warning",
                    message=f"Found {len(broken_refs)} potentially broken relationship(s): {broken_refs[:5]}"
                ))

    except zipfile.BadZipFile as e:
        issues.append(QAIssue(
            code="CORRUPT_ZIP",
            severity="blocker",
            message=f"PPTX ZIP is corrupt: {e}"
        ))

    has_blockers = any(i.severity == "blocker" for i in issues)
    return QAReport(
        passed=not has_blockers,
        total_checks=checks_run,
        issues=issues,
    )
