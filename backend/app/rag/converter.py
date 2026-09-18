"""Chuyển file nguồn thành Markdown sạch trước khi chunk.

Mọi định dạng đều quy về Markdown vì đó là dạng giữ được bảng biểu và cấu trúc
tiêu đề - thứ mà trích xuất text thuần làm mất:

    .pdf              trang digital -> pymupdf4llm (bảng thành bảng Markdown)
                      trang scan    -> OCR bằng VLM (app.rag.ocr)
    ảnh               -> OCR
    .docx .xlsx .pptx -> MarkItDown
    .csv .json        -> MarkItDown
    .txt .md          -> đọc thẳng
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.ocr import IMAGE_SUFFIXES, get_ocr_service

logger = logging.getLogger(__name__)

OFFICE_SUFFIXES = {".docx", ".doc", ".xlsx", ".pptx", ".csv", ".json"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES | OFFICE_SUFFIXES | TEXT_SUFFIXES

PAGE_SEPARATOR = "\n\n"

_PAGE_NUMBER_LINE = re.compile(r"^[-_\s]*\d{1,4}[-_\s]*$")
_SEPARATOR_LINE = re.compile(r"^[_=*\-]{3,}$")        # không khớp "|---|---|" của bảng
_UPPERCASE_HEADING = re.compile(r"^[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s\d,./()-]{4,}$")
_BOLD_LINE = re.compile(r"^\*\*(?P<inner>[^*]{3,150})\*\*[:.]?$")
_TABLE_LINE = re.compile(r"^\s*\|")


@dataclass(slots=True)
class LoadedDocument:
    """Markdown đã làm sạch, kèm mốc ký tự bắt đầu mỗi trang (nếu nguồn có trang)."""

    text: str
    page_offsets: list[int] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Làm sạch
# --------------------------------------------------------------------------- #
def fix_broken_bold(line: str) -> str:
    """Gỡ dấu ** bị vỡ vụn trên văn bản tiếng Việt.

    pymupdf4llm phát hiện in đậm theo từng span font; chữ có dấu thường nằm ở span
    khác nên một từ bị xé thành "**Đi**ề**u**". Dòng nào có nhiều hơn một cặp **
    thì gần như chắc chắn là vỡ chứ không phải in đậm thật.
    """
    if line.count("**") > 2:
        return line.replace("**", "")
    return line


def cleanup_markdown(text: str) -> str:
    """Bỏ rác của khâu trích xuất và chuẩn hoá tiêu đề, giữ nguyên khối bảng."""
    lines_out: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = fix_broken_bold(raw_line).rstrip()
        stripped = line.strip()

        # Dòng trống: giữ tối đa một dòng trống liên tiếp (ranh giới đoạn).
        if not stripped:
            if lines_out and lines_out[-1] != "":
                lines_out.append("")
            continue

        # Bảng phải được giữ nguyên xi, kể cả khoảng trắng căn cột.
        if _TABLE_LINE.match(line):
            lines_out.append(line)
            continue

        if _PAGE_NUMBER_LINE.match(stripped) or _SEPARATOR_LINE.match(stripped):
            continue

        if stripped.startswith("#"):
            lines_out.append(stripped.replace("**", ""))
            continue

        # Tiêu đề ngầm: dòng ngắn viết hoa toàn bộ, hoặc dòng chỉ gồm **in đậm**.
        if len(stripped) < 200:
            if _UPPERCASE_HEADING.match(stripped):
                lines_out.append(f"## {stripped}")
                continue
            if (bold := _BOLD_LINE.match(stripped)) is not None:
                lines_out.append(f"### {bold.group('inner').strip()}")
                continue

        lines_out.append(line)

    cleaned = "\n".join(lines_out)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #
class DocumentConverter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._markitdown = None

    def convert(self, path: str | Path) -> LoadedDocument:
        path = Path(path)
        suffix = path.suffix.lower()

        if suffix == ".pdf" or suffix in IMAGE_SUFFIXES:
            return self._convert_pdf(path)
        if suffix in OFFICE_SUFFIXES:
            return LoadedDocument(text=cleanup_markdown(self._convert_office(path)))
        if suffix in TEXT_SUFFIXES:
            raw = path.read_text(encoding="utf-8", errors="ignore")
            return LoadedDocument(text=cleanup_markdown(raw))

        raise ValueError(
            f"Định dạng chưa hỗ trợ: {path.suffix} "
            f"(hỗ trợ: {', '.join(sorted(SUPPORTED_SUFFIXES))})"
        )

    # ------------------------------------------------------------ office -- #
    def _convert_office(self, path: Path) -> str:
        if self._markitdown is None:
            from markitdown import MarkItDown

            self._markitdown = MarkItDown()
        result = self._markitdown.convert(str(path))
        return result.text_content or ""

    # --------------------------------------------------------------- pdf -- #
    def _convert_pdf(self, path: Path) -> LoadedDocument:
        """Trích xuất theo từng trang rồi ghép, giữ lại mốc trang cho trích dẫn."""
        ocr = get_ocr_service()
        needs_ocr = ocr.classify_pages(path)
        if not needs_ocr:
            return LoadedDocument(text="")

        page_texts = self._extract_digital(path, len(needs_ocr))

        scan_pages = [i for i, need in enumerate(needs_ocr) if need]
        if scan_pages:
            if ocr.enabled:
                # OCR chỉ ghi đè khi thực sự đọc được chữ, tránh xoá mất nội dung
                # mà bản digital đã lấy được.
                for page_number, text in ocr.ocr_pages(path, scan_pages).items():
                    page_texts[page_number] = text
            else:
                logger.warning(
                    "%s có %d trang scan nhưng OCR đang tắt (OCR_ENABLED=false) - "
                    "những trang này sẽ thiếu nội dung",
                    path.name, len(scan_pages),
                )

        cleaned = [cleanup_markdown(text) for text in page_texts]
        return self._join_pages(cleaned)

    def _extract_digital(self, path: Path, page_count: int) -> list[str]:
        if path.suffix.lower() in IMAGE_SUFFIXES:
            return [""]
        try:
            import pymupdf4llm

            pages = pymupdf4llm.to_markdown(
                str(path), page_chunks=True, show_progress=False, table_strategy="lines_strict"
            )
        except Exception as exc:  # noqa: BLE001 - còn đường lùi là OCR
            logger.warning("pymupdf4llm lỗi trên %s: %s", path.name, exc)
            return [""] * page_count

        texts = [""] * page_count
        for index, page in enumerate(pages[:page_count]):
            texts[index] = (page.get("text") or "").strip()
        return texts

    @staticmethod
    def _join_pages(page_texts: list[str]) -> LoadedDocument:
        parts: list[str] = []
        offsets: list[int] = []
        cursor = 0
        for text in page_texts:
            # Trang rỗng vẫn phải chiếm một mốc để số trang không bị lệch.
            offsets.append(cursor)
            parts.append(text)
            cursor += len(text) + len(PAGE_SEPARATOR)
        return LoadedDocument(text=PAGE_SEPARATOR.join(parts).strip(), page_offsets=offsets)


_converter: DocumentConverter | None = None


def get_converter() -> DocumentConverter:
    global _converter
    if _converter is None:
        _converter = DocumentConverter()
    return _converter
