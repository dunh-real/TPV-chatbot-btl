"""Các node của workflow 3: soạn văn bản theo mẫu.

    yêu cầu ─> trích tham số ─> chọn mẫu ─> lấy số liệu (SQL theo khai báo của mẫu)
                                        └─> tra quy định (RAG, chỉ lấy căn cứ)
              ─> dựng từng mục ─> đối chiếu số liệu ─> xuất DOCX

Ranh giới quan trọng nhất: **số liệu do SQL lấy, LLM chỉ viết văn quanh số liệu đó.**
Mục loại `data`/`table` hoàn toàn do code dựng; chỉ phần nhận xét mới gọi model, và
mọi con số nó viết ra đều bị `app.documents.verify` đối chiếu lại với dữ liệu gốc.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from app.agents import references
from app.agents.history import format_history
from app.agents.prompts import (
    DRAFT_PARAMS_SYSTEM,
    DRAFT_PARAMS_USER,
    DRAFT_SECTION_SYSTEM,
    DRAFT_SECTION_USER,
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
from app.db.erp_repository import AS_OF_NOTE, ErpTaiNguyenRepository
from app.services import storage
from app.db.erp_session import erp_session_scope
from app.services import errors
from app.agents.nodes.report import next_so_ky_hieu
from app.db.repository import TemplateRepository, VanBanRepository
from app.db.session import session_scope
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

COLUMN_LABELS = {
    "ten_thiet_bi": "Tên thiết bị",
    "so_luong": "Số lượng",
    "tinh_trang": "Tình trạng",
    "cap_nhat_cuoi": "Cập nhật gần nhất (ERP)",
    "nhan_su": "Nhân sự",
    "nhan_su_kiem_ke": "Ngày kiểm kê",
}
MONTHS_VI = "tháng"


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
    async with erp_session_scope() as session:
        units = await ErpTaiNguyenRepository(session).list_don_vi()
    unit_lines = "\n".join(f"- {u['ma_don_vi']}: {u['ten_don_vi']}" for u in units)

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
    valid_units = {u["ma_don_vi"] for u in units}
    ma_don_vi = params.get("ma_don_vi") if params.get("ma_don_vi") in valid_units else None
    ma_don_vi = ma_don_vi or state.get("ma_don_vi")

    missing: list[str] = []
    if not ma_don_vi:
        missing.append("ma_don_vi")

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

    async with session_scope() as session:
        repo = TemplateRepository(session)
        catalog = await repo.catalog()
        if not catalog:
            return {"error": "Chưa có mẫu báo cáo nào trong CSDL"}

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
            # Thà hỏi lại còn hơn soạn nhầm loại báo cáo.
            return {"missing_input": ["ma_template"],
                    "template_candidates": catalog,
                    "error": "Không xác định được mẫu báo cáo phù hợp"}

        template = await repo.get(ma_template)
        return {
            "template": {
                "ma_template": template.ma_template,
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

    ma_don_vi = state["ma_don_vi"]
    ky = state["params"].get("ky")
    notes: list[str] = []

    try:
        async with erp_session_scope() as session:
            tai_nguyen = await ErpTaiNguyenRepository(session).get_tai_nguyen(ma_don_vi, ky)
    except Exception as exc:  # noqa: BLE001 - CSDL hỏng thì dừng gọn, đừng ném traceback lên chat
        return {"error": errors.as_error(exc, "lấy số liệu đơn vị từ CSDL nghiệp vụ")}

    if tai_nguyen is None:
        return {"error": f"Không tìm thấy đơn vị {ma_don_vi}"}

    # ERP không chốt số theo kỳ, nên số của kỳ cũ là hiện trạng suy ngược. Người
    # ký văn bản cần biết điều này, vì đọc lại sau vài tháng có thể ra số khác.
    if ky:
        notes.append(AS_OF_NOTE)
    return {"data": tai_nguyen.as_dict(), "data_notes": notes}


# --------------------------------------------------------------------------- #
# 4. Tra quy định làm căn cứ
# --------------------------------------------------------------------------- #
async def search_regulations_node(state: dict[str, Any]) -> dict[str, Any]:
    """Chỉ lấy 2-3 đoạn làm căn cứ. Không tìm được thì để trống.

    Số hiệu văn bản bịa là lỗi nguy hiểm nhất trong văn bản hành chính, nên thà
    không có phần "Căn cứ ..." còn hơn có một số hiệu không tồn tại.
    """
    if state.get("error") or state.get("missing_input"):
        return {}

    query = f"quy định về {state['params']['loai_bao_cao']}"
    try:
        from app.rag.retrieval import get_retriever

        result = await get_retriever().retrieve(query=query, top_n=3)
    except Exception as exc:  # noqa: BLE001 - không có Qdrant thì bỏ qua căn cứ
        logger.info("Không tra được quy định (%s) - bỏ phần căn cứ", exc)
        return {"regulations": []}

    return {
        "regulations": [
            {"doc_title": chunk.doc_title, "section": chunk.section,
             "text": chunk.text[:600], "score": round(chunk.rerank_score, 3)}
            for chunk in result.chunks
        ]
    }


# --------------------------------------------------------------------------- #
# 5. Dựng từng mục
# --------------------------------------------------------------------------- #
ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _format_value(value: Any) -> str:
    """Ngày ISO trong CSDL phải ra dạng người Việt đọc: 2026-08-30 -> 30/8/2026."""
    text = str(value)
    if (match := ISO_DATE_RE.match(text)) is not None:
        year, month, day = match.groups()
        return f"{int(day)}/{int(month)}/{year}"
    return text


def _facts_paragraph(section_spec: dict[str, Any], data: dict[str, Any]) -> str:
    """Câu liệt kê số liệu do CODE dựng - không qua LLM."""
    parts: list[str] = []
    for field_name in section_spec.get("fields", []):
        value = data.get(field_name)
        if value is None:
            continue
        label = COLUMN_LABELS.get(field_name, field_name.replace("_", " "))
        parts.append(f"{label}: {_format_value(value)}")
    return "; ".join(parts) + "." if parts else ""


def _build_table(section_spec: dict[str, Any], data: dict[str, Any]) -> RenderedTable | None:
    rows_source = data.get(section_spec.get("query", ""), [])
    if not isinstance(rows_source, list) or not rows_source:
        return None
    columns = section_spec.get("columns") or list(rows_source[0].keys())
    return RenderedTable(
        columns=[COLUMN_LABELS.get(c, c.replace("_", " ").title()) for c in columns],
        rows=[[_format_value(row.get(c, "")) for c in columns] for row in rows_source],
    )


# Trường nhận dạng: mục nào cũng cần để gọi đúng tên đơn vị và đúng kỳ.
IDENTITY_FIELDS = {"ma_don_vi", "ten_don_vi", "ky", "ghi_chu"}

# Trường tóm tắt: con số duy nhất về toàn bộ tài nguyên đơn vị, dùng được ở mọi
# mục. Danh sách chi tiết (`thiet_bi`) thì KHÔNG - xem `_section_data`.
SUMMARY_FIELDS = {"nhan_su", "nhan_su_kiem_ke", "tong_so_thiet_bi", "so_loai_thiet_bi",
                  "so_loai_can_bao_duong"}


def _section_data(spec: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    """Phần số liệu mục này ĐƯỢC NHÌN THẤY - cũng chính là phạm vi đối chiếu số.

    Trước đây mọi mục đều nhận nguyên khối dữ liệu đơn vị, nên phạm vi số hợp lệ
    rộng bằng cả khối: model viết "5 loại đang cần bảo dưỡng" và van chắn cho qua
    chỉ vì số 5 tình cờ là số chủng loại. Thu hẹp lại thì cùng câu đó bị bắt.

    Workflow 4 đã làm đúng như vậy từ đầu (`source_data` theo từng mục); đây là
    phần workflow 3 còn nợ.
    """
    payload = {k: v for k, v in data.items() if k in IDENTITY_FIELDS and v not in (None, "")}

    kind = spec.get("type", "llm")
    if kind == "data":
        for field_name in spec.get("fields", []):
            if data.get(field_name) is not None:
                payload[field_name] = data[field_name]
    elif kind == "table":
        # Mục bảng: bảng do code dựng, phần chữ chỉ được nói về con số tổng của
        # chính bảng đó - không đi vào từng dòng.
        key = spec.get("query", "")
        if isinstance(data.get(key), list):
            payload[f"so_dong_{key}"] = len(data[key])
        payload.update({k: v for k, v in data.items()
                        if k in SUMMARY_FIELDS and v is not None})
    else:
        # Mục nhận xét/kiến nghị: chỉ số tổng, không có danh sách chi tiết.
        payload.update({k: v for k, v in data.items()
                        if k in SUMMARY_FIELDS and v is not None})
    return payload


async def render_sections_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or state.get("missing_input"):
        return {}

    template = state["template"]
    data = state["data"]
    specs = template["fields"].get("sections", [])
    params = state["params"]
    period = f"tháng {params['thang']}/{params['nam']}" if params.get("ky") else "hiện tại"

    regulations = state.get("regulations") or []
    reg_refs = references.from_regulations(regulations[:2])

    llm = get_llm()
    sections: list[dict[str, Any]] = []

    for spec in specs:
        section_id = spec.get("id", f"s{len(sections)}")
        title = spec.get("title", "")
        kind = spec.get("type", "llm")
        paragraphs: list[str] = []
        table = None

        # --- phần số liệu: code dựng, không đụng tới LLM ---
        if kind == "data":
            if (facts := _facts_paragraph(spec, data)):
                paragraphs.append(facts)
        elif kind == "table":
            table = _build_table(spec, data)

        # --- phần nhận xét: LLM viết, chỉ được dùng số liệu đã cho ---
        narrative = spec.get("narrative")
        section_cited: list[str] = []
        section_refs: list[references.Reference] = []
        # Phạm vi số liệu của riêng mục này: vừa là thứ gửi cho model, vừa là
        # thứ bước đối chiếu số dùng làm tập hợp lệ. Hai thứ đó PHẢI bằng nhau,
        # nếu không van chắn lại rộng hơn thứ model nhìn thấy.
        section_data = _section_data(spec, data)
        data_refs = references.from_data(section_data)
        all_refs = references.merge(data_refs, reg_refs)
        reg_block = ""
        if len(all_refs) > len(data_refs):
            reg_block = ("\n\nQUY ĐỊNH LIÊN QUAN (chỉ để tham khảo cách diễn đạt):\n"
                         + references.render(all_refs[len(data_refs):]))
        if narrative:
            try:
                result = await llm.chat_json(
                    [
                        {"role": "system", "content": DRAFT_SECTION_SYSTEM},
                        {"role": "user", "content": DRAFT_SECTION_USER.format(
                            report_title=template["ten_bao_cao"],
                            unit_name=data.get("ten_don_vi", ""),
                            period=period,
                            section_title=title,
                            narrative=narrative,
                            data=references.render(all_refs[:len(data_refs)]),
                            regulations=reg_block,
                        )},
                    ],
                    temperature=0.2, max_tokens=900, thinking=False,
                )
                marked = [str(p).strip() for p in (result.get("paragraphs") or []) if str(p).strip()]
                # Bản sạch vào DOCX và vào bước đối chiếu số; bản có marker cho UI.
                written = [references.strip_markers(p) for p in marked]
                written = [p for p in written if p]
                section_cited, section_refs = (
                    references.renumber(marked, all_refs)
                    if any(references.MARKER_RE.search(p) for p in marked)
                    else (marked, []))
                paragraphs.extend(written)
            except (LLMError, Exception) as exc:  # noqa: BLE001
                logger.warning("Không viết được mục %s: %s", section_id, exc)

        sections.append({"id": section_id, "title": title, "kind": kind,
                         "paragraphs": paragraphs,
                         "table": {"columns": table.columns, "rows": table.rows} if table else None,
                         "llm_written": [p for p in paragraphs if narrative and p not in
                                         ([_facts_paragraph(spec, data)] if kind == "data" else [])],
                         "llm_written_cited": section_cited,
                         "refs": section_refs,
                         "source_data": section_data})

    return {"sections": sections}


# --------------------------------------------------------------------------- #
# 6. Đối chiếu số liệu + kiểm tra đủ mục
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

    expected = {s.get("id") for s in state["template"]["fields"].get("sections", [])}
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
    today = date.today()

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
    for key, value in inputs.items():
        meta.setdefault(key, str(value))

    period = f"tháng {params['thang']}/{params['nam']}" if params.get("ky") else "hiện tại"
    meta.setdefault("dia_danh", inputs.get("dia_danh", "Hà Nội"))
    meta.setdefault("ngay_bao_cao", _ngay_tieng_viet(today))
    meta.setdefault("noi_gui", str(data.get("ten_don_vi", "")).upper())
    meta["trich_yeu"] = inputs.get(
        "trich_yeu", f"V/v {template['ten_bao_cao'].lower()} {period}"
    )
    # Số ký hiệu do `export_node` cấp từ sổ văn bản. Còn "01" ở đây là đường lùi
    # cho lúc chưa qua bước xuất file (xem trước, test) - không phải số thật.
    meta.setdefault("so_ky_hieu", inputs.get("so_ky_hieu") or state.get("so_ky_hieu")
                    or f"01/BC-{state.get('ma_don_vi', '')}")
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

    # Đánh số trong sổ thay vì gán cứng "01": báo cáo thứ hai của cùng một đơn vị
    # trong năm mà cũng mang số 01 thì sổ văn bản có hai văn bản trùng số, và bản
    # sau ghi đè bản trước lúc vào sổ.
    inputs = state.get("inputs", {})
    ma_don_vi = state.get("ma_don_vi", "")
    try:
        async with session_scope() as session:
            # Không có đơn vị (nhánh soạn từ tài liệu) thì ký hiệu là "BC" trần.
            # Ghép thẳng thành "BC-" cho ra số cụt đuôi kiểu "01/BC-" ngay dòng
            # đầu văn bản trình ký.
            ky_hieu = f"BC-{ma_don_vi}" if ma_don_vi else "BC"
            so_ky_hieu = inputs.get("so_ky_hieu") or await next_so_ky_hieu(
                session, state["params"].get("nam") or date.today().year, ky_hieu)
    except Exception as exc:  # noqa: BLE001 - sổ hỏng không được chặn việc xuất file
        logger.warning("Không cấp được số ký hiệu từ sổ văn bản: %s", exc)
        so_ky_hieu = inputs.get("so_ky_hieu") or (
            f"01/BC-{ma_don_vi}" if ma_don_vi else "01/BC")
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
    stem = f"{template['ma_template']}_{state['ma_don_vi']}_{suffix}"
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
async def register_node(state: dict[str, Any]) -> dict[str, Any]:
    """Ghi báo cáo vừa sinh vào sổ văn bản để tra cứu lại sau."""
    output_path = state.get("output_path")
    if not output_path:
        return {}

    from app.db.models import VanBan

    meta = _resolve_meta(state["template"], state)
    ma_van_ban = meta.get("so_ky_hieu") or Path(output_path).stem
    try:
        async with session_scope() as session:
            await VanBanRepository(session).upsert(VanBan(
                ma_van_ban=ma_van_ban,
                ten_van_ban=f"{state['template']['ten_bao_cao']} - {state['data'].get('ten_don_vi', '')}",
                loai_van_ban="bao_cao_di",
                # Mã đơn vị và kỳ ghi thẳng vào sổ: mục "đơn vị nào đã gửi báo
                # cáo kỳ này" khớp bằng hai trường này chứ không dò tên nữa.
                ma_don_vi=state.get("ma_don_vi") or None,
                ky=state["params"].get("ky"),
                noi_gui=str(state["data"].get("ten_don_vi", "")),
                noi_nhan=meta.get("noi_nhan", ""),
                mo_ta=state["params"].get("loai_bao_cao", ""),
                ngay_van_ban=date.today(),
                file_path=output_path,
                dang_file="docx",
            ))
    except Exception as exc:  # noqa: BLE001 - ghi sổ hỏng không làm mất file đã xuất
        logger.warning("Không ghi được sổ văn bản: %s", exc)
        return {}
    return {"registered_as": ma_van_ban}
