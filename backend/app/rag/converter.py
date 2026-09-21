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
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import Settings, get_settings
from app.rag.ocr import IMAGE_SUFFIXES, OCRService, get_ocr_service

logger = logging.getLogger(__name__)

OFFICE_SUFFIXES = {".docx", ".doc", ".xlsx", ".pptx", ".csv", ".json"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES | OFFICE_SUFFIXES | TEXT_SUFFIXES

PAGE_SEPARATOR = "\n\n"

_PAGE_NUMBER_LINE = re.compile(r"^[-_\s]*\d{1,4}[-_\s]*$")
_SEPARATOR_LINE = re.compile(r"^[_=*\-]{3,}$")        # không khớp "|---|---|" của bảng
_UPPERCASE_HEADING = re.compile(r"^[A-ZÀ-Ỹ][A-ZÀ-Ỹ\s\d,./()-]{4,}$")
# Dòng mở/đóng khối bố cục do `app.documents.bo_cuc_hanh_chinh` sinh ra.
_DONG_BO_CUC = re.compile(r"^:::(\s|$)")
# Chữ khuôn của văn bản hành chính: viết hoa nhưng KHÔNG phải tiêu đề mục. Quốc
# hiệu và tiêu ngữ là phần đầu thư, ngang hàng với tên cơ quan ban hành - gán
# chúng thành `##` thì con dấu và quốc hiệu to ngang tên văn bản, và tệ hơn:
# `app.rag.chunking` cắt chunk theo heading nên mỗi tờ công văn bị cắt bậy ngay
# tại quốc hiệu, rồi lấy chính dòng đó làm nhãn chương cho nội dung bên dưới.
_CHU_KHUON = (
    "cong hoa xa hoi chu nghia viet nam",
    "doc lap - tu do - hanh phuc",
    "doc lap – tu do – hanh phuc",
    "cong van den",
)


def _la_chu_khuon(dong: str) -> bool:
    """Dòng khuôn của văn bản hành chính, so khớp sau khi bỏ dấu."""
    bo_dau = unicodedata.normalize("NFD", dong.lower())
    bo_dau = "".join(c for c in bo_dau if unicodedata.category(c) != "Mn")
    bo_dau = bo_dau.replace("đ", "d").strip()
    return any(mau in bo_dau for mau in _CHU_KHUON)
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
    trong_khoi_bo_cuc = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = fix_broken_bold(raw_line).rstrip()
        stripped = line.strip()

        # Khối bố cục đi qua nguyên vẹn. Bên trong nó chữ đã được xếp đúng chỗ
        # rồi; chuẩn hoá thêm lần nữa chỉ phá - "ĐƠN KHỞI KIỆN" nằm trong khối
        # căn giữa mà bị gán `##` thì vừa hỏng khối, vừa mất căn giữa.
        if _DONG_BO_CUC.match(stripped):
            # "::: <tên>" mở khối, ":::" trơn đóng khối.
            trong_khoi_bo_cuc = stripped != ":::"
            lines_out.append(stripped)
            continue
        if trong_khoi_bo_cuc:
            lines_out.append(stripped)
            continue

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
            khong_rao = stripped.replace("**", "")
            # Bộ trích xuất PDF cũng tự gắn `#` cho chữ to/đậm, nên quốc hiệu có
            # thể tới đây khi ĐÃ là heading - hạ nó xuống, cùng lý do như dưới.
            if _la_chu_khuon(khong_rao):
                khong_rao = khong_rao.lstrip("#").strip()
            lines_out.append(khong_rao)
            continue

        # Tiêu đề ngầm: dòng ngắn viết hoa toàn bộ, hoặc dòng chỉ gồm **in đậm**.
        if len(stripped) < 200 and not _la_chu_khuon(stripped):
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
# Trích xuất trang digital
# --------------------------------------------------------------------------- #
def extract_digital_pages(path: Path, page_count: int) -> list[str]:
    """Markdown của từng trang PDF, lấy thẳng từ lớp text có sẵn (không OCR).

    Trả về đúng `page_count` phần tử: trang nào không lấy được thì là chuỗi rỗng,
    để chỉ số trong danh sách luôn khớp số trang thật.
    """
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


# --------------------------------------------------------------------------- #
# Converter
# --------------------------------------------------------------------------- #
class DocumentConverter:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._markitdown = None
        self._ocr: OCRService | None = None

    @property
    def ocr(self) -> OCRService:
        """Dịch vụ OCR ĐÚNG theo cấu hình của converter này.

        Trước đây chỗ này gọi thẳng `get_ocr_service()`, tức singleton đọc từ .env
        - nên `DocumentConverter(Settings(ocr_enabled=False))` vẫn gọi mô hình
        thật. Một bài test tưởng mình chạy khép kín hoá ra phụ thuộc vào chữ mà
        model trả về hôm đó, và đỏ khi ai đó chỉnh tham số lấy mẫu.

        Cấu hình toàn cục thì vẫn dùng singleton, để cả tiến trình chia chung một
        thread pool thay vì mỗi nơi dựng một cái.
        """
        if self._ocr is None:
            self._ocr = (get_ocr_service() if self.settings is get_settings()
                         else OCRService(self.settings))
        return self._ocr

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
        ocr = self.ocr
        needs_ocr = ocr.classify_pages(path)
        if not needs_ocr:
            return LoadedDocument(text="")

        page_texts = extract_digital_pages(path, len(needs_ocr))

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
