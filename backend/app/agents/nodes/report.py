"""Các node của workflow 4: tổng hợp báo cáo.

    yêu cầu ─> trích tham số ─> gọi data tool (SQL) ─> đối chiếu file báo cáo
            ─> vẽ biểu đồ (code) ─> viết nhận xét (LLM) ─> đối chiếu số ─> DOCX

Khác workflow 3 ở ba điểm: gộp nhiều đơn vị, so sánh giữa các kỳ, và có biểu đồ.
Mọi thứ còn lại - dựng mục, kiểm số, xuất file, ghi sổ - tái dùng nguyên.

Ranh giới không được vượt: **LLM không tính toán**. Tổng, hiệu, tỷ lệ phần trăm,
mức tăng giảm đều do `app.tools.data` tính sẵn; model chỉ diễn đạt. Biểu đồ cũng
do code vẽ, vì người đọc nhìn chiều cao cột chứ không đọc số.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

import anyio

from app.agents.history import format_history
from app.agents import references
from app.agents.prompts import (
    AGG_NARRATIVE_SYSTEM,
    AGG_NARRATIVE_USER,
    AGG_PARAMS_SYSTEM,
    AGG_PARAMS_USER,
)
from app.core.config import get_settings
from app.documents.charts import compare_bar_chart, status_bar_chart
from app.documents.docx_builder import (
    DocumentPayload,
    RenderedSection,
    RenderedTable,
    build_docx,
)
from app.documents.extract_figures import reconcile_file
from app.documents.verify import check_numbers, collect_known_numbers
from app.db.repository import TemplateRepository, VanBanRepository
from app.db.session import session_scope
from app.services.llm import LLMError, get_llm
from app.tools.data import ToolError, call_tool, previous_ky

logger = logging.getLogger(__name__)

METRIC_LABELS = {
    "total_personnel": "Tổng quân số", "present": "Có mặt", "absent": "Vắng",
    "training": "Đi học", "leave": "Nghỉ phép",
    "total_equipment": "Tổng trang bị", "good": "Tình trạng tốt",
    "needs_attention": "Cần xử lý", "equipment_types": "Số chủng loại",
}


async def _next_so_ky_hieu(session, loai_van_ban: str, nam: int, ky_hieu: str) -> str:
    """Số thứ tự tiếp theo trong sổ văn bản của năm.

    Văn bản hành chính đánh số thứ tự trong sổ ("88/BC-HCQT"), không đánh theo
    tháng - dạng "TH08/BC-HCQT" không qua được kiểm tra thể thức.
    """
    from sqlalchemy import extract, func, select

    from app.db.models import VanBan

    result = await session.execute(
        select(func.count()).select_from(VanBan).where(
            VanBan.loai_van_ban == loai_van_ban,
            extract("year", VanBan.ngay_van_ban) == nam,
        )
    )
    return f"{(result.scalar() or 0) + 1:02d}/{ky_hieu}"


def _ngay_tieng_viet(value: date) -> str:
    return f"ngày {value.day:02d} tháng {value.month} năm {value.year}"


# --------------------------------------------------------------------------- #
# 1. Trích tham số
# --------------------------------------------------------------------------- #
async def extract_params_node(state: dict[str, Any]) -> dict[str, Any]:
    from app.db.repository import TaiNguyenRepository

    today = date.today()
    async with session_scope() as session:
        units = await TaiNguyenRepository(session).list_don_vi()
    unit_lines = "\n".join(f"- {u.ma_don_vi}: {u.ten_don_vi}" for u in units)

    params: dict[str, Any] = {}
    try:
        params = await get_llm().chat_json(
            [
                {"role": "system", "content": AGG_PARAMS_SYSTEM.format(
                    today=today.strftime("%d/%m/%Y"), units=unit_lines)},
                {"role": "user", "content": AGG_PARAMS_USER.format(
                    history=format_history(state.get("history")), request=state["request"])},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=400,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không trích được tham số: %s", exc)

    thang, nam = params.get("thang"), params.get("nam")
    assumptions: list[str] = []
    if thang and not nam:
        nam = today.year
        assumptions.append(f"Không nêu năm, hiểu là năm {nam}")
    if not thang:
        # Không nêu kỳ thì lấy tháng trước - báo cáo tổng hợp luôn là của kỳ đã khép.
        previous_month = today.month - 1 or 12
        thang = previous_month
        nam = nam or (today.year if today.month > 1 else today.year - 1)
        assumptions.append(f"Không nêu kỳ, lấy kỳ gần nhất đã khép: tháng {thang}/{nam}")

    valid_codes = {u.ma_don_vi for u in units}
    requested = [c for c in (params.get("ma_don_vi") or []) if c in valid_codes]

    noi_dung = [c for c in (params.get("noi_dung") or []) if c in ("quan_so", "trang_bi")]
    ky = f"{nam:04d}-{thang:02d}"
    compare_to = (f"{nam:04d}-{int(params['so_sanh_thang']):02d}"
                  if params.get("so_sanh_thang") else previous_ky(ky))

    return {
        "params": {"ky": ky, "thang": thang, "nam": nam, "compare_to": compare_to,
                   "ma_don_vi": requested, "noi_dung": noi_dung or ["quan_so", "trang_bi"]},
        "assumptions": assumptions,
    }


# --------------------------------------------------------------------------- #
# 2. Gọi data tool
# --------------------------------------------------------------------------- #
async def gather_data_node(state: dict[str, Any]) -> dict[str, Any]:
    params = state["params"]
    scope = params["ma_don_vi"] or None
    data: dict[str, Any] = {}

    async with session_scope() as session:
        try:
            status = await call_tool(session, "get_reporting_status", ky=params["ky"],
                                     ma_don_vi=scope)
            data["reporting"] = status

            if "quan_so" in params["noi_dung"]:
                result = await call_tool(session, "get_personnel_statistics",
                                         ky=params["ky"], ma_don_vi=scope,
                                         compare_to=params["compare_to"])
                data["personnel"] = result.as_dict()
                data["personnel_consistent"] = result.is_consistent

            if "trang_bi" in params["noi_dung"]:
                result = await call_tool(session, "get_equipment_statistics",
                                         ky=params["ky"], ma_don_vi=scope,
                                         compare_to=params["compare_to"])
                data["equipment"] = result.as_dict()
        except ToolError as exc:
            return {"error": f"Tham số truy vấn không hợp lệ: {exc}"}

    if not any(k in data for k in ("personnel", "equipment")):
        return {"error": "Không có nội dung nào để tổng hợp"}
    return {"data": data}


# --------------------------------------------------------------------------- #
# 3. Đối chiếu với file báo cáo đơn vị đã gửi
# --------------------------------------------------------------------------- #
async def reconcile_node(state: dict[str, Any]) -> dict[str, Any]:
    """CSDL là số chuẩn; đọc lại file để phát hiện đơn vị báo cáo lệch."""
    if state.get("error"):
        return {}

    data = state.get("data", {})
    reporting = data.get("reporting", {})
    personnel = {row["ma_don_vi"]: row for row in data.get("personnel", {}).get("breakdown", [])}

    equipment_totals: dict[str, int] = {}
    for row in data.get("equipment", {}).get("breakdown", []):
        equipment_totals[row["ma_don_vi"]] = equipment_totals.get(row["ma_don_vi"], 0) + row["so_luong"]

    results: list[dict[str, Any]] = []
    for item in reporting.get("reported", []):
        code = item["ma_don_vi"]
        db_values = {
            **{k: v for k, v in personnel.get(code, {}).items()
               if k in ("quan_so", "co_mat", "vang")},
            **({"tong_so_trang_bi": equipment_totals[code]} if code in equipment_totals else {}),
        }
        result = await anyio.to_thread.run_sync(
            lambda c=code, f=item.get("file_path"), d=db_values: reconcile_file(c, f, d)
        )
        results.append(result.as_dict())

    mismatched = [r for r in results if r["status"] == "mismatched"]
    return {"reconciliation": results, "has_discrepancy": bool(mismatched)}


# --------------------------------------------------------------------------- #
# 4. Vẽ biểu đồ - bằng code
# --------------------------------------------------------------------------- #
def _build_charts(data: dict[str, Any], params: dict[str, Any]) -> dict[str, bytes]:
    charts: dict[str, bytes] = {}
    period_label = f"tháng {params['thang']}/{params['nam']}"
    compare_label = f"tháng {int(params['compare_to'][5:])}/{params['compare_to'][:4]}"

    if (personnel := data.get("personnel")):
        metrics = personnel["metrics"]
        labels = [METRIC_LABELS[k] for k in metrics]
        charts["personnel"] = compare_bar_chart(
            f"Quân số {period_label} so với {compare_label}",
            labels,
            [m["value"] for m in metrics.values()],
            [m["prev"] or 0 for m in metrics.values()] if personnel.get("compare_to") else None,
            current_label=period_label.capitalize(), previous_label=compare_label.capitalize(),
        )

    if (equipment := data.get("equipment")):
        by_status: dict[str, int] = {}
        for row in equipment["breakdown"]:
            by_status[row["tinh_trang"]] = by_status.get(row["tinh_trang"], 0) + row["so_luong"]
        if by_status:
            charts["equipment"] = status_bar_chart(
                f"Tình trạng trang thiết bị {period_label}",
                list(by_status), list(by_status.values()),
            )
    return charts


async def charts_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}
    charts = await anyio.to_thread.run_sync(
        lambda: _build_charts(state["data"], state["params"])
    )
    return {"charts": charts}


# --------------------------------------------------------------------------- #
# 5. Dựng các mục
# --------------------------------------------------------------------------- #
def _metric_sentence(metrics: dict[str, Any]) -> str:
    """Câu liệt kê chỉ tiêu do CODE dựng."""
    parts = []
    for key, metric in metrics.items():
        label = METRIC_LABELS.get(key, key)
        text = f"{label}: {metric['value']}"
        if metric.get("share_pct") is not None:
            text += f" ({metric['share_pct']}%)"
        parts.append(text)
    return "; ".join(parts) + "."


def _metric_refs(payload: dict[str, Any]) -> list[references.Reference]:
    """Mỗi chỉ tiêu là một nguồn trích dẫn được.

    Mức này là mức người đọc quan tâm: "tổng quân số" chứ không phải "ô 113".
    """
    refs = [
        references.make("du_lieu", METRIC_LABELS.get(key, key),
                        json.dumps(value, ensure_ascii=False, default=str),
                        {"metric": key})
        for key, value in (payload.get("metrics") or {}).items()
    ]
    if (scope := payload.get("scope")):
        refs.append(references.make("du_lieu", "Phạm vi số liệu",
                                    json.dumps(scope, ensure_ascii=False, default=str),
                                    {"scope": True}))
    return references.number(refs)


async def _narrative(section_title: str, narrative: str, payload: dict[str, Any],
                     params: dict[str, Any]) -> tuple[list[str], list[str],
                                                      list[references.Reference]]:
    """Trả (đoạn sạch, đoạn còn marker, nguồn đã dùng).

    Bản sạch đi vào DOCX và đi vào bước đối chiếu số - marker "[1]" mà lọt vào
    `check_numbers` sẽ bị đọc thành con số 1 không truy được về dữ liệu gốc.
    Bản còn marker để UI dựng chỗ bấm.
    """
    compare = f" (so với kỳ {params['compare_to']})" if params.get("compare_to") else ""
    refs = _metric_refs(payload)
    try:
        result = await get_llm().chat_json(
            [
                {"role": "system", "content": AGG_NARRATIVE_SYSTEM},
                {"role": "user", "content": AGG_NARRATIVE_USER.format(
                    section_title=section_title, period=params["ky"], compare=compare,
                    narrative=narrative, data=references.render(refs))},
            ],
            temperature=0.2, max_tokens=900, thinking=False,
        )
        marked = [str(p).strip() for p in (result.get("paragraphs") or []) if str(p).strip()]
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không viết được mục %s: %s", section_title, exc)
        return [], [], []

    clean = [references.strip_markers(p) for p in marked]
    cited, used = references.renumber(marked, refs) if any(
        references.MARKER_RE.search(p) for p in marked) else (marked, [])
    return [p for p in clean if p], cited, used


async def render_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {}

    data, params = state["data"], state["params"]
    charts = state.get("charts", {})
    period_label = f"tháng {params['thang']}/{params['nam']}"
    sections: list[dict[str, Any]] = []
    index = 1

    # --- I. Tình hình nộp báo cáo: thuần số liệu, không cần LLM ---
    reporting = data.get("reporting", {})
    if reporting:
        missing = reporting.get("missing", [])
        facts = (f"Tổng số {reporting['units_total']} đơn vị, "
                 f"{reporting['units_reported']} đơn vị đã gửi báo cáo.")
        if missing:
            facts += (" Các đơn vị chưa gửi: "
                      + ", ".join(m["ten_don_vi"] for m in missing) + ".")
        sections.append({"id": "nop_bao_cao", "title": f"{'I'*index}. TÌNH HÌNH GỬI BÁO CÁO",
                         "paragraphs": [facts], "table": None, "image": None,
                         "image_caption": "", "llm_written": []})
        index += 1

    # --- II. Quân số ---
    if (personnel := data.get("personnel")):
        title = f"{'I'*index}. TÌNH HÌNH QUÂN SỐ"
        facts = _metric_sentence(personnel["metrics"])
        payload = {"metrics": personnel["metrics"], "scope": personnel["scope"]}
        written, written_cited, refs = await _narrative(
            title,
            "Nhận xét về quân số: nêu mức tăng/giảm so với kỳ trước và tỷ trọng vắng mặt. "
            "Dùng đúng các giá trị delta, delta_pct, share_pct đã cho.",
            payload, params,
        )
        table = RenderedTable(
            columns=["Đơn vị", "Quân số", "Có mặt", "Vắng", "Đi học", "Nghỉ phép"],
            rows=[[r["ten_don_vi"], str(r["quan_so"]), str(r["co_mat"]), str(r["vang"]),
                   str(r["di_hoc"]), str(r["nghi_phep"])] for r in personnel["breakdown"]],
        )
        sections.append({
            "id": "quan_so", "title": title, "paragraphs": [facts, *written],
            "table": {"columns": table.columns, "rows": table.rows},
            "image": charts.get("personnel"),
            "image_caption": f"Biểu đồ: Quân số {period_label} so với kỳ trước",
            # `llm_written` là bản sạch đi vào file; `_cited` giữ marker cho UI.
            "llm_written": written, "llm_written_cited": written_cited,
            "refs": refs, "source_data": payload,
        })
        index += 1

    # --- III. Trang thiết bị ---
    if (equipment := data.get("equipment")):
        title = f"{'I'*index}. TÌNH HÌNH TRANG THIẾT BỊ"
        facts = _metric_sentence(equipment["metrics"])
        payload = {"metrics": equipment["metrics"], "scope": equipment["scope"]}
        written, written_cited, refs = await _narrative(
            title,
            "Nhận xét về trang thiết bị: nêu tỷ lệ tình trạng tốt và số lượng cần xử lý.",
            payload, params,
        )
        sections.append({
            "id": "trang_bi", "title": title, "paragraphs": [facts, *written],
            "table": None, "image": charts.get("equipment"),
            "image_caption": f"Biểu đồ: Tình trạng trang thiết bị {period_label}",
            "llm_written": written, "llm_written_cited": written_cited,
            "refs": refs, "source_data": payload,
        })
        index += 1

    # --- IV. Đối chiếu: chỉ xuất hiện khi thực sự có chênh lệch ---
    if state.get("has_discrepancy"):
        rows = [
            [item["ma_don_vi"], d["label"], str(d["file_value"]), str(d["db_value"])]
            for item in state.get("reconciliation", [])
            for d in item["discrepancies"]
        ]
        sections.append({
            "id": "doi_chieu", "title": f"{'I'*index}. ĐỐI CHIẾU SỐ LIỆU",
            "paragraphs": ["Phát hiện chênh lệch giữa số liệu trong báo cáo của đơn vị "
                           "và số liệu kiểm kê trong hệ thống:"],
            "table": {"columns": ["Đơn vị", "Chỉ tiêu", "Báo cáo ghi", "Kiểm kê"],
                      "rows": rows},
            "image": None, "image_caption": "", "llm_written": [],
        })
        index += 1

    # --- V. Cảnh báo số liệu không nhất quán ---
    inconsistent = data.get("personnel", {}).get("consistency", [])
    if inconsistent:
        sections.append({
            "id": "canh_bao", "title": f"{'I'*index}. SỐ LIỆU CẦN KIỂM TRA LẠI",
            "paragraphs": [item["message"] for item in inconsistent],
            "table": None, "image": None, "image_caption": "", "llm_written": [],
        })

    return {"sections": sections}


# --------------------------------------------------------------------------- #
# 6. Đối chiếu số trong văn bản
# --------------------------------------------------------------------------- #
async def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error"):
        return {"validation": {"status": "skipped", "issues": []}}

    params = state.get("params", {})
    period_numbers = {str(v) for v in (params.get("thang"), params.get("nam")) if v}

    issues: list[dict[str, Any]] = []
    checked_total = 0
    for section in state.get("sections", []):
        # Phạm vi kiểm tra đúng bằng số liệu đã đưa cho LLM ở mục này. Lấy cả `data`
        # làm tập hợp lệ thì một con số bịa như "tăng 5 người" vẫn lọt chỉ vì số 5
        # tình cờ xuất hiện ở chỗ khác trong dữ liệu.
        known = collect_known_numbers(section.get("source_data", {})) | period_numbers
        checked_total = max(checked_total, len(known))
        for paragraph in section.get("llm_written", []):
            check = check_numbers(paragraph, known, section["id"])
            if not check.ok:
                issues.append({"type": "unverified_number", "section": section["id"],
                               "numbers": check.unverified, "severity": "error",
                               "quote": paragraph[:160]})

    if not state.get("data", {}).get("personnel_consistent", True):
        issues.append({"type": "inconsistent_source", "section": "quan_so",
                       "numbers": [], "severity": "warning",
                       "quote": "Số liệu nguồn không thoả ràng buộc quân số"})

    blocking = [i for i in issues if i["severity"] == "error"]
    return {"validation": {
        "status": "failed" if blocking else ("warning" if issues else "passed"),
        "issues": issues, "checked_numbers": checked_total,
    }}


# --------------------------------------------------------------------------- #
# 7. Xuất DOCX + ghi sổ
# --------------------------------------------------------------------------- #
async def export_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("validation", {}).get("status") == "failed":
        return {"output_path": ""}

    cfg = get_settings()
    params = state["params"]
    inputs = state.get("inputs", {})
    period_label = f"tháng {params['thang']}/{params['nam']}"

    async with session_scope() as session:
        template = await TemplateRepository(session).get("BC_TONGHOP")
        template_file = template.file_path if template else ""
        so_ky_hieu = inputs.get("so_ky_hieu") or await _next_so_ky_hieu(
            session, "bao_cao_tong_hop", params["nam"], "BC-HCQT"
        )

    payload = DocumentPayload(
        meta={
            "noi_gui": inputs.get("noi_gui", "PHÒNG HÀNH CHÍNH QUẢN TRỊ"),
            "noi_nhan": inputs.get("noi_nhan", "Ban Giám đốc"),
            "so_ky_hieu": so_ky_hieu,
            "dia_danh": inputs.get("dia_danh", "Hà Nội"),
            "ngay_bao_cao": _ngay_tieng_viet(date.today()),
            "trich_yeu": inputs.get(
                "trich_yeu", f"V/v tổng hợp tình hình quân số và trang thiết bị {period_label}"),
            "chuc_vu_ky": inputs.get("chuc_vu_ky", "TRƯỞNG PHÒNG"),
            "nguoi_ky": inputs.get("nguoi_ky", ""),
            "can_cu": inputs.get("can_cu", ""),
        },
        sections=[
            RenderedSection(
                id=s["id"], title=s["title"], paragraphs=s["paragraphs"],
                table=RenderedTable(**s["table"]) if s["table"] else None,
                image=s.get("image"), image_caption=s.get("image_caption", ""),
            )
            for s in state.get("sections", [])
        ],
    )

    stem = re.sub(r"[^A-Za-z0-9_.-]", "_", f"BC_TONGHOP_{params['ky']}")
    output_path = Path(cfg.output_dir) / f"{stem}.docx"
    path = await anyio.to_thread.run_sync(
        lambda: build_docx(payload, output_path, template_file or None)
    )
    return {"output_path": str(path), "so_ky_hieu": so_ky_hieu}


async def register_node(state: dict[str, Any]) -> dict[str, Any]:
    output_path = state.get("output_path")
    if not output_path:
        return {}

    from app.db.models import VanBan

    params = state["params"]
    ma_van_ban = state.get("so_ky_hieu") or f"{params['thang']:02d}/BC-HCQT"
    try:
        async with session_scope() as session:
            await VanBanRepository(session).upsert(VanBan(
                ma_van_ban=ma_van_ban,
                ten_van_ban=f"Báo cáo tổng hợp quân số và trang thiết bị "
                            f"tháng {params['thang']}/{params['nam']}",
                loai_van_ban="bao_cao_tong_hop",
                noi_gui="Phòng Hành chính nhân sự",
                noi_nhan="Ban Giám đốc",
                mo_ta=f"Tổng hợp kỳ {params['ky']}",
                ngay_van_ban=date.today(),
                file_path=output_path, dang_file="docx",
            ))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Không ghi được sổ văn bản: %s", exc)
        return {}
    return {"registered_as": ma_van_ban}
