"""Công cụ dựng bộ slide .pptx từ một đặc tả JSON.

Ranh giới giống `generate_docx`: LLM sinh ĐẶC TẢ (slide nào, tiêu đề gì, gạch đầu
dòng gì), code dựng file. Nhờ vậy đặc tả kiểm tra được trước khi mở PowerPoint, và
biểu đồ - thứ không đi lọt qua JSON - luôn là PNG do code vẽ, truyền riêng qua
`charts` và tham chiếu bằng `chart_key`.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

import anyio

from app.documents.pptx_builder import (
    SLIDE_KINDS,
    DeckSpec,
    MetricBox,
    SlideSpec,
    SlideTable,
    build_pptx,
)
from app.services import storage
from app.tools.base import ToolError
from app.tools.templates import get_template

logger = logging.getLogger(__name__)


def _metrics(raw: Any, index: int) -> list[MetricBox]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise ToolError(f"generate_presentation: slide {index + 1} có `metrics` không phải danh sách")
    boxes: list[MetricBox] = []
    for item in raw:
        if not isinstance(item, dict) or "label" not in item or "value" not in item:
            raise ToolError(
                f"generate_presentation: slide {index + 1} có ô chỉ tiêu thiếu label/value"
            )
        boxes.append(MetricBox(label=str(item["label"]), value=str(item["value"]),
                               note=str(item.get("note") or "")))
    return boxes


def _table(raw: Any, index: int) -> SlideTable | None:
    if not raw:
        return None
    if not isinstance(raw, dict) or "columns" not in raw or "rows" not in raw:
        raise ToolError(f"generate_presentation: slide {index + 1} có `table` thiếu columns/rows")
    return SlideTable(
        columns=[str(c) for c in raw["columns"]],
        rows=[[str(cell) for cell in row] for row in raw["rows"]],
    )


def _slide(raw: Any, index: int, charts: dict[str, bytes]) -> SlideSpec:
    if not isinstance(raw, dict):
        raise ToolError(f"generate_presentation: slide {index + 1} phải là object")

    kind = str(raw.get("kind") or "").lower()
    if kind not in SLIDE_KINDS:
        raise ToolError(
            f"generate_presentation: slide {index + 1} có kiểu {kind!r} không hợp lệ. "
            f"Chỉ dùng: {', '.join(SLIDE_KINDS)}"
        )

    bullets = raw.get("bullets") or []
    if isinstance(bullets, str):
        bullets = [bullets]
    if not isinstance(bullets, list):
        raise ToolError(f"generate_presentation: slide {index + 1} có `bullets` không phải danh sách")

    chart_key = str(raw.get("chart_key") or "")
    chart = charts.get(chart_key) if chart_key else None
    # Slide biểu đồ mà không có biểu đồ thì chỉ còn cái tiêu đề: bắt lỗi ở đây,
    # đừng để người dùng phát hiện khi đang trình chiếu.
    if kind == "chart" and chart is None:
        available = ", ".join(sorted(charts)) or "(không có)"
        raise ToolError(
            f"generate_presentation: slide {index + 1} cần biểu đồ {chart_key!r} "
            f"nhưng chỉ có: {available}"
        )

    return SlideSpec(
        kind=kind,
        title=str(raw.get("title") or ""),
        subtitle=str(raw.get("subtitle") or ""),
        bullets=[str(b).strip() for b in bullets if str(b).strip()],
        metrics=_metrics(raw.get("metrics"), index),
        chart=chart,
        caption=str(raw.get("caption") or ""),
        table=_table(raw.get("table"), index),
        notes=str(raw.get("notes") or ""),
    )


async def _template_file(template_id: str) -> str:
    """Mẫu .pptx: tra trong danh mục mẫu trước, sau đó mới tới file đã tải lên."""
    template = await get_template(template_id)
    if template.get("found") and str(template.get("file_path", "")).endswith(".pptx"):
        return str(template["file_path"])
    try:
        return str(storage.resolve(template_id).path)
    except storage.StorageError as exc:
        raise ToolError(f"generate_presentation: không tìm thấy mẫu {template_id!r} ({exc})") from exc


async def generate_presentation(
    data: dict[str, Any],
    template_id: str | None = None,
    charts: dict[str, bytes] | None = None,
    filename: str = "",
) -> dict[str, Any]:
    """Dựng file .pptx từ đặc tả bộ slide.

    `data` = {"title", "subtitle", "slides": [{kind, title, bullets, metrics, table,
    chart_key, caption, notes}]}; `charts` = {khoá: PNG} do code vẽ.
    """
    if not isinstance(data, dict):
        raise ToolError("generate_presentation: `data` phải là object có `slides`")

    raw_slides = data.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise ToolError("generate_presentation: `data.slides` phải là danh sách không rỗng")

    charts = charts or {}
    slides = [_slide(raw, i, charts) for i, raw in enumerate(raw_slides)]

    template_file = await _template_file(template_id) if template_id else ""

    title = str(data.get("title") or "Báo cáo")
    deck = DeckSpec(title=title, subtitle=str(data.get("subtitle") or ""), slides=slides)

    stem = filename or f"SLIDE_{date.today():%Y%m%d}"
    output_path = storage.new_output(stem, ".pptx")
    path = await anyio.to_thread.run_sync(
        lambda: build_pptx(deck, output_path, template_file or None)
    )

    return {
        "output_path": str(path),
        "file_id": storage.make_file_id("output", path.name),
        "file_name": path.name,
        "title": title,
        "slide_count": len(slides),
        "used_template_file": bool(template_file),
    }
