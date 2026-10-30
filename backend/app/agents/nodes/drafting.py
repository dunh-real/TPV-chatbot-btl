"""Soạn Word theo mẫu; số liệu ERP lấy bằng discovery, bảng dựng từ QueryResult."""
from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.history import format_history
from app.agents.prompts import (
    DRAFT_PARAMS_SYSTEM,
    DRAFT_PARAMS_USER,
    DRAFT_TEMPLATE_SYSTEM,
)
from app.core.config import get_settings
from app.documents.docx_builder import (
    DocumentPayload,
    RenderedSection,
    RenderedTable,
    build_docx,
)
from app.documents.verify import check_numbers, collect_known_numbers
from app.services import storage
from app.services.erp_discovery import query_erp
from app.documents.query_result import query_sections
from app.services import errors
from app.documents.the_thuc import doc_ngay_ban_hanh, kiem_so_van_ban, ngay_ban_hanh_an_toan, so_nguoi_dung
from app.db.repository import TemplateRepository
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

def _ky(thang: int | None, nam: int | None) -> str | None:
    return f"{nam:04d}-{thang:02d}" if thang and nam else None


def _ngay_tieng_viet(value: date) -> str:
    return f"ngày {value.day:02d} tháng {value.month} năm {value.year}"


# --------------------------------------------------------------------------- #
# 1. Trích tham số từ yêu cầu
# --------------------------------------------------------------------------- #
async def extract_params_node(state: dict[str, Any]) -> dict[str, Any]:
    """Trích loại báo cáo, kỳ và đơn vị. Thiếu thì nêu rõ, không đoán bừa."""
    today = date.today()
    unit_lines = "Đơn vị do người dùng nêu; discovery sẽ tra mã và tên."

    params: dict[str, Any] = {}
    try:
        params = await get_llm().chat_json(
            [
                {"role": "system", "content": DRAFT_PARAMS_SYSTEM.format(
                    today=today.strftime("%d/%m/%Y"), units=unit_lines)},
                {"role": "user", "content": DRAFT_PARAMS_USER.format(
                    history=format_history(state.get("history")), request=state["request"])},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=400,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Không trích được tham số: %s", exc)

    thang = params.get("thang")
    nam = params.get("nam")
    # Năm không nêu thì suy năm hiện tại - ghi lại giả định để báo cho người dùng.
    assumptions: list[str] = []
    if thang and not nam:
        nam = today.year
        assumptions.append(f"Không nêu năm, hiểu là năm {nam}")

    # Đơn vị: ưu tiên người dùng nêu, sau đó mới tới đơn vị của tài khoản.
    ma_don_vi = params.get("ma_don_vi")
    ma_don_vi = ma_don_vi or state.get("ma_don_vi")

    missing: list[str] = []

    return {
        "params": {
            "loai_bao_cao": str(params.get("loai_bao_cao") or state["request"]),
            "thang": thang, "nam": nam,
            "ky": _ky(thang, nam),
            "ghi_chu": str(params.get("ghi_chu") or ""),
        },
        "ma_don_vi": ma_don_vi,
        "assumptions": assumptions,
        "missing_input": missing,
    }


# --------------------------------------------------------------------------- #
# 2. Chọn mẫu báo cáo
# --------------------------------------------------------------------------- #
async def select_template_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("missing_input"):
        return {}

    repo = TemplateRepository()
    catalog = await repo.catalog()
    if not catalog:
        return {"missing_input": ["Mẫu SQL và phiên bản đang công bố"]}

    rendered = "\n".join(
        f"- {t['ma_template']} | {t['ten_bao_cao']} ({t['loai_bao_cao']}): {t['mo_ta']}"
        for t in catalog
    )
    try:
        choice = await get_llm().chat_json(
            [
                {"role": "system", "content": DRAFT_TEMPLATE_SYSTEM.format(templates=rendered)},
                {"role": "user", "content": state["params"]["loai_bao_cao"]},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=300,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Chọn mẫu thất bại: %s", exc)
        choice = {}

    valid = {t["ma_template"] for t in catalog}
    ma_template = choice.get("ma_template") if choice.get("ma_template") in valid else None
    if ma_template is None:
        return {"missing_input": ["Chọn mã mẫu SQL phù hợp trong danh mục"]}

    template = await repo.get(ma_template)
    return {
        "template": {
            "ma_template": template.ma_template,
            "template_id": getattr(template, "ai_id", None),
            "template_version": getattr(template, "ai_version", None),
            "ten_bao_cao": template.ten_bao_cao,
            "file_path": template.file_path,
            "fields": template.fields,
        },
        "template_choice": {"confidence": float(choice.get("confidence") or 0.0),
                            "reason": str(choice.get("reason") or "")},
    }


# --------------------------------------------------------------------------- #
# 3. Lấy số liệu từ CSDL
# --------------------------------------------------------------------------- #
async def fetch_data_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("missing_input"):
        return {}
    question = state["request"]
    if state.get("ma_don_vi"):
        question += f"\nĐơn vị người dùng chọn: {state['ma_don_vi']}"
    history = format_history(state["history"]) if state.get("history") else ""
    if history:
        question += "\nLịch sử để hiểu yêu cầu tiếp nối:\n" + history
    try:
        result = await query_erp(question)
        if result.truncated:
            return {"error": "Kết quả bị cắt; hãy thu hẹp phạm vi hoặc yêu cầu truy vấn tổng hợp trước khi lập Word."}
        from app.documents.nhan_cot import gan_nhan_cot

        query = await gan_nhan_cot(result.as_dict(), state["request"])
        return {"data": {"queries": [query]}, "data_notes": result.warnings}
    except Exception as exc:
        return {"error": errors.as_error(exc, "truy vấn dữ liệu ERP")}


# --------------------------------------------------------------------------- #
# 4. Dựng từng mục
# --------------------------------------------------------------------------- #
async def render_sections_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("missing_input"):
        return {}
    from app.documents.nhan_cot import gan_nhan_moi_truy_van

    await gan_nhan_moi_truy_van(state["data"], state.get("request", ""))
    return {"sections": query_sections(state["data"]["queries"])}


# --------------------------------------------------------------------------- #
# 5. Đối chiếu số liệu + kiểm tra đủ mục
# --------------------------------------------------------------------------- #
async def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("missing_input"):
        return {"validation": {"status": "skipped", "issues": []}}

    data = state.get("data", {})
    # Kỳ báo cáo cũng là số hợp lệ (xuất hiện trong tiêu đề, câu mở đầu).
    params = state.get("params", {})
    period_numbers = {str(v) for v in (params.get("thang"), params.get("nam")) if v}

    issues: list[dict[str, Any]] = []
    checked_total = 0
    for section in state.get("sections", []):
        # Phạm vi đúng bằng số liệu đã đưa cho model ở MỤC NÀY. Lấy cả khối dữ
        # liệu đơn vị làm tập hợp lệ thì một khẳng định bịa vẫn lọt chỉ vì con số
        # trong đó tình cờ trùng một giá trị ở chỗ khác.
        known = collect_known_numbers(
            section.get("source_data", data)) | period_numbers
        checked_total = max(checked_total, len(known))
        for paragraph in section.get("llm_written", []):
            check = check_numbers(paragraph, known, section["id"])
            if not check.ok:
                issues.append({
                    "type": "unverified_number", "section": section["id"],
                    "numbers": check.unverified, "severity": "error",
                    "quote": paragraph[:160],
                })

    expected = set() if "queries" in data else {s.get("id") for s in state["template"]["fields"].get("sections", [])}
    rendered = {s["id"] for s in state.get("sections", []) if s["paragraphs"] or s["table"]}
    for missing in sorted(expected - rendered):
        issues.append({"type": "empty_section", "section": missing, "severity": "warning",
                       "numbers": [], "quote": ""})

    blocking = [i for i in issues if i["severity"] == "error"]
    return {
        "validation": {
            "status": "failed" if blocking else ("warning" if issues else "passed"),
            "issues": issues,
            "checked_numbers": checked_total,
        }
    }


# --------------------------------------------------------------------------- #
# 7. Xuất DOCX
# --------------------------------------------------------------------------- #
def _resolve_meta(template: dict[str, Any], state: dict[str, Any]) -> dict[str, str]:
    """Điền phần thể thức theo khai báo `source` của mẫu."""
    data = state.get("data", {})
    inputs = state.get("inputs", {})
    params = state.get("params", {})
    # Ngày ban hành người dùng nhập thay cho ngày lập (đã kiểm định dạng ở export_node).
    today = ngay_ban_hanh_an_toan(inputs)

    meta: dict[str, str] = {}
    for key, spec in (template["fields"].get("meta") or {}).items():
        source = spec.get("source") if isinstance(spec, dict) else "literal"
        if source == "literal":
            meta[key] = str(spec.get("value", ""))
        elif source == "today":
            meta[key] = _ngay_tieng_viet(today)
        elif source == "input":
            meta[key] = str(inputs.get(key, ""))
        elif source and source.startswith("unit."):
            meta[key] = str(data.get(source.split(".", 1)[1], ""))
        else:
            meta[key] = str(inputs.get(key, ""))

    # Trường người dùng nhập mà mẫu không khai báo vẫn phải được điền: mẫu .docx
    # có thể chứa {{chuc_vu_ky}} trong khi JSON của mẫu quên khai báo nó.
    # Ô để trống (giao diện gửi "so_ky_hieu": "") không được chiếm chỗ giá trị hệ thống tự cấp bên dưới.
    for key, value in inputs.items():
        if str(value or "").strip():
            meta.setdefault(key, str(value))

    period = f"tháng {params['thang']}/{params['nam']}" if params.get("ky") else "hiện tại"
    meta.setdefault("dia_danh", inputs.get("dia_danh", "Hà Nội"))
    meta.setdefault("ngay_bao_cao", _ngay_tieng_viet(today))
    meta.setdefault("noi_gui", str(data.get("ten_don_vi", "")).upper())
    meta["trich_yeu"] = inputs.get(
        "trich_yeu", state["request"][:200] if "queries" in data else f"V/v {template['ten_bao_cao'].lower()} {period}"
    )
    # Bản nháp chưa có số; ERP cấp số chính thức khi ban hành.
    meta["so_ky_hieu"] = inputs.get("so_ky_hieu") or state.get("so_ky_hieu") or ""
    if not meta.get("can_cu") and state.get("regulations"):
        meta["can_cu"] = f"Căn cứ {state['regulations'][0]['doc_title']};"
    return meta


async def export_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("missing_input"):
        return {}
    if state.get("validation", {}).get("status") == "failed":
        # Có số không truy được về dữ liệu gốc -> không xuất file.
        return {"output_path": ""}

    cfg = get_settings()
    template = state["template"]

    # Giữ số do ERP cấp nếu có; bản nháp được phép chưa có số.
    inputs = state.get("inputs", {})
    ma_don_vi = state.get("ma_don_vi", "")
    try:
        doc_ngay_ban_hanh(inputs)
    except ValueError as exc:
        return {"error": str(exc)}
    so_nhap = so_nguoi_dung(inputs)
    if (trung := await kiem_so_van_ban(so_nhap)):
        return {"error": trung}
    so_ky_hieu = so_nhap
    state = {**state, "so_ky_hieu": so_ky_hieu}

    payload = DocumentPayload(
        meta=_resolve_meta(template, state),
        sections=[
            RenderedSection(
                id=section["id"], title=section["title"],
                paragraphs=section["paragraphs"],
                table=RenderedTable(**section["table"]) if section["table"] else None,
            )
            for section in state.get("sections", [])
        ],
    )

    params = state["params"]
    suffix = params.get("ky") or date.today().strftime("%Y%m%d")
    stem = f"{template['ma_template']}_{state.get('ma_don_vi') or 'ERP'}_{suffix}"
    stem = storage.versioned_stem(re.sub(r"[^A-Za-z0-9_.-]", "_", stem))
    output_path = Path(cfg.output_dir) / f"{stem}.docx"

    import anyio

    path = await anyio.to_thread.run_sync(
        lambda: build_docx(payload, output_path, template.get("file_path") or None)
    )
    return {"output_path": str(path), "so_ky_hieu": so_ky_hieu}


# --------------------------------------------------------------------------- #
# 8. Ghi sổ văn bản
# --------------------------------------------------------------------------- #
