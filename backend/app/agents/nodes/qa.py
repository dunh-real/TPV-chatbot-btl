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
    QA_SYSTEM,
    QA_USER,
    QUERY_REWRITE_SYSTEM,
    QUERY_REWRITE_USER,
)
from app.agents.history import format_history
from app.agents.state import Citation, QAState
from app.core.config import get_settings
from app.rag.retrieval import RetrievedChunk, get_retriever
from app.rag.vectorstore import build_filter
from app.services.cache import cache_key, get_cache
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d{1,2})\]")


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

    if not cfg.query_rewrite_enabled:
        return {"standalone_query": question, "query_variants": []}

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
    query = state.get("standalone_query") or state["question"]
    query_filter = build_filter(
        doc_ids=state.get("doc_ids"),
        sources=state.get("sources"),
        doc_types=state.get("doc_types"),
    )

    try:
        result = await get_retriever().retrieve(
            query=query,
            query_variants=state.get("query_variants"),
            query_filter=query_filter,
            top_n=state.get("top_n") or cfg.rerank_top_n,
            rerank=state.get("use_rerank", True),
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
def _snippet(text: str, limit: int = 240) -> str:
    flat = " ".join(text.split())
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
