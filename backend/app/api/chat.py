"""API workflow 1: hỏi đáp / tra cứu tài liệu (RAG có trích dẫn)."""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.graph import build_initial_state, prepare_context, run_qa
from app.agents.nodes.qa import (
    NO_CONTEXT_LLM_ARGS,
    build_generation_messages,
    build_no_context_messages,
    extract_used_citations,
)
from app.agents.prompts import NO_CONTEXT_ANSWER
from app.core.config import get_settings
from app.rag.retrieval import get_retriever
from app.rag.vectorstore import build_filter
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    CitationModel,
    HistoryResponse,
    HistoryTurn,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.services.conversation import get_memory
from app.services.llm import LLMError, get_llm

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/chat", tags=["chat"])


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/qa", response_model=ChatResponse, summary="Hỏi đáp trên kho tài liệu")
async def ask(request: ChatRequest) -> ChatResponse:
    state = await build_initial_state(
        question=request.question,
        conversation_id=request.conversation_id,
        doc_ids=request.filters.doc_ids,
        sources=request.filters.sources,
        doc_types=request.filters.doc_types,
        top_n=request.top_n,
        use_rerank=request.use_rerank,
    )
    result = await run_qa(state)

    return ChatResponse(
        answer=result.get("answer", ""),
        conversation_id=state["conversation_id"],
        citations=[CitationModel(**c) for c in result.get("used_citations", [])],
        standalone_query=result.get("standalone_query", ""),
        query_variants=result.get("query_variants", []),
        trace=result.get("trace") if request.include_trace else None,
    )


@router.post("/qa/stream", summary="Hỏi đáp dạng streaming (SSE)")
async def ask_stream(request: ChatRequest) -> StreamingResponse:
    """Phát câu trả lời theo từng mảnh; trích dẫn được gửi trước ở event `meta`."""
    state = await build_initial_state(
        question=request.question,
        conversation_id=request.conversation_id,
        doc_ids=request.filters.doc_ids,
        sources=request.filters.sources,
        doc_types=request.filters.doc_types,
        top_n=request.top_n,
        use_rerank=request.use_rerank,
    )
    prepared = await prepare_context(state, defer_generation=True)

    async def event_stream() -> AsyncIterator[str]:
        conversation_id = prepared["conversation_id"]
        has_context = bool(prepared.get("context"))
        citations = prepared.get("citations", []) if has_context else []
        yield _sse(
            "meta",
            {
                "conversation_id": conversation_id,
                "standalone_query": prepared.get("standalone_query", ""),
                "query_variants": prepared.get("query_variants", []),
                "citations": citations,
                "trace": prepared.get("trace", {}) if request.include_trace else None,
            },
        )

        # Truy hồi HỎNG: node no_context đã dựng sẵn câu nói đúng sự thật về sự
        # cố, không có gì để sinh thêm.
        if not has_context and prepared.get("answer"):
            answer = prepared["answer"]
            yield _sse("delta", {"text": answer})
            yield _sse("done", {"citations": [], "conversation_id": conversation_id})
            await _save_turn(conversation_id, request.question, answer)
            return

        # Truy hồi không ra gì: vẫn đối đáp bình thường (chào hỏi, hỏi hệ thống
        # làm được gì), nhưng không nguồn nào nên cũng không trích dẫn gì.
        if has_context:
            messages, extra = build_generation_messages(prepared), {}
        else:
            messages, extra = build_no_context_messages(prepared), NO_CONTEXT_LLM_ARGS

        collected: list[str] = []
        try:
            async for piece in get_llm().stream_chat(messages, **extra):
                collected.append(piece)
                yield _sse("delta", {"text": piece})
        except LLMError as exc:
            logger.exception("Streaming thất bại")
            yield _sse("error", {"detail": str(exc)})
            return

        answer = "".join(collected).strip()
        if not answer:
            # Model im lặng: vẫn phải trả về một câu, không để khung chat trống trơn.
            answer = NO_CONTEXT_ANSWER
            yield _sse("delta", {"text": answer})

        used = extract_used_citations(answer, citations) if citations else []
        yield _sse("done", {"citations": used, "conversation_id": conversation_id})
        await _save_turn(conversation_id, request.question, answer)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _save_turn(conversation_id: str, question: str, answer: str) -> None:
    await get_memory().append_turns(
        conversation_id, [("user", question), ("assistant", answer)])


@router.post("/search", response_model=SearchResponse, summary="Chỉ chạy truy hồi (debug hybrid)")
async def search(request: SearchRequest) -> SearchResponse:
    """Trả về kết quả hybrid + RRF + rerank kèm hạng của từng nhánh."""
    query_variants: list[str] = []
    query = request.query
    if request.rewrite:
        from app.agents.nodes.qa import rewrite_query_node

        rewritten = await rewrite_query_node({"question": query, "history": []})  # type: ignore[arg-type]
        query = rewritten.get("standalone_query", query)
        query_variants = list(rewritten.get("query_variants", []))
        # Giữ câu gốc trong tập chấm điểm, cùng lý do với `retrieve_node`: bản
        # viết lại do máy sinh, có lúc diễn đạt kém hơn chính câu người dùng hỏi.
        if request.query and request.query != query:
            query_variants.insert(0, request.query)

    result = await get_retriever().retrieve(
        query=query,
        query_variants=query_variants,
        query_filter=build_filter(
            doc_ids=request.filters.doc_ids,
            sources=request.filters.sources,
            doc_types=request.filters.doc_types,
        ),
        top_n=request.top_n,
        rerank=request.use_rerank,
    )

    return SearchResponse(
        queries=result.queries,
        hits=[
            SearchHit(
                point_id=chunk.point_id,
                text=chunk.text,
                doc_id=chunk.doc_id,
                doc_title=chunk.doc_title,
                section=chunk.section,
                page=chunk.payload.get("page"),
                rrf_score=round(chunk.rrf_score, 6),
                rerank_score=round(chunk.rerank_score, 6),
                matched_by=chunk.matched_by,
                branch_ranks=chunk.branch_ranks,
            )
            for chunk in result.chunks
        ],
        branch_hits=result.branch_hits,
        fused_count=result.fused_count,
        timings_ms={k: round(v, 1) for k, v in result.timings_ms.items()},
    )


@router.get("/history/{conversation_id}", response_model=HistoryResponse)
async def get_history(conversation_id: str) -> HistoryResponse:
    turns = await get_memory().get_history(conversation_id)
    return HistoryResponse(
        conversation_id=conversation_id,
        turns=[HistoryTurn(**turn) for turn in turns],
    )


@router.delete("/history/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def clear_history(conversation_id: str) -> None:
    await get_memory().clear_history(conversation_id)


@router.post("/conversations", summary="Tạo mã hội thoại mới")
async def new_conversation() -> dict[str, str]:
    return {"conversation_id": str(uuid.uuid4())}
