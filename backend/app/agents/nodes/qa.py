"""Các node của workflow 1: hỏi đáp / tra cứu tài liệu (RAG).

    question -> rewrite_query -> retrieve (hybrid + RRF + rerank)
             -> build_context -> generate -> answer + citations

Mỗi node là một hàm async thuần trên `QAState`, nên vừa lắp được vào LangGraph
(`app.agents.graph`) vừa gọi tuần tự được khi cần stream câu trả lời.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.agents import progress
from app.services import errors
from app.agents.prompts import (
    NO_CONTEXT_ANSWER,
    NO_CONTEXT_SYSTEM,
    QA_CAN_TRA_CUU_SYSTEM,
    QA_CAN_TRA_CUU_USER,
    QA_SYSTEM,
    QA_USER,
    QUERY_REWRITE_SYSTEM,
    QUERY_REWRITE_USER,
)
from app.agents.history import format_history
from app.agents.state import Citation, QAState
from app.agents.xa_giao import la_xa_giao
from app.core.config import get_settings
from app.rag.retrieval import RetrievedChunk, get_retriever
from app.rag.vectorstore import build_filter
from app.services.cache import cache_key, get_cache
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


# --------------------------------------------------------------------------- #
# 0. Có cần tra cứu không - quyết định của MODEL, không phải của pipeline
# --------------------------------------------------------------------------- #
async def quyet_dinh_tra_cuu_node(state: QAState) -> dict[str, Any]:
    """Hỏi model: lượt này có cần lục kho tài liệu không.

    Trước đây pipeline `qa` LUÔN truy hồi - không có bước nào để quyết định cả.
    Nên một lời chào cũng kéo về mấy chunk ngẫu nhiên, và model dựng chúng thành
    một câu trả lời có trích dẫn đàng hoàng. Ngưỡng điểm không chữa được: "Hi"
    không giống đoạn nào, nhưng cũng không giống đoạn nào theo kiểu đều đều.

    Việc cần làm là nhận ra "lượt này không phải câu hỏi", và đó là chuyện hiểu
    câu chữ - việc của model, không phải của một bảng từ khoá. `la_xa_giao` vẫn
    còn, nhưng lùi xuống làm ĐƯỜNG LÙI khi model hỏng, đúng như mọi tầng khác
    trong agent này.

    Cán cân lệch hẳn về phía tra cứu: tra thừa chỉ tốn vài giây, còn bỏ qua tra
    cứu cho một câu hỏi thật thì model trả lời bằng trí nhớ của nó và người dùng
    không có cách nào biết câu đó không đến từ tài liệu của mình.
    """
    question = state["question"].strip()
    if not question:
        return {"can_tra_cuu": False,
                "trace": {**state.get("trace", {}), "can_tra_cuu": False,
                          "xa_giao": True, "nguon_quyet_dinh": "rỗng"}}

    cfg = get_settings()
    try:
        data = await get_llm().chat_json(
            [
                {"role": "system", "content": QA_CAN_TRA_CUU_SYSTEM},
                {"role": "user", "content": QA_CAN_TRA_CUU_USER.format(
                    history=format_history(state.get("history", []), 2), question=question)},
            ],
            model=cfg.utility_model, temperature=0.0, max_tokens=200, thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001 - luôn phải có đường lùi
        logger.warning("Không quyết được có tra cứu không, dùng từ khoá: %s", exc)
        can = not la_xa_giao(question)
        return {"can_tra_cuu": can,
                "trace": {**state.get("trace", {}), "can_tra_cuu": can,
                          "xa_giao": not can, "nguon_quyet_dinh": "từ khoá"}}

    can = bool(data.get("can_tra_cuu", True))
    truy_van = str(data.get("truy_van") or "").strip()
    logger.info("Cần tra cứu: %s (%s)", can, str(data.get("ly_do") or "")[:80])
    ra: dict[str, Any] = {
        "can_tra_cuu": can,
        # `xa_giao` là cờ mà `graph._step_is_empty` đọc để KHÔNG coi lượt này là
        # tra cứu hụt. Thiếu nó, `RETRY_AS` đẩy lời chào sang vòng lặp công cụ
        # ERP - chạy vài giây, gọi vài tool, để nói lại đúng câu "chào bạn".
        "trace": {**state.get("trace", {}), "can_tra_cuu": can, "xa_giao": not can,
                  "nguon_quyet_dinh": "llm", "ly_do_tra_cuu": str(data.get("ly_do") or "")},
    }
    # Model đã giải đại từ sẵn khi quyết định; dùng lại, khỏi gọi thêm một lượt.
    if can and truy_van:
        ra["standalone_query"] = truy_van
    return ra


# --------------------------------------------------------------------------- #
# 1. Viết lại truy vấn
# --------------------------------------------------------------------------- #
async def rewrite_query_node(state: QAState) -> dict[str, Any]:
    """Giải đại từ theo ngữ cảnh hội thoại và sinh biến thể truy vấn.

    Thất bại ở bước này không được chặn pipeline: fallback là dùng câu hỏi gốc.
    """
    cfg = get_settings()
    question = state["question"].strip()
    history = state.get("history", [])

    # Bước quyết định đã giải đại từ và viết sẵn `standalone_query` rồi; viết lại
    # lần nữa chỉ tốn một lượt gọi và thêm một dịp rụng chữ.
    if not cfg.query_rewrite_enabled:
        return {"standalone_query": state.get("standalone_query") or question,
                "query_variants": []}

    history_text = format_history(history, cfg.query_rewrite_history_turns)
    cache = get_cache()
    key = cache_key("rewrite", question, history_text, str(cfg.query_rewrite_max_variants))
    if (cached := await cache.get_json(key)) is not None:
        return {
            "standalone_query": cached.get("standalone_query", question),
            "query_variants": cached.get("variants", []),
        }

    started = time.perf_counter()
    try:
        data = await get_llm().chat_json(
            [
                {
                    "role": "system",
                    "content": QUERY_REWRITE_SYSTEM.format(max_variants=cfg.query_rewrite_max_variants),
                },
                {
                    "role": "user",
                    "content": QUERY_REWRITE_USER.format(history=history_text, question=question),
                },
            ],
            model=cfg.utility_model,
            temperature=0.1,
            max_tokens=400,
            timeout=cfg.query_rewrite_timeout,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001 - luôn phải có đường lùi
        logger.warning("Viết lại truy vấn thất bại, dùng câu hỏi gốc: %s", exc)
        return {"standalone_query": question, "query_variants": []}

    standalone = str(data.get("standalone_query") or question).strip() or question
    variants = [
        str(v).strip()
        for v in (data.get("variants") or [])
        if isinstance(v, (str, int, float)) and str(v).strip()
    ][: cfg.query_rewrite_max_variants]

    await cache.set_json(key, {"standalone_query": standalone, "variants": variants})
    logger.debug("Rewrite %.0fms: %r -> %r + %d biến thể",
                 (time.perf_counter() - started) * 1000, question, standalone, len(variants))
    return {"standalone_query": standalone, "query_variants": variants}


# --------------------------------------------------------------------------- #
# 2. Truy hồi hybrid
# --------------------------------------------------------------------------- #
async def retrieve_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    question = state["question"].strip()
    # Model đã quyết là không cần tra cứu -> không chunk nào, đồ thị rẽ sang
    # `no_context`, nơi nó được đối đáp bình thường mà không có nguồn để trỏ tới.
    if not state.get("can_tra_cuu", True):
        return {"chunks": [], "trace": {**state.get("trace", {}), "xa_giao": True}}
    query = state.get("standalone_query") or question
    # Câu GỐC của người dùng luôn phải nằm trong tập đem đi chấm điểm, kể cả khi
    # đã có bản viết lại. Bản viết lại và các biến thể đều do máy sinh: chúng có
    # thể diễn đạt kém hơn chính câu người dùng hỏi. Đã gặp thật - "Ai ký công
    # văn chỉ thị kiểm kê" được 0,1298 với câu gốc, nhưng cả 4 câu viết lại đều
    # dưới ngưỡng, và một tài liệu KHÁC trèo lên 0,1201: hệ thống trả lời về sai
    # văn bản. Thêm câu gốc vào thì nó vẫn được chấm và vẫn thắng.
    variants = list(state.get("query_variants") or [])
    if question and question != query:
        variants.insert(0, question)
    query_filter = build_filter(
        doc_ids=state.get("doc_ids"),
        sources=state.get("sources"),
        doc_types=state.get("doc_types"),
    )

    # Người dùng đã tự chọn tài liệu thì NGƯỠNG hết việc. Ngưỡng sinh ra để gạt
    # tài liệu không liên quan lẫn vào từ cả kho; ở đây không còn kho nào để lẫn,
    # chỉ còn đúng thứ họ bấm chọn. Giữ ngưỡng thì gặp đúng cảnh vô lý: chọn một
    # công văn, hỏi "văn bản này nói gì", rồi nhận về "chưa rõ bạn hỏi tài liệu
    # nào" - trong khi tên tài liệu đang hiện ngay trên đầu màn hình.
    da_chon = bool(state.get("doc_ids") or state.get("sources"))

    try:
        result = await get_retriever().retrieve(
            query=query,
            query_variants=variants,
            query_filter=query_filter,
            top_n=state.get("top_n") or cfg.rerank_top_n,
            rerank=state.get("use_rerank", True),
            score_threshold=0.0 if da_chon else None,
        )
    except Exception as exc:  # noqa: BLE001 - Qdrant chết thì trả lời có kiểm soát
        logger.exception("Truy hồi thất bại")
        return {"chunks": [], "error": errors.friendly(exc)}

    trace = {
        "queries": result.queries,
        "branch_hits": result.branch_hits,
        "fused_count": result.fused_count,
        "timings_ms": {k: round(v, 1) for k, v in result.timings_ms.items()},
        "top_scores": [round(c.rerank_score, 4) for c in result.chunks],
    }
    return {"retrieval": result, "chunks": result.chunks, "trace": {**state.get("trace", {}), **trace}}


# --------------------------------------------------------------------------- #
# 3. Dựng ngữ cảnh + danh sách trích dẫn
# --------------------------------------------------------------------------- #
# Cú pháp Markdown trong đoạn trích dẫn. Giao diện hiện `snippet` như chữ thuần,
# nên "**Bước 2:**" tới mắt người đọc đúng là "**Bước 2:**" - dấu sao và tất cả.
_CU_PHAP_MARKDOWN: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), ""),          # ảnh: bỏ hẳn
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),      # liên kết: giữ chữ
    (re.compile(r"<[^>\n]{1,40}>"), ""),                   # thẻ HTML sót lại
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),          # tiêu đề
    (re.compile(r"^\s{0,3}>\s?", re.M), ""),               # trích dẫn lồng
    (re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+", re.M), ""),  # đầu dòng
    (re.compile(r"^\s*\|?[\s:|-]{3,}\|?\s*$", re.M), ""),   # dòng kẻ bảng
    (re.compile(r"[*_`~]+"), ""),                          # đậm / nghiêng / code
    (re.compile(r"\s*\|\s*"), " | "),                      # ô bảng
)


def lam_phang_markdown(text: str) -> str:
    """Bỏ cú pháp Markdown, giữ lại chữ - dùng cho đoạn trích hiện trên màn hình."""
    for mau, thay in _CU_PHAP_MARKDOWN:
        text = mau.sub(thay, text)
    return " ".join(text.split()).strip(" |")


def _snippet(text: str, limit: int = 240) -> str:
    flat = lam_phang_markdown(text)
    return flat if len(flat) <= limit else flat[:limit].rsplit(" ", 1)[0] + "…"


def build_context(chunks: list[RetrievedChunk], max_chars: int) -> tuple[str, list[Citation]]:
    """Ghép các chunk thành khối ngữ cảnh đánh số [n] kèm metadata nguồn."""
    blocks: list[str] = []
    citations: list[Citation] = []
    used_chars = 0

    for chunk in chunks:
        text = chunk.text.strip()
        if not text:
            continue
        # Chunk chồng lấn nhau có thể lặp nội dung -> bỏ phần đã có trong ngữ cảnh.
        if any(text in block for block in blocks):
            continue

        index = len(citations) + 1
        header = f"[{index}] {chunk.citation_label()}"
        block = f"{header}\n{text}"
        if used_chars + len(block) > max_chars and blocks:
            break

        blocks.append(block)
        used_chars += len(block)
        citations.append(
            Citation(
                id=index,
                doc_id=chunk.doc_id,
                doc_title=chunk.doc_title,
                section=chunk.section,
                source=str(chunk.payload.get("source", "")),
                page=chunk.payload.get("page"),
                snippet=_snippet(text),
                score=round(chunk.rerank_score, 4),
                matched_by=getattr(chunk, "matched_by", "rerank"),
            )
        )

    return "\n\n".join(blocks), citations


async def build_context_node(state: QAState) -> dict[str, Any]:
    cfg = get_settings()
    context, citations = build_context(state.get("chunks", []), cfg.context_max_chars)
    return {
        "context": context,
        "citations": citations,
        "trace": {**state.get("trace", {}), "context_chars": len(context),
                  "context_blocks": len(citations)},
    }


# --------------------------------------------------------------------------- #
# 4. Sinh câu trả lời
# --------------------------------------------------------------------------- #
def build_generation_messages(state: QAState) -> list[dict[str, str]]:
    """Messages gửi LLM; dùng chung cho cả chế độ thường và streaming."""
    cfg = get_settings()
    messages: list[dict[str, str]] = [{"role": "system", "content": QA_SYSTEM}]

    # Vài lượt gần nhất giữ mạch hội thoại; nguồn sự thật vẫn là NGỮ CẢNH.
    for turn in state.get("history", [])[-2:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": turn["content"]})

    messages.append(
        {
            "role": "user",
            "content": QA_USER.format(
                context=state.get("context", ""),
                question=state.get("standalone_query") or state["question"],
            ),
        }
    )
    return messages


def extract_used_citations(answer: str, citations: list[Citation]) -> list[Citation]:
    """Chỉ giữ những nguồn thực sự được nhắc tới trong câu trả lời."""
    used_ids = {int(n) for n in _CITATION_RE.findall(answer)}
    used = [c for c in citations if c["id"] in used_ids]
    # Model quên đánh số -> vẫn trả về toàn bộ nguồn đã đưa vào ngữ cảnh.
    return used or citations


async def generate_node(state: QAState) -> dict[str, Any]:
    if state.get("error"):
        return {"answer": f"{NO_CONTEXT_ANSWER}", "used_citations": []}
    if not state.get("context"):
        return {"answer": NO_CONTEXT_ANSWER, "used_citations": [], "citations": []}

    started = time.perf_counter()
    try:
        # Stream để agent tổng đẩy được từng mảnh ra màn hình. Không ai nghe thì
        # `progress.emit` là lệnh rỗng và vòng lặp này chỉ gom chuỗi - đúng bằng
        # công một lời gọi `chat()` như trước.
        pieces: list[str] = []
        async for piece in get_llm().stream_chat(build_generation_messages(state)):
            pieces.append(piece)
            progress.emit("answer_delta", text=piece)
        answer = "".join(pieces).strip()
    except LLMError as exc:
        logger.exception("Sinh câu trả lời thất bại")
        return {"answer": "Hệ thống chưa kết nối được mô hình ngôn ngữ, vui lòng thử lại.",
                "used_citations": [], "error": errors.friendly(exc)}

    citations = state.get("citations", [])
    return {
        "answer": answer,
        "used_citations": extract_used_citations(answer, citations),
        "trace": {**state.get("trace", {}), "generate_ms": round((time.perf_counter() - started) * 1000, 1)},
    }


# Câu xã giao thì không cần suy luận, bật lên chỉ tốn vài giây chờ vô ích.
NO_CONTEXT_LLM_ARGS: dict[str, Any] = {"max_tokens": 400, "thinking": False}


def build_no_context_messages(state: QAState) -> list[dict[str, str]]:
    """Messages cho nhánh không có ngữ cảnh; dùng chung thường và streaming."""
    messages: list[dict[str, str]] = [{"role": "system", "content": NO_CONTEXT_SYSTEM}]
    for turn in state.get("history", [])[-4:]:
        role = turn.get("role")
        if role in ("user", "assistant") and turn.get("content"):
            messages.append({"role": role, "content": turn["content"]})
    messages.append({"role": "user", "content": state["question"]})
    return messages


async def no_context_node(state: QAState) -> dict[str, Any]:
    """Nhánh khi không truy hồi được gì.

    Vẫn không cho LLM bịa nội dung nghiệp vụ, nhưng để nó đối đáp bình thường:
    chào hỏi và câu hỏi về khả năng hệ thống cũng rơi vào đây, mà đáp lại bằng
    "không tìm thấy trong tài liệu" thì người dùng tưởng hệ thống hỏng.

    Suy luận tắt: đây là câu xã giao, bật lên chỉ tốn vài giây chờ.
    """
    # Truy hồi HỎNG khác hẳn truy hồi KHÔNG RA GÌ: để model đối đáp vui vẻ lúc
    # Qdrant chết là giấu mất sự cố, người dùng tưởng kho rỗng.
    if (failure := state.get("error")):
        return {
            "answer": f"Tôi chưa tra cứu được vì kho tài liệu đang không truy vấn được ({failure}). "
                      "Vui lòng thử lại sau hoặc báo quản trị hệ thống.",
            "citations": [],
            "used_citations": [],
            "trace": {**state.get("trace", {}), "no_context": True, "retrieval_failed": True},
        }

    answer = ""
    try:
        answer = (await get_llm().chat(
            build_no_context_messages(state), **NO_CONTEXT_LLM_ARGS)).strip()
    except LLMError as exc:
        logger.warning("Không sinh được câu đáp cho nhánh no_context: %s", exc)

    # Model hỏng hoặc trả rỗng thì vẫn phải có câu trả lời an toàn.
    return {
        "answer": answer or NO_CONTEXT_ANSWER,
        "citations": [],
        "used_citations": [],
        "trace": {**state.get("trace", {}), "no_context": True},
    }
