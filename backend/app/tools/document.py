"""Công cụ xử lý và sinh văn bản .docx.

Hai chiều ngược nhau của cùng một tầng:
    analyze_document - đọc một file có sẵn, trả về nhận xét cấu trúc + nội dung
    generate_docx    - nhận nội dung đã chốt, đổ ra file đúng thể thức

`generate_docx` cố tình "ngu": nó dựng đúng những gì được đưa, không tự thêm số
liệu, không tự suy ra người ký. Phần meta nào phải hỏi người dùng thì
`app.tools.templates.get_template` đã liệt kê ở `required_inputs`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import anyio

from app.documents.docx_builder import (
    DocumentPayload,
    RenderedSection,
    RenderedTable,
    build_docx,
)
from app.db.erp_repository import ErpDonViRepository
from app.db.erp_session import erp_session_scope
from app.services import storage
from app.tools.base import ToolError
from app.tools.templates import get_template

logger = logging.getLogger(__name__)

ANALYZABLE_SUFFIXES = {".docx", ".pdf", ".txt", ".md"}


def _ngay_tieng_viet(value: date) -> str:
    return f"ngày {value.day:02d} tháng {value.month} năm {value.year}"


# --------------------------------------------------------------------------- #
# Đọc và soát một văn bản có sẵn
# --------------------------------------------------------------------------- #
async def analyze_document(file_id: str, noi_gui: str = "") -> dict[str, Any]:
    """Chạy workflow 2 trên một file đã tải lên: cấu trúc, chữ nghĩa, định tuyến, nhiệm vụ.

    `file_id` là định danh do `app.services.storage` cấp khi nhận file, không phải
    đường dẫn tuỳ ý - đây là ranh giới chặn đọc trộm file ngoài thư mục cho phép.
    """
    try:
        ref = storage.resolve(file_id)
    except storage.StorageError as exc:
        raise ToolError(f"analyze_document: {exc}") from exc

    if ref.suffix not in ANALYZABLE_SUFFIXES:
        raise ToolError(
            f"analyze_document: chỉ đọc được {', '.join(sorted(ANALYZABLE_SUFFIXES))}, "
            f"nhận được {ref.suffix or '(không rõ)'}"
        )

    # Danh mục phòng ban là nguồn cho bước định tuyến; mất CSDL thì bỏ bước đó
    # chứ không làm hỏng cả việc soát văn bản.
    departments: list[dict[str, str]] = []
    try:
        async with erp_session_scope() as session:
            departments = await ErpDonViRepository(session).catalog()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không đọc được danh mục phòng ban: %s", exc)

    # Import tại chỗ: đồ thị agent import tầng tool, nên tool không được import
    # ngược đồ thị ở mức module.
    from app.agents.graph import run_document_workflow

    result = await run_document_workflow(
        file_path=str(ref.path), file_name=ref.name,
        noi_gui=noi_gui, departments=departments,
    )
    return {"file_id": ref.file_id, "file_name": ref.name, **result}


# --------------------------------------------------------------------------- #
# Sinh văn bản .docx
# --------------------------------------------------------------------------- #
def _section(raw: Any, index: int, images: dict[str, bytes]) -> RenderedSection:
    if not isinstance(raw, dict):
        raise ToolError(f"generate_docx: mục thứ {index + 1} phải là object, nhận được {type(raw).__name__}")

    section_id = str(raw.get("id") or f"s{index + 1}")
    paragraphs = raw.get("paragraphs") or []
    if isinstance(paragraphs, str):
        paragraphs = [paragraphs]
    if not isinstance(paragraphs, list):
        raise ToolError(f"generate_docx: mục {section_id!r} có `paragraphs` không phải danh sách")

    table = None
    if (raw_table := raw.get("table")):
        if not isinstance(raw_table, dict) or "columns" not in raw_table or "rows" not in raw_table:
            raise ToolError(f"generate_docx: mục {section_id!r} có `table` thiếu columns/rows")
        table = RenderedTable(
            columns=[str(c) for c in raw_table["columns"]],
            rows=[[str(cell) for cell in row] for row in raw_table["rows"]],
        )

    return RenderedSection(
        id=section_id,
        title=str(raw.get("title") or ""),
        paragraphs=[str(p).strip() for p in paragraphs if str(p).strip()],
        table=table,
        image=images.get(section_id),
        image_caption=str(raw.get("image_caption") or ""),
    )


async def generate_docx(
    template_id: str,
    content: dict[str, Any],
    images: dict[str, bytes] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Đổ nội dung đã chốt ra file .docx theo mẫu.

    `content` = {"meta": {...}, "sections": [{id, title, paragraphs, table}]}.
    `images` là PNG do code vẽ, khoá theo id của mục - biểu đồ không bao giờ đi
    qua JSON của LLM.
    """
    if not isinstance(content, dict):
        raise ToolError("generate_docx: `content` phải là object có `sections`")

    raw_sections = content.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise ToolError("generate_docx: `content.sections` phải là danh sách không rỗng")

    meta = {str(k): ("" if v is None else str(v)) for k, v in (content.get("meta") or {}).items()}
    images = images or {}
    sections = [_section(raw, i, images) for i, raw in enumerate(raw_sections)]

    template_file = ""
    template_name = ""
    if template_id:
        template = await get_template(template_id)
        if not template.get("found"):
            raise ToolError(
                f"generate_docx: không có mẫu {template_id!r}. "
                f"Chọn trong: {', '.join(t['ma_template'] for t in template.get('candidates', []))}"
            )
        template_file = template.get("file_path") or ""
        template_name = template.get("ten_bao_cao") or ""

    # Hai trường này thuần cơ học nên điền hộ; mọi trường còn lại thiếu là thiếu,
    # tool không bịa người ký hay số ký hiệu.
    meta.setdefault("ngay_bao_cao", _ngay_tieng_viet(date.today()))
    meta.setdefault("dia_danh", "Hà Nội")

    stem = filename or f"{template_id or 'VANBAN'}_{date.today():%Y%m%d}"
    output_path = storage.new_output(stem, ".docx")
    path = await anyio.to_thread.run_sync(
        lambda: build_docx(DocumentPayload(meta=meta, sections=sections),
                           output_path, template_file or None)
    )

    return {
        "output_path": str(path),
        "file_id": storage.make_file_id("output", path.name),
        "file_name": path.name,
        "template_id": template_id,
        "template_name": template_name,
        "used_template_file": bool(template_file),
        "section_count": len(sections),
    }
