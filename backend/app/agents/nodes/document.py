"""Các node của workflow 2: xử lý văn bản tự động.

Cả ba nhánh LLM ở đây đều gọi với `thinking=False`. Đo trên cùng một bản thảo: bật
suy luận thì bước soát chữ nghĩa tìm được 0 lỗi, tắt thì tìm được 3 - trong đó có
lỗi thật ("Chức danh | Mức" thiếu chữ "lương"). Soi lỗi chi tiết cần bám sát mặt
chữ, còn suy luận dài khiến model tự nói mình ra khỏi những phát hiện nhỏ.

                       ┌─> rule_check   (tất định: dàn ý, đánh số, trình bày)
    parse ─> dàn ý ────┼─> llm_review   (chữ nghĩa, theo lô, có verify)
                       ├─> classify     (định tuyến, dùng danh mục phòng ban)
                       └─> tasks        (tóm tắt + phân rã nhiệm vụ)
                                         └─> assemble -> ReviewResult

Tài liệu đưa vào có thể thuộc bất kỳ loại nào - rule engine chỉ soi cách tổ chức
và cách trình bày, không đòi hỏi tệp phải theo mẫu văn bản nào.

Ba nhánh LLM chạy song song và nhận ba gói ngữ cảnh khác nhau - xem chú thích ở
từng node. Không nhánh nào nhận cả file kèm câu hỏi chung chung.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from app.agents import references
from app.agents.prompts import (
    DOC_CLASSIFY_SYSTEM,
    DOC_CLASSIFY_USER,
    DOC_REVIEW_SYSTEM,
    DOC_REVIEW_USER,
    DOC_TASKS_SYSTEM,
    DOC_TASKS_USER,
)
from app.core.config import get_settings
from app.documents.outline import build_outline
from app.documents.parser import parse_document
from app.documents.rules import RuleFinding, get_rule_engine
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"error", "warning"}
VALID_TYPES = {"spelling", "grammar", "wording", "logic", "missing"}


def _normalize(text: str) -> str:
    """So khớp bỏ qua khác biệt khoảng trắng - LLM hay đổi xuống dòng thành cách."""
    return " ".join(text.split()).lower()


# --------------------------------------------------------------------------- #
# 1. Parse + dựng dàn ý
# --------------------------------------------------------------------------- #
async def parse_node(state: dict[str, Any]) -> dict[str, Any]:
    import anyio

    path = state["file_path"]
    try:
        structure = await anyio.to_thread.run_sync(lambda: parse_document(path))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Không parse được %s", path)
        return {"error": f"Không đọc được tài liệu: {exc}"}

    # Dàn ý dựng một lần ở đây rồi dùng lại cho cả rule engine lẫn phần trả về
    # giao diện: hai nơi đọc cùng một cách hiểu về tài liệu.
    outline = build_outline(structure)
    return {
        "structure": structure,
        "outline": outline,
        "trace": {**state.get("trace", {}),
                  "source_format": structure.source_format,
                  "block_count": len(structure.blocks),
                  "headings": len(outline.headings)},
    }


# --------------------------------------------------------------------------- #
# 2. Rule engine - cấu trúc và trình bày, không LLM
# --------------------------------------------------------------------------- #
async def rule_check_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or "structure" not in state:
        return {}
    try:
        engine = get_rule_engine(state.get("rule_set", ""))
    except FileNotFoundError as exc:
        logger.warning("%s - dùng bộ tiêu chí mặc định", exc)
        engine = get_rule_engine()
    return {"rule_result": engine.check(state["structure"])}


# --------------------------------------------------------------------------- #
# 3. LLM soát chữ nghĩa - chia lô, có xác minh trích dẫn
# --------------------------------------------------------------------------- #
def build_review_batches(blocks, max_chars: int = 2400) -> list[list]:
    """Gom các khối nội dung thành lô vừa ngữ cảnh.

    Cả tài liệu trong một lần gọi thì model bỏ sót lỗi ở giữa và không trả được
    địa chỉ lỗi; chia lô nhỏ cho phép chạy song song và quy lỗi về đúng đoạn.
    """
    batches: list[list] = []
    current: list = []
    size = 0
    for block in blocks:
        length = len(block.text)
        if current and size + length > max_chars:
            batches.append(current)
            current, size = [], 0
        current.append(block)
        size += length
    if current:
        batches.append(current)
    return batches


def verify_findings(raw_findings: list[dict[str, Any]], blocks) -> list[dict[str, Any]]:
    """Giữ lại phát hiện trích dẫn được nguyên văn; phần còn lại là bịa.

    Đây là van chặn ảo giác của nhánh LLM: mỗi lỗi phải chỉ ra được chuỗi ký tự
    thật trong văn bản. Nếu LLM ghi sai block_id nhưng trích đúng, ta sửa lại id.
    """
    by_id = {block.id: block.text for block in blocks}
    verified: list[dict[str, Any]] = []

    for finding in raw_findings:
        quote = str(finding.get("quote", "")).strip()
        if not quote:
            continue

        block_id = str(finding.get("block_id", "")).strip()
        target = block_id if block_id in by_id and _normalize(quote) in _normalize(by_id[block_id]) else None
        if target is None:
            target = next(
                (bid for bid, text in by_id.items() if _normalize(quote) in _normalize(text)),
                None,
            )
        if target is None:
            logger.debug("Bỏ phát hiện không trích dẫn được: %r", quote[:60])
            continue

        finding_type = str(finding.get("type", "wording"))
        severity = str(finding.get("severity", "warning"))
        verified.append({
            "block_id": target,
            "type": finding_type if finding_type in VALID_TYPES else "wording",
            "quote": quote,
            "suggest": str(finding.get("suggest", "")).strip(),
            "message": str(finding.get("message", "")).strip(),
            "severity": severity if severity in VALID_SEVERITIES else "warning",
        })
    return verified


async def llm_review_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or "structure" not in state:
        return {}

    outline = state["outline"]
    blocks = outline.blocks
    if not blocks:
        return {"llm_findings": [], "trace": {**state.get("trace", {}), "review_batches": 0}}

    cfg = get_settings()
    llm = get_llm()
    batches = build_review_batches(blocks)
    tieu_de = outline.title.text if outline.title else (state.get("file_name") or "(không rõ)")

    async def review(batch) -> list[dict[str, Any]]:
        rendered = "\n\n".join(f"[{b.id}] {b.text}" for b in batch)
        try:
            data = await llm.chat_json(
                [
                    {"role": "system", "content": DOC_REVIEW_SYSTEM},
                    {"role": "user", "content": DOC_REVIEW_USER.format(
                        title=tieu_de, blocks=rendered)},
                ],
                model=cfg.utility_model, temperature=0.0, max_tokens=1200,
                thinking=False,
            )
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.warning("Soát lô %s thất bại: %s", [b.id for b in batch], exc)
            return []
        return verify_findings(data.get("findings") or [], batch)

    started = time.perf_counter()
    results = await asyncio.gather(*(review(batch) for batch in batches))
    findings = [item for group in results for item in group]

    return {
        "llm_findings": findings,
        "trace": {**state.get("trace", {}),
                  "review_batches": len(batches),
                  "review_ms": round((time.perf_counter() - started) * 1000, 1)},
    }


# --------------------------------------------------------------------------- #
# 4. Phân loại + định tuyến
# --------------------------------------------------------------------------- #
def format_departments(catalog: list[dict[str, str]]) -> str:
    return "\n".join(
        f"- {item['ma_phong_ban']} | {item['ten_phong_ban']}: {item['mo_ta']}"
        for item in catalog
    )


def _head_content(structure, limit: int = 2500) -> str:
    """Phần đầu văn bản - đủ để phân loại, không cần cả file."""
    parts: list[str] = []
    total = 0
    for block in structure.blocks:
        if block.is_empty:
            continue
        parts.append(block.text)
        total += len(block.text)
        if total >= limit:
            break
    return "\n".join(parts)


async def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or "structure" not in state:
        return {}

    outline = state["outline"]

    try:
        data = await get_llm().chat_json(
            [
                {"role": "system", "content": DOC_CLASSIFY_SYSTEM},
                {"role": "user", "content": DOC_CLASSIFY_USER.format(
                    title=outline.title.text if outline.title else "(không có)",
                    noi_gui=state.get("noi_gui") or "(không rõ)",
                    content=_head_content(state["structure"]))},
            ],
            temperature=0.1, max_tokens=600, thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Phân loại thất bại: %s", exc)
        return {"classification": None}

    # Phân công phòng ban nằm ở `tasks_node` - một nguồn duy nhất, tránh hai lời
    # gọi LLM cho cùng một câu hỏi rồi trả lời lệch nhau.
    return {
        "classification": {
            "document_type": str(data.get("document_type") or "khac"),
            "topic": str(data.get("topic") or ""),
            "confidence": float(data.get("confidence") or 0.0),
            "reason": str(data.get("reason") or ""),
        }
    }


# --------------------------------------------------------------------------- #
# 5. Tóm tắt + phân rã nhiệm vụ
# --------------------------------------------------------------------------- #
def _numbered_blocks(structure, limit: int = 6000) -> tuple[str, list[references.Reference]]:
    """Văn bản đánh số từng khối, kèm danh sách tham chiếu tương ứng.

    Model đọc số ở đây rồi trích lại đúng số đó, nên UI bấm vào [3] là mở được
    đúng khối P03 trong văn bản gốc.
    """
    chosen, total = [], 0
    for block in structure.blocks:
        if block.is_empty:
            continue
        chosen.append(block)
        total += len(block.text)
        if total >= limit:
            break
    refs = references.from_blocks(chosen)
    return references.render(refs), refs


async def tasks_node(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("error") or "structure" not in state:
        return {}

    catalog = state.get("departments") or []
    structure = state["structure"]
    content, refs = _numbered_blocks(structure)
    try:
        data = await get_llm().chat_json(
            [
                {"role": "system", "content": DOC_TASKS_SYSTEM.format(
                    departments=format_departments(catalog) if catalog else "(không có)")},
                {"role": "user", "content": DOC_TASKS_USER.format(content=content)},
            ],
            temperature=0.2, max_tokens=1600, thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001
        logger.warning("Tóm tắt/phân rã nhiệm vụ thất bại: %s", exc)
        return {"summary": "", "tasks": []}

    by_code = {item["ma_phong_ban"]: item["ten_phong_ban"] for item in catalog}

    # Nơi nhận ngoài danh mục (Ban Giám đốc, cơ quan cấp trên) KHÔNG bị vứt: văn
    # bản có giao việc cho họ thì người đọc cần thấy. Chỉ đánh dấu là ngoài danh
    # mục để bên gọi biết không có email/mã để chuyển tự động.
    tasks: list[dict[str, Any]] = []
    for raw in data.get("tasks") or []:
        code = str(raw.get("department") or "").strip()
        ten = str(raw.get("department_name") or "").strip()
        in_catalog = code in by_code
        if code and not in_catalog:
            logger.info("Nơi nhận ngoài danh mục: %r (%s)", code, ten or "không rõ tên")
            code = ""
        # Thứ tự ưu tiên tên: danh mục -> tên model đưa -> chính cái mã. Chỉ bỏ
        # khi không còn gì để gọi tên nơi nhận - mất một nơi nhận tệ hơn xấu chữ.
        name = by_code.get(code, "") or ten or str(raw.get("department") or "").strip()
        if not name:
            logger.debug("Bỏ mục không xác định được nơi nhận: %r", raw)
            continue
        tasks.append({
            "department": code,
            "department_name": name,
            "in_catalog": in_catalog,
            "task": str(raw.get("task") or "").strip(),
            "data_needed": [str(x) for x in (raw.get("data_needed") or [])],
            "deadline": str(raw.get("deadline") or "") or None,
        })

    # Tóm tắt và mọi nhiệm vụ dùng CHUNG một dãy số: [2] ở phần tóm tắt và [2] ở
    # một nhiệm vụ phía dưới trỏ cùng một khối, nên đọc liền mạch không rối.
    summary = str(data.get("summary") or "").strip()
    viet_lai, da_dung = references.renumber(
        [summary] + [t["task"] for t in tasks], refs)
    summary, task_texts = viet_lai[0], viet_lai[1:]
    for task, text in zip(tasks, task_texts, strict=True):
        task["task"] = text
        ids = {int(n) for n in references.MARKER_RE.findall(text)}
        task["refs"] = [r for r in da_dung if r["id"] in ids]

    return {
        "summary": summary,
        "summary_refs": [r for r in da_dung
                         if r["id"] in {int(n) for n in references.MARKER_RE.findall(summary)}],
        "all_refs": da_dung,
        "deadline": str(data.get("deadline") or "") or None,
        "tasks": tasks,
    }


# --------------------------------------------------------------------------- #
# 6. Gộp kết quả
# --------------------------------------------------------------------------- #
def _rule_findings_payload(findings: list[RuleFinding]) -> list[dict[str, Any]]:
    return [f.as_dict() for f in findings]


# Dàn ý trả về giao diện, cắt bớt cho khỏi dài: người đọc cần thấy tài liệu được
# chia mục thế nào, không cần từng mục con của một tài liệu 200 trang.
MAX_OUTLINE_ITEMS = 40


def _outline_payload(outline: Any) -> list[dict[str, Any]]:
    if outline is None:
        return []
    return [{"block_id": h.block_id, "level": h.level, "text": h.text[:120]}
            for h in outline.headings[:MAX_OUTLINE_ITEMS]]


async def assemble_node(state: dict[str, Any]) -> dict[str, Any]:
    structure = state.get("structure")
    outline = state.get("outline")
    rule_result = state.get("rule_result")
    llm_findings = state.get("llm_findings") or []

    rule_payload: dict[str, Any] = {"status": "skipped", "findings": [], "passed": [],
                                    "skipped": [], "reason": state.get("error", "")}
    if rule_result is not None:
        rule_payload = {
            "status": rule_result.status,
            "findings": _rule_findings_payload(rule_result.findings),
            "passed": rule_result.passed,
            "skipped": rule_result.skipped,
            "reason": rule_result.reason,
            "rule_set": rule_result.rule_set,
        }

    # Gom lỗi chữ nghĩa theo khối: người đọc soát từng đoạn một, không đọc một
    # danh sách phẳng rồi tự nhặt xem lỗi nào thuộc đoạn nào.
    by_block: dict[str, list[dict[str, Any]]] = {}
    for finding in llm_findings:
        by_block.setdefault(finding["block_id"], []).append(finding)

    errors = (rule_result.error_count if rule_result else 0) + sum(
        1 for f in llm_findings if f["severity"] == "error"
    )
    warnings = (rule_result.warning_count if rule_result else 0) + sum(
        1 for f in llm_findings if f["severity"] == "warning"
    )

    return {
        "result": {
            "document": {
                "source_format": structure.source_format if structure else "unknown",
                "block_count": len(structure.blocks) if structure else 0,
                "page_count": structure.page_count if structure else 0,
                "has_format_info": structure.has_format_info if structure else False,
                "title": outline.title.text if outline and outline.title else "",
                "outline": _outline_payload(outline),
            },
            "rule_check": rule_payload,
            "llm_review": by_block,
            "classification": state.get("classification"),
            "summary": state.get("summary", ""),
            "summary_refs": state.get("summary_refs", []),
            "all_refs": state.get("all_refs", []),
            "deadline": state.get("deadline"),
            "tasks": state.get("tasks", []),
            "totals": {"errors": errors, "warnings": warnings},
            "error": state.get("error", ""),
        }
    }
