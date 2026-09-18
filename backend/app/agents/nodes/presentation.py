"""Các node của workflow 5: tạo bộ slide.

    dữ liệu ─> dàn ý (LLM) ─> nội dung từng slide (LLM) ─┐
            └─> biểu đồ (code) ─────────────────────────┴─> render PPTX (code)

LLM chỉ sinh JSON trung gian: slide nào, tiêu đề gì, gạch đầu dòng gì. Nó không
chạm vào python-pptx. Nhờ vậy dàn ý kiểm tra được trước khi dựng file, và mọi con
số trên slide vẫn đi qua van đối chiếu như workflow 3 và 4.

Phần lấy số liệu và vẽ biểu đồ tái dùng nguyên của workflow 4.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import anyio

from app.agents import references
from app.agents.history import format_history
from app.agents.prompts import (
    PPT_CONTENT_SYSTEM,
    PPT_CONTENT_USER,
    PPT_OUTLINE_SYSTEM,
    PPT_OUTLINE_USER,
)
from app.agents.nodes.report import METRIC_LABELS
from app.core.config import get_settings
from app.documents.pptx_builder import (
    SLIDE_KINDS,
    DeckSpec,
    MetricBox,
    SlideSpec,
    SlideTable,
    build_pptx,
)
from app.documents.verify import check_numbers, collect_known_numbers
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

MAX_SLIDES = 8
MAX_BULLETS = 4


def _period_label(params: dict[str, Any]) -> str:
    return f"tháng {params['thang']}/{params['nam']}"


def _available_data(data: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Mô tả dữ liệu sẵn có để LLM lập dàn ý dựa trên thứ thật sự tồn tại."""
    lines: list[str] = []
    charts: list[str] = []
    tables: list[str] = []

    if (personnel := data.get("personnel")):
        metrics = ", ".join(METRIC_LABELS.get(k, k) for k in personnel["metrics"])
        lines.append(f"- Quân số: {metrics} (có so sánh với kỳ trước)")
        charts.append("personnel")
        tables.append("personnel_breakdown")
    if (equipment := data.get("equipment")):
        metrics = ", ".join(METRIC_LABELS.get(k, k) for k in equipment["metrics"])
        lines.append(f"- Trang thiết bị: {metrics}")
        charts.append("equipment")
        tables.append("equipment_breakdown")
    if (reporting := data.get("reporting")):
        lines.append(f"- Tình hình gửi báo cáo: {reporting['units_reported']}"
                     f"/{reporting['units_total']} đơn vị đã gửi")

    return "\n".join(lines) or "(không có)", charts, tables


# --------------------------------------------------------------------------- #
# 1. Dàn ý
# --------------------------------------------------------------------------- #
def _fallback_outline(data: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """Dàn ý mặc định khi LLM hỏng - vẫn ra được bộ slide dùng được."""
    period = _period_label(params)
    slides: list[dict[str, Any]] = [
        {"kind": "title", "title": f"Báo cáo {period}", "focus": ""},
    ]
    if data.get("personnel"):
        slides += [
            {"kind": "summary", "title": "Tổng quan quân số", "focus": "quan_so"},
            {"kind": "chart", "title": "Biến động quân số", "chart_key": "personnel",
             "focus": "quan_so"},
            {"kind": "table", "title": "Chi tiết theo đơn vị",
             "data_key": "personnel_breakdown", "focus": "quan_so"},
        ]
    if data.get("equipment"):
        slides.append({"kind": "chart", "title": "Tình trạng trang thiết bị",
                       "chart_key": "equipment", "focus": "trang_bi"})
    slides.append({"kind": "bullet", "title": "Đánh giá, kiến nghị", "focus": "tong_hop"})
    return {"title": f"Báo cáo {period}", "subtitle": "", "slides": slides}


async def outline_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}

    data, params = state["data"], state["params"]
    available, charts, tables = _available_data(data)

    outline: dict[str, Any] = {}
    try:
        outline = await get_llm().chat_json(
            [
                {"role": "system", "content": PPT_OUTLINE_SYSTEM.format(
                    available=available,
                    charts=", ".join(charts) or "(không có)",
                    tables=", ".join(tables) or "(không có)")},
                {"role": "user", "content": PPT_OUTLINE_USER.format(
                    history=format_history(state.get("history")), request=state["request"])},
            ],
            model=get_settings().utility_model, temperature=0.2, max_tokens=800,
                thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không lập được dàn ý, dùng dàn ý mặc định: %s", exc)

    raw_slides = outline.get("slides") or []
    if not raw_slides:
        outline = _fallback_outline(data, params)
        raw_slides = outline["slides"]

    # Lọc dàn ý: kiểu slide, khoá biểu đồ và khoá bảng đều phải có thật.
    slides: list[dict[str, Any]] = []
    for raw in raw_slides[:MAX_SLIDES]:
        kind = str(raw.get("kind", "")).lower()
        if kind not in SLIDE_KINDS:
            logger.debug("Bỏ slide kiểu lạ: %r", kind)
            continue
        if kind == "chart" and raw.get("chart_key") not in charts:
            logger.debug("Bỏ slide chart không có dữ liệu: %r", raw.get("chart_key"))
            continue
        if kind == "table" and raw.get("data_key") not in tables:
            logger.debug("Bỏ slide table không có dữ liệu: %r", raw.get("data_key"))
            continue
        slides.append({
            "kind": kind,
            "title": str(raw.get("title") or "").strip(),
            "focus": str(raw.get("focus") or "").strip(),
            "chart_key": raw.get("chart_key"),
            "data_key": raw.get("data_key"),
        })

    if not any(s["kind"] == "title" for s in slides):
        slides.insert(0, {"kind": "title", "title": f"Báo cáo {_period_label(params)}",
                          "focus": "", "chart_key": None, "data_key": None})

    return {"outline": {
        "title": str(outline.get("title") or f"Báo cáo {_period_label(params)}"),
        "subtitle": str(outline.get("subtitle") or ""),
        "slides": slides,
    }}


# --------------------------------------------------------------------------- #
# 2. Nội dung từng slide
# --------------------------------------------------------------------------- #
def _slide_payload(focus: str, data: dict[str, Any]) -> dict[str, Any]:
    """Chỉ đưa cho LLM phần số liệu liên quan tới slide đó."""
    payload: dict[str, Any] = {}
    if focus in ("quan_so", "tong_hop") and data.get("personnel"):
        payload["quan_so"] = {"metrics": data["personnel"]["metrics"],
                              "scope": data["personnel"]["scope"]}
    if focus in ("trang_bi", "tong_hop") and data.get("equipment"):
        payload["trang_bi"] = {"metrics": data["equipment"]["metrics"]}
    if focus in ("bao_cao", "tong_hop") and data.get("reporting"):
        reporting = data["reporting"]
        payload["gui_bao_cao"] = {
            "units_total": reporting["units_total"],
            "units_reported": reporting["units_reported"],
            "missing": [m["ten_don_vi"] for m in reporting.get("missing", [])],
        }
    return payload or {"quan_so": data.get("personnel", {}).get("metrics", {})}


def _metric_boxes(data: dict[str, Any], focus: str) -> list[MetricBox]:
    """Ô chỉ tiêu do CODE dựng thẳng từ số liệu, không qua LLM."""
    source = data.get("personnel") if focus != "trang_bi" else data.get("equipment")
    if not source:
        return []

    boxes: list[MetricBox] = []
    for key, metric in list(source["metrics"].items())[:4]:
        note = ""
        if metric.get("delta") is not None:
            sign = "+" if metric["delta"] > 0 else ""
            note = f"{sign}{metric['delta']:g}"
            if metric.get("delta_pct") is not None:
                note += f" ({metric['delta_pct']:g}%)"
        elif metric.get("share_pct") is not None:
            note = f"{metric['share_pct']:g}%"
        boxes.append(MetricBox(label=METRIC_LABELS.get(key, key),
                               value=f"{metric['value']:g}", note=note))
    return boxes


async def content_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}

    data, params = state["data"], state["params"]
    period = _period_label(params)
    llm = get_llm()
    slides: list[dict[str, Any]] = []

    for spec in state["outline"]["slides"]:
        slide: dict[str, Any] = {**spec, "bullets": [], "notes": "",
                                 "metrics": [], "source_data": {}}

        if spec["kind"] == "title":
            slide["subtitle"] = state["outline"].get("subtitle") or period
            slides.append(slide)
            continue

        if spec["kind"] == "summary":
            slide["metrics"] = [
                {"label": box.label, "value": box.value, "note": box.note}
                for box in _metric_boxes(data, spec.get("focus", ""))
            ]

        if spec["kind"] in ("summary", "bullet"):
            payload = _slide_payload(spec.get("focus", "tong_hop"), data)
            slide["source_data"] = payload
            refs = references.from_data(payload)
            try:
                result = await llm.chat_json(
                    [
                        {"role": "system", "content": PPT_CONTENT_SYSTEM},
                        {"role": "user", "content": PPT_CONTENT_USER.format(
                            slide_title=spec["title"], focus=spec.get("focus") or "tổng hợp",
                            period=period, data=references.render(refs))},
                    ],
                    temperature=0.2, max_tokens=800, thinking=False,
                )
                marked = [
                    str(b).strip() for b in (result.get("bullets") or [])
                    if str(b).strip()
                ][:MAX_BULLETS]
                notes = str(result.get("notes") or "").strip()
                # Bản sạch lên slide và đi vào bước đối chiếu số; bản có marker
                # để UI dựng chỗ bấm. "[1]" lọt vào check_numbers sẽ bị đọc thành
                # con số 1 không truy được về dữ liệu gốc.
                slide["bullets"] = [references.strip_markers(b) for b in marked]
                slide["notes"] = references.strip_markers(notes)
                cited, used = references.renumber(marked, refs) if any(
                    references.MARKER_RE.search(b) for b in marked) else (marked, [])
                slide["bullets_cited"], slide["refs"] = cited, used
            except (LLMError, Exception) as exc:  # noqa: BLE001
                logger.warning("Không viết được slide %r: %s", spec["title"], exc)

        slides.append(slide)

    return {"slides": slides}


# --------------------------------------------------------------------------- #
# 3. Đối chiếu số trên slide
# --------------------------------------------------------------------------- #
async def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {"validation": {"status": "skipped", "issues": []}}

    params = state.get("params", {})
    period_numbers = {str(v) for v in (params.get("thang"), params.get("nam")) if v}

    issues: list[dict[str, Any]] = []
    for index, slide in enumerate(state.get("slides", [])):
        known = collect_known_numbers(slide.get("source_data", {})) | period_numbers
        for bullet in slide.get("bullets", []):
            check = check_numbers(bullet, known, str(index))
            if not check.ok:
                issues.append({"type": "unverified_number", "section": slide["title"],
                               "numbers": check.unverified, "severity": "error",
                               "quote": bullet[:160]})

    blocking = [i for i in issues if i["severity"] == "error"]
    return {"validation": {
        "status": "failed" if blocking else ("warning" if issues else "passed"),
        "issues": issues,
    }}


# --------------------------------------------------------------------------- #
# 4. Render PPTX
# --------------------------------------------------------------------------- #
def _slide_table(data_key: str | None, data: dict[str, Any]) -> SlideTable | None:
    if data_key == "personnel_breakdown" and data.get("personnel"):
        return SlideTable(
            columns=["Đơn vị", "Quân số", "Có mặt", "Vắng"],
            rows=[[r["ten_don_vi"], str(r["quan_so"]), str(r["co_mat"]), str(r["vang"])]
                  for r in data["personnel"]["breakdown"]],
        )
    if data_key == "equipment_breakdown" and data.get("equipment"):
        return SlideTable(
            columns=["Đơn vị", "Trang bị", "Số lượng", "Tình trạng"],
            rows=[[r["ten_don_vi"], r["ten_trang_bi"], str(r["so_luong"]), r["tinh_trang"]]
                  for r in data["equipment"]["breakdown"]],
        )
    return None


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    """Slide có số không truy được vẫn dựng file, nhưng đánh dấu để người duyệt biết.

    Khác văn bản hành chính ở workflow 3-4: slide là tài liệu nội bộ để trình bày,
    chặn hẳn thì người dùng không có gì để sửa. Thay vào đó bỏ đúng những gạch đầu
    dòng có số sai và ghi rõ trong kết quả trả về.
    """
    if state.get("error"):
        return {"output_path": ""}

    cfg = get_settings()
    data, params = state["data"], state["params"]
    charts = state.get("charts", {})
    period = _period_label(params)

    bad_quotes = {issue["quote"] for issue in state.get("validation", {}).get("issues", [])}
    removed = 0

    specs: list[SlideSpec] = []
    for slide in state.get("slides", []):
        bullets = [b for b in slide.get("bullets", []) if b[:160] not in bad_quotes]
        removed += len(slide.get("bullets", [])) - len(bullets)
        specs.append(SlideSpec(
            kind=slide["kind"],
            title=slide.get("title", ""),
            subtitle=slide.get("subtitle", ""),
            bullets=bullets,
            metrics=[MetricBox(**box) for box in slide.get("metrics", [])],
            chart=charts.get(slide.get("chart_key") or ""),
            caption=f"Nguồn: số liệu kiểm kê {period}" if slide["kind"] == "chart" else "",
            table=_slide_table(slide.get("data_key"), data),
            notes=slide.get("notes", ""),
        ))

    deck = DeckSpec(title=state["outline"]["title"],
                    subtitle=state["outline"].get("subtitle") or period,
                    slides=specs)

    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", f"SLIDE_{params['ky']}")
    output_path = Path(cfg.output_dir) / f"{stem}.pptx"
    path = await anyio.to_thread.run_sync(lambda: build_pptx(deck, output_path))

    return {"output_path": str(path), "removed_bullets": removed,
            "slide_count": len(specs)}
