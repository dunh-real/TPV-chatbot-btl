"""Điểm vào FastAPI của backend TPV chatbot."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import agent, chat, documents, presentations, reports
from app.core.config import get_settings
from app.db.session import dispose_engine, healthcheck
from app.schemas.common import HealthResponse
from app.services.cache import get_cache
from app.services.conversation import get_memory
from app.services.llm import get_llm
from app.rag.vectorstore import get_vector_store

logger = logging.getLogger(__name__)
settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)


async def _warmup() -> None:
    """Nạp trước embedding + reranker để request đầu tiên không phải chờ."""
    import anyio

    def _load() -> None:
        from app.rag.embedding import get_embedder
        from app.rag.reranker import get_reranker

        get_embedder().encode(["khởi động"])
        get_reranker().score("khởi động", ["khởi động"])

    try:
        await anyio.to_thread.run_sync(_load)
        logger.info("Đã nạp sẵn embedding + reranker")
    except Exception as exc:  # noqa: BLE001
        logger.error("Warm-up model thất bại: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_vector_store()
    conversations = get_memory().store
    try:
        await store.ensure_collection()
        logger.info("Qdrant sẵn sàng: collection=%s", store.collection)
    except Exception as exc:  # noqa: BLE001 - vẫn cho app chạy để /health báo lỗi
        logger.error("Không khởi tạo được Qdrant: %s", exc)
    try:
        await conversations.ensure_collection()
        logger.info("Collection hội thoại sẵn sàng: %s", conversations.collection)
    except Exception as exc:  # noqa: BLE001
        # Không chặn app, nhưng phải kêu to: đây là bản gốc của lịch sử chat,
        # hỏng mà im lặng thì hội thoại mất mà không ai biết.
        logger.error("KHÔNG khởi tạo được collection hội thoại %s - lịch sử chat sẽ "
                     "không được lưu bền: %s", conversations.collection, exc)

    if settings.warmup_models:
        await _warmup()

    yield

    await store.close()
    await conversations.close()
    await get_llm().close()
    await get_cache().close()
    await dispose_engine()


app = FastAPI(
    title=settings.app_name,
    description="Multi-agent backend (LangGraph). POST /api/agent/chat tự định tuyến "
                "yêu cầu về một trong năm workflow: hỏi đáp, soát văn bản, soạn văn bản, "
                "tổng hợp báo cáo, tạo slide.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agent.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(reports.router)
app.include_router(presentations.router)


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    store = get_vector_store()
    qdrant_ok, points = False, 0
    try:
        points = await store.count()
        qdrant_ok = True
    except Exception as exc:  # noqa: BLE001
        logger.debug("Health check Qdrant lỗi: %s", exc)

    conversations = get_memory().store
    conversation_turns = 0
    try:
        conversation_turns = await conversations.count()
    except Exception as exc:  # noqa: BLE001
        logger.debug("Health check collection hội thoại lỗi: %s", exc)

    llm_ok = await get_llm().health()
    database_ok = await healthcheck()
    return HealthResponse(
        status="ok" if (qdrant_ok and llm_ok and database_ok) else "degraded",
        qdrant=qdrant_ok,
        llm=llm_ok,
        database=database_ok,
        collection=store.collection,
        points=points,
        details={"llm_model": settings.llm_model, "embedding_model": settings.embedding_model,
                 "reranker_model": settings.reranker_model,
                 "conversation_collection": conversations.collection,
                 "conversation_turns": str(conversation_turns)},
    )


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "docs": "/docs", "health": "/health"}
