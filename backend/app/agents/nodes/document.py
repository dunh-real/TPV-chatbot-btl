"""Các node của workflow 2: xử lý văn bản tự động.

Cả ba nhánh LLM ở đây đều gọi với `thinking=False`. Đo trên cùng một bản thảo: bật
suy luận thì bước soát chữ nghĩa tìm được 0 lỗi, tắt thì tìm được 3 - trong đó có
lỗi thật ("Chức danh | Mức" thiếu chữ "lương"). Soi lỗi chi tiết cần bám sát mặt
chữ, còn suy luận dài khiến model tự nói mình ra khỏi những phát hiện nhỏ.

                       ┌─> rule_check   (tất định: dàn ý, thứ bậc mục, trình bày)
                       ├─> chinh_ta     (tất định: từ điển + dấu cách, dấu câu)
    parse ─> dàn ý ────┼─> llm_review   (ngữ pháp/diễn đạt/logic, theo lô, có verify)
                       ├─> classify     (định tuyến, dùng danh mục phòng ban)
                       └─> tasks        (tóm tắt + phân rã nhiệm vụ)
                                         └─> assemble -> ReviewResult

Hai nguồn phát hiện lỗi KHÔNG được trộn làm một khi trả về: `chinh_ta` đối chiếu
chuỗi nên sai là sai, còn `llm_review` là phán đoán. Mỗi phát hiện mang theo
`source` để giao diện vẽ khung liền nét cho loại thứ nhất và khung đứt nét cho
loại thứ hai - người ký cần biết chỗ nào máy chắc chắn, chỗ nào máy chỉ gợi ý.

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
    DOC_SPELL_SYSTEM,
    DOC_SPELL_USER,
    DOC_TASKS_SYSTEM,
    DOC_TASKS_USER,
)
from app.core.config import get_settings
from app.documents.chinh_ta import soat_chinh_ta
from app.documents.giao_viec import goi_y_mau
from app.documents.outline import build_outline
from app.documents.parser import parse_document
from app.documents.rules import RuleFinding, get_rule_engine
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"error", "warning"}

# Trích dài hơn thế thì cái khung ôm trọn cả đoạn, người đọc nhìn vào vẫn không
# biết chữ nào sai. Prompt đã dặn tối đa 20 từ; đây là chỗ thi hành, vì model để
# mặc thì rất hay chép cả đoạn rồi "gợi ý" viết lại nguyên đoạn.
MAX_QUOTE_CHARS = 240

# Ít nhất hai chữ cái liền nhau (kể cả chữ có dấu) thì mới coi là có từ.
_CO_TU = re.compile(r"[^\W\d_]{2,}")
# "spelling" vẫn nhận được: prompt đã bảo LLM đừng soát chính tả, nhưng thỉnh
# thoảng nó vẫn bắt được một lỗi ngoài từ điển. Giữ lại thì thà thừa còn hơn mất,
# và `source: "llm"` đã nói rõ đó là gợi ý chưa chắc chắn.
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
# 2b. Chính tả tất định - từ điển cặp sai/đúng, dấu cách, dấu câu, lặp từ
# --------------------------------------------------------------------------- #
async def chinh_ta_node(state: dict[str, Any]) -> dict[str, Any]:
    """Chạy trước LLM và chiếm hẳn phần chính tả.

    Chỉ là đối chiếu chuỗi nên tính bằng mili giây; đặt thành node riêng không
    phải để chạy song song cho nhanh mà để một nhánh hỏng không kéo nhánh kia
    theo, và để chỗ trả về của nó là một khoá state riêng như mọi nhánh khác.
    """
    if state.get("error") or "outline" not in state:
        return {}
    try:
        findings = soat_chinh_ta(state["outline"].blocks)
    except Exception as exc:  # noqa: BLE001 - mất phần chính tả còn hơn mất cả bản soát
        logger.warning("Soát chính tả thất bại: %s", exc)
        return {"typo_findings": []}
    return {"typo_findings": [f.as_dict() for f in findings]}


# --------------------------------------------------------------------------- #
# 3b. LLM soát chữ nghĩa - ngữ pháp, diễn đạt, logic (KHÔNG chính tả)
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


def _tim_span(quote: str, text: str) -> tuple[int, int] | None:
    """Vị trí ký tự của câu trích trong khối, bỏ qua khác biệt khoảng trắng.

    Giao diện khoanh vùng lỗi ngay trên tài liệu, mà muốn khoanh thì phải biết
    lỗi bắt đầu và kết thúc ở ký tự thứ mấy - câu trích không thôi thì giao diện
    phải tự dò lại, và dò trượt ở đúng chỗ LLM đổi xuống dòng thành dấu cách.
    Dung sai ở đây giống hệt dung sai của `_normalize`, nên tìm được span thì
    chắc chắn khớp với kết quả xác minh.
    """
    parts = quote.split()
    if not parts:
        return None
    pattern = re.compile(r"\s+".join(re.escape(p) for p in parts), re.IGNORECASE)
    match = pattern.search(text)
    return (match.start(), match.end()) if match else None


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
        if len(quote) > MAX_QUOTE_CHARS:
            logger.debug("Bỏ phát hiện trích quá dài (%d ký tự): %r…", len(quote), quote[:60])
            continue
        # Không nói được sai ở đâu mà cũng không đưa được cách sửa thì phát hiện đó
        # rỗng - hay gặp trên rác của khâu đọc file ("1516 11" ở chân trang).
        suggest = str(finding.get("suggest", "")).strip()
        if not str(finding.get("message", "")).strip() and not suggest:
            continue
        # Trích không có lấy một từ nào - chỉ số trang, số hiệu rời, dấu chấm điền
        # tay. Đó là rác của khâu đọc file, không phải lỗi của người soạn văn bản.
        if not _CO_TU.search(quote):
            logger.debug("Bỏ phát hiện trên mẩu không có chữ: %r", quote[:40])
            continue
        # Sửa xong vẫn y nguyên chữ cũ, chỉ khác khoảng trắng. Gặp liên tục trên
        # PDF: khâu đọc file làm mất dấu xuống dòng, model tưởng là câu chạy liền
        # rồi "sửa" bằng cách thêm xuống dòng vào. Đó là vá lỗi của máy đọc file,
        # không phải lỗi trong văn bản gốc.
        if suggest and _normalize(suggest) == _normalize(quote):
            logger.debug("Bỏ phát hiện chỉ đổi khoảng trắng: %r", quote[:40])
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
        span = _tim_span(quote, by_id[target])
        verified.append({
            "block_id": target,
            "type": finding_type if finding_type in VALID_TYPES else "wording",
            "quote": quote,
            "suggest": str(finding.get("suggest", "")).strip(),
            "message": str(finding.get("message", "")).strip(),
            "severity": severity if severity in VALID_SEVERITIES else "warning",
            # Span có thể None khi câu trích khớp kiểu "chuẩn hoá" mà không khớp
            # kiểu "khoảng trắng co giãn". Hiếm, và giao diện đã có đường lùi:
            # không có span thì tô cả khối thay vì tô đúng đoạn.
            "start": span[0] if span else None,
            "end": span[1] if span else None,
            "source": "llm",
        })
    return verified


# --------------------------------------------------------------------------- #
# 3a. LLM soát chính tả - pass riêng, không gộp với soát chữ nghĩa
# --------------------------------------------------------------------------- #
# Lô nhỏ hơn lô của nhánh chữ nghĩa: soi mặt chữ cần bám sát từng từ, đưa cả trang
# thì model đọc lướt và bỏ sót giữa đoạn.
SPELL_BATCH_CHARS = 1600


def loc_tach_roi(raw: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Bỏ những lỗi mà chính model khai là "tách rời đọc vẫn xuôi".

    Đây là van chặn chính của nhánh này. Gần như mọi lần báo oan chính tả tiếng
    Việt đều cùng một dạng: hai TỪ ĐÚNG đứng cạnh nhau trông y hệt một từ viết sai
    ("đơn vị cũng cố gắng", "hồ sơ xuất khẩu"). Prompt bắt model đọc lại câu theo
    nghĩa tách rời rồi khai kết quả vào `van_xuoi`; ở đây chỉ việc thi hành.

    Bắt model KHAI ra thay vì tự lọc trong đầu là có chủ ý: phải viết phép thử ra
    thì mới thật sự làm phép thử, và khi nó báo oan thì đọc `tach_roi` là biết ngay
    nó nghĩ sai ở đâu.
    """
    giu, bo = [], 0
    for f in raw:
        # Sửa thành y nguyên chữ cũ thì không phải lỗi, chỉ là model lỡ tay báo một
        # chỗ rồi không nghĩ ra sửa gì. Gặp thật: "khai trương" -> "khai trương".
        quote, suggest = str(f.get("quote", "")), str(f.get("suggest", ""))
        if suggest and _normalize(suggest) == _normalize(quote):
            logger.debug("Bỏ lỗi chính tả không đổi gì: %r", quote)
            bo += 1
            continue
        if bool(f.get("van_xuoi")):
            logger.debug("Bỏ lỗi chính tả tách rời vẫn xuôi: %r (%s)",
                         f.get("quote"), f.get("tach_roi"))
            bo += 1
            continue
        giu.append({**f, "type": "spelling", "severity": "error"})
    return giu, bo


async def chinh_ta_llm_node(state: dict[str, Any]) -> dict[str, Any]:
    """Soát chính tả bằng LLM, tách hẳn khỏi nhánh soát ngữ pháp/diễn đạt.

    Vì sao không nhét chung vào một prompt với ngữ pháp và logic: prompt chính tả
    phải dành gần hết chỗ cho bẫy "hai từ đứng cạnh nhau" cùng một bảng lỗi hay
    gặp; trộn vào một prompt lo năm việc thì phần đó bị loãng, mà đó đúng là phần
    quyết định bản soát có báo oan hay không.

    Mọi phát hiện vẫn phải qua `verify_findings`: trích được nguyên văn mới được
    giữ, và kèm luôn vị trí ký tự cho giao diện khoanh vùng.
    """
    if state.get("error") or "outline" not in state:
        return {}

    blocks = state["outline"].blocks
    if not blocks:
        return {"spell_findings": []}

    cfg = get_settings()
    llm = get_llm()
    batches = build_review_batches(blocks, max_chars=SPELL_BATCH_CHARS)
    bo_tong = 0

    async def soat(batch) -> list[dict[str, Any]]:
        nonlocal bo_tong
        rendered = "\n\n".join(f"[{b.id}] {b.text}" for b in batch)
        try:
            data = await llm.chat_json(
                [
                    {"role": "system", "content": DOC_SPELL_SYSTEM},
                    {"role": "user", "content": DOC_SPELL_USER.format(blocks=rendered)},
                ],
                model=cfg.utility_model, temperature=0.0, max_tokens=1200,
                thinking=False,
            )
        except (LLMError, Exception) as exc:  # noqa: BLE001
            logger.warning("Soát chính tả lô %s thất bại: %s", [b.id for b in batch], exc)
            return []
        giu, bo = loc_tach_roi(data.get("findings") or [])
        bo_tong += bo
        return verify_findings(giu, batch)

    started = time.perf_counter()
    results = await asyncio.gather(*(soat(batch) for batch in batches))
    findings = [item for group in results for item in group]

    logger.info("Soát chính tả: %d lỗi giữ lại, %d bỏ vì tách rời vẫn xuôi",
                len(findings), bo_tong)
    return {
        "spell_findings": findings,
        "trace": {**state.get("trace", {}),
                  "spell_batches": len(batches), "spell_bo_tach_roi": bo_tong,
                  "spell_ms": round((time.perf_counter() - started) * 1000, 1)},
    }


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

# Giao diện dựng lại tài liệu từ đúng những khối này. Chặn hai đầu để một tệp 300
# trang không biến phản hồi JSON thành vài chục MB; vượt ngưỡng thì cắt và NÓI RÕ
# là đã cắt, chứ không lặng lẽ trả về nửa tài liệu.
MAX_VIEW_BLOCKS = 1200
MAX_VIEW_CHARS = 400_000


def _outline_payload(outline: Any) -> list[dict[str, Any]]:
    if outline is None:
        return []
    return [{"block_id": h.block_id, "level": h.level, "text": h.text[:120]}
            for h in outline.headings[:MAX_OUTLINE_ITEMS]]


def _blocks_payload(structure: Any) -> tuple[list[dict[str, Any]], bool]:
    """Nội dung + định dạng thật của từng khối, đủ để giao diện dựng lại trang.

    Gửi cả những thuộc tính rule engine dùng để kết luận (phông, cỡ, canh lề):
    khoanh vùng một lỗi "lạc phông" mà trang dựng lại vẫn một phông thì người đọc
    không thấy được cái sai, và bản dựng lại mất luôn lý do tồn tại.

    `bbox` chỉ có ở PDF và chỉ để DỰNG LẠI, không để kết luận. PDF không lưu canh
    lề của đoạn (`alignment` luôn None), nên bản dựng lại đẩy hết quốc hiệu và tiêu
    đề về sát trái - trông không còn giống văn bản gốc. Có toạ độ thì giao diện suy
    ra được đoạn nào căn giữa, đoạn nào căn phải.

    Cố tình KHÔNG suy ngược vào `alignment` ở đây: rule engine đo sự nhất quán của
    canh lề, cho nó ăn số liệu suy đoán là tự tạo ra một lớp lỗi oan mới trên đúng
    loại tệp không kiểm được. Suy để vẽ thì được, suy để phán thì không.
    """
    if structure is None:
        return [], False

    payload: list[dict[str, Any]] = []
    total = 0
    for block in structure.blocks:
        if len(payload) >= MAX_VIEW_BLOCKS or total >= MAX_VIEW_CHARS:
            return payload, True
        payload.append({
            "id": block.id, "text": block.text, "kind": block.kind,
            "font": block.font, "size_pt": block.size_pt, "bold": block.bold,
            "alignment": block.alignment, "line_spacing": block.line_spacing,
            "space_before_pt": block.space_before_pt,
            "space_after_pt": block.space_after_pt,
            "style": block.style, "page": block.page,
            "bbox": list(block.bbox) if block.bbox else None,
        })
        total += len(block.text)
    return payload, False


def _geometry_payload(structure: Any) -> dict[str, Any] | None:
    geo = getattr(structure, "geometry", None)
    if geo is None:
        return None
    return {
        "width_mm": round(geo.width_mm, 1), "height_mm": round(geo.height_mm, 1),
        "top_mm": round(geo.top_mm, 1), "bottom_mm": round(geo.bottom_mm, 1),
        "left_mm": round(geo.left_mm, 1), "right_mm": round(geo.right_mm, 1),
        "measured": geo.measured,
    }


def _giao_nhau(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Hai phát hiện có chạm nhau trên cùng một đoạn chữ không."""
    if a["block_id"] != b["block_id"]:
        return False
    if a.get("start") is None or b.get("start") is None:
        return False
    return a["start"] < b["end"] and b["start"] < a["end"]


def gop_phat_hien(
    typo: list[dict[str, Any]], llm: list[dict[str, Any]], thu_tu: list[str]
) -> list[dict[str, Any]]:
    """Một danh sách duy nhất, xếp theo đúng thứ tự đọc của tài liệu.

    Phát hiện của LLM chồng lên phát hiện tất định thì BỎ cái của LLM: cùng một
    chỗ chữ mà hiện hai khung với hai lời giải thích khác nhau thì người đọc phải
    tự xử lấy, trong khi bên tất định đã chắc chắn đúng.
    """
    giu = [f for f in llm if not any(_giao_nhau(f, t) for t in typo)]
    if len(giu) < len(llm):
        logger.debug("Bỏ %d phát hiện LLM trùng chỗ với lớp tất định", len(llm) - len(giu))

    # Hai nhánh LLM cũng chồng lên nhau: nhánh chính tả báo "Công căn", nhánh chữ
    # nghĩa báo "khoản 2 Công căn này" - cùng một lỗi, hai khung lồng nhau. Giữ
    # cái HẸP hơn: nó chỉ đúng chữ sai, còn cái rộng ôm thêm chữ không liên quan.
    # Xét từ hẹp tới rộng nên cái hẹp luôn được vào trước.
    khong_chong: list[dict[str, Any]] = []
    for f in sorted(giu, key=lambda x: (x["end"] - x["start"]) if x.get("start") is not None else 10**6):
        if any(_giao_nhau(f, g) for g in khong_chong):
            continue
        khong_chong.append(f)
    if len(khong_chong) < len(giu):
        logger.debug("Bỏ %d phát hiện LLM chồng lên nhau", len(giu) - len(khong_chong))
    giu = khong_chong

    vi_tri = {block_id: i for i, block_id in enumerate(thu_tu)}
    return sorted(
        typo + giu,
        key=lambda f: (vi_tri.get(f["block_id"], len(vi_tri)),
                       f["start"] if f.get("start") is not None else -1),
    )


def _dem(findings: list[dict[str, Any]], muc: str) -> int:
    return sum(1 for f in findings if f.get("severity") == muc)


async def assemble_node(state: dict[str, Any]) -> dict[str, Any]:
    structure = state.get("structure")
    outline = state.get("outline")
    rule_result = state.get("rule_result")
    # Chính tả (LLM) đi cùng phe với soát chữ nghĩa: cùng là phán đoán, cùng phải
    # trích dẫn được, cùng vẽ khung đứt nét.
    llm_findings = (state.get("llm_findings") or []) + (state.get("spell_findings") or [])
    typo_findings = state.get("typo_findings") or []

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

    thu_tu = [b.id for b in outline.blocks] if outline else []
    findings = gop_phat_hien(typo_findings, llm_findings, thu_tu)

    # Gom lỗi chữ nghĩa theo khối: người đọc soát từng đoạn một, không đọc một
    # danh sách phẳng rồi tự nhặt xem lỗi nào thuộc đoạn nào. Giữ khoá cũ
    # `llm_review` để bên đã tích hợp không vỡ; giao diện mới đọc `findings`.
    by_block: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        by_block.setdefault(finding["block_id"], []).append(finding)

    blocks, cat_bot = _blocks_payload(structure)
    errors = (rule_result.error_count if rule_result else 0) + _dem(findings, "error")
    warnings = (rule_result.warning_count if rule_result else 0) + _dem(findings, "warning")

    return {
        "result": {
            "document": {
                "source_format": structure.source_format if structure else "unknown",
                "block_count": len(structure.blocks) if structure else 0,
                "page_count": structure.page_count if structure else 0,
                "has_format_info": structure.has_format_info if structure else False,
                "title": outline.title.text if outline and outline.title else "",
                "outline": _outline_payload(outline),
                "default_font": getattr(structure, "default_font", None),
                "default_size_pt": getattr(structure, "default_size_pt", None),
                "geometry": _geometry_payload(structure),
            },
            # Nội dung tài liệu để giao diện dựng lại trang và khoanh vùng lỗi.
            "blocks": blocks,
            "blocks_truncated": cat_bot,
            # Danh sách phẳng, có vị trí ký tự và `source` - thứ bản dựng lại dùng.
            "findings": findings,
            "rule_check": rule_payload,
            "llm_review": by_block,
            "classification": state.get("classification"),
            "summary": state.get("summary", ""),
            "summary_refs": state.get("summary_refs", []),
            "all_refs": state.get("all_refs", []),
            "deadline": state.get("deadline"),
            "tasks": state.get("tasks", []),
            # Mẫu văn bản giao việc nên chọn sẵn. Chỉ là giá trị mặc định - người
            # dùng đổi được, và đổi rồi thì không ai phải giải thích vì sao.
            "giao_viec_goi_y": goi_y_mau(
                (state.get("classification") or {}).get("document_type", "")),
            # Đếm theo `source` của phát hiện CÒN LẠI, không theo độ dài danh sách
            # nguồn: ứng viên bị cổng gác loại đã biến mất, còn ứng viên chưa duyệt
            # được thì đã hạ xuống "llm" - đếm theo nguồn thì hai cột luôn khớp với
            # số khung liền nét và khung đứt nét người dùng đang nhìn thấy.
            "totals": {
                "errors": errors,
                "warnings": warnings,
                "chac_chan": len([f for f in findings if f.get("source") == "rule"]),
                "goi_y": len([f for f in findings if f.get("source") == "llm"]),
            },
            "error": state.get("error", ""),
        }
    }
