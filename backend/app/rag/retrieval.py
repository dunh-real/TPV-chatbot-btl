"""Hybrid retrieval: 3 nhánh song song -> RRF -> cross-encoder rerank.

Luồng:
    queries (gốc + biến thể do LLM viết lại)
      -> embed 1 lượt (dense + lexical) + BM25
      -> với mỗi query: 3 truy vấn Qdrant chạy song song (dense / lexical / bm25)
      -> RRF gộp mọi nhánh của mọi biến thể theo point id
      -> lấy top `rrf_top_k` đưa qua reranker
      -> trả về top `rerank_top_n` chunk kèm thông tin phục vụ trích dẫn

RRF được tính phía client (thay vì `FusionQuery` của Qdrant) để giữ lại thứ hạng
của từng nhánh - rất hữu ích khi debug và khi muốn chỉnh trọng số từng nhánh.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import anyio
from qdrant_client import models

from app.core.config import Settings, get_settings
from app.rag.embedding import HybridEmbedding, get_bm25_encoder, get_embedder
from app.rag.vectorstore import (
    BM25_VECTOR,
    DENSE_VECTOR,
    LEXICAL_VECTOR,
    QdrantVectorStore,
    get_vector_store,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class FusedHit:
    """Một point sau khi gộp thứ hạng từ nhiều nhánh."""

    point_id: str
    rrf_score: float
    payload: dict[str, Any]
    branch_ranks: dict[str, int] = field(default_factory=dict)   # nhánh -> hạng tốt nhất
    branch_scores: dict[str, float] = field(default_factory=dict)

    @property
    def text(self) -> str:
        return str(self.payload.get("text", ""))

    @property
    def rerank_text(self) -> str:
        """Văn bản đưa vào cross-encoder - khớp với những gì đã được embed."""
        from app.rag.ingestion import contextualize

        return contextualize(str(self.payload.get("doc_title", "")), self.text)


@dataclass(slots=True)
class RetrievedChunk:
    """Chunk cuối cùng đưa vào context, đủ dữ liệu để trích dẫn."""

    point_id: str
    text: str
    rerank_score: float
    rrf_score: float
    payload: dict[str, Any]
    branch_ranks: dict[str, int] = field(default_factory=dict)

    @property
    def doc_id(self) -> str:
        return str(self.payload.get("doc_id", ""))

    @property
    def doc_title(self) -> str:
        return str(self.payload.get("doc_title") or self.payload.get("source") or self.doc_id)

    @property
    def section(self) -> str:
        return str(self.payload.get("section", ""))

    def citation_label(self) -> str:
        parts = [self.doc_title]
        if self.section:
            parts.append(self.section)
        if self.payload.get("page") is not None:
            parts.append(f"tr.{self.payload['page']}")
        return " - ".join(p for p in parts if p)


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    queries: list[str]
    branch_hits: dict[str, int] = field(default_factory=dict)
    fused_count: int = 0
    timings_ms: dict[str, float] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.chunks


# --------------------------------------------------------------------------- #
# RRF
# --------------------------------------------------------------------------- #
def reciprocal_rank_fusion(
    branches: Sequence[tuple[str, float, list[models.ScoredPoint]]],
    k: float = 60.0,
    limit: int | None = None,
) -> list[FusedHit]:
    """Gộp nhiều danh sách xếp hạng: score = Σ weight / (k + rank).

    `branches` là các bộ (tên nhánh, trọng số, danh sách kết quả đã sắp xếp).
    Điểm gốc của từng nhánh không so sánh được với nhau (cosine vs BM25) nên chỉ
    thứ hạng được dùng; điểm gốc giữ lại cho mục đích quan sát.
    """
    fused: dict[str, FusedHit] = {}
    for branch_name, weight, points in branches:
        for rank, point in enumerate(points, start=1):
            pid = str(point.id)
            hit = fused.get(pid)
            if hit is None:
                hit = FusedHit(point_id=pid, rrf_score=0.0, payload=dict(point.payload or {}))
                fused[pid] = hit
            hit.rrf_score += weight / (k + rank)
            # Một nhánh có thể xuất hiện nhiều lần (nhiều biến thể query): giữ hạng tốt nhất.
            if rank < hit.branch_ranks.get(branch_name, 10**9):
                hit.branch_ranks[branch_name] = rank
                hit.branch_scores[branch_name] = float(point.score)

    ranked = sorted(fused.values(), key=lambda h: h.rrf_score, reverse=True)
    return ranked[:limit] if limit else ranked


# --------------------------------------------------------------------------- #
# Retriever
# --------------------------------------------------------------------------- #
class HybridRetriever:
    def __init__(
        self,
        settings: Settings | None = None,
        store: QdrantVectorStore | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or get_vector_store()

    # -- embedding (chạy trong threadpool vì là tác vụ GPU đồng bộ) --------- #
    async def _embed_queries(self, queries: list[str]) -> list[HybridEmbedding]:
        def _run() -> list[HybridEmbedding]:
            dense, lexical = get_embedder().encode(queries)
            bm25 = get_bm25_encoder()
            return [
                HybridEmbedding(dense=d, lexical=lx, bm25=bm25.encode_query(q))
                for d, lx, q in zip(dense, lexical, queries, strict=True)
            ]

        return await anyio.to_thread.run_sync(_run)

    async def search(
        self,
        queries: list[str],
        query_filter: models.Filter | None = None,
        branch_limit: int | None = None,
        top_k: int | None = None,
    ) -> tuple[list[FusedHit], dict[str, int], dict[str, float]]:
        """Chạy 3 nhánh cho mọi biến thể truy vấn rồi gộp bằng RRF."""
        cfg = self.settings
        branch_limit = branch_limit or cfg.retrieval_branch_limit
        top_k = top_k or cfg.rrf_top_k
        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        embeddings = await self._embed_queries(queries)
        timings["embed"] = (time.perf_counter() - t0) * 1000

        # Mỗi biến thể query -> 3 nhánh; tất cả chạy song song.
        tasks: list[asyncio.Future] = []
        meta: list[tuple[str, float]] = []  # (tên nhánh, trọng số)
        for i, emb in enumerate(embeddings):
            # Biến thể phụ đóng góp ít hơn truy vấn gốc.
            query_weight = 1.0 if i == 0 else 0.85
            tasks.append(
                asyncio.create_task(self.store.search_dense(emb.dense, branch_limit, query_filter))
            )
            meta.append((DENSE_VECTOR, cfg.weight_dense * query_weight))

            tasks.append(
                asyncio.create_task(
                    self.store.search_sparse(LEXICAL_VECTOR, emb.lexical, branch_limit, query_filter)
                )
            )
            meta.append((LEXICAL_VECTOR, cfg.weight_lexical * query_weight))

            tasks.append(
                asyncio.create_task(
                    self.store.search_sparse(BM25_VECTOR, emb.bm25, branch_limit, query_filter)
                )
            )
            meta.append((BM25_VECTOR, cfg.weight_bm25 * query_weight))

        t1 = time.perf_counter()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        timings["search"] = (time.perf_counter() - t1) * 1000

        branches: list[tuple[str, float, list[models.ScoredPoint]]] = []
        branch_hits: dict[str, int] = {DENSE_VECTOR: 0, LEXICAL_VECTOR: 0, BM25_VECTOR: 0}
        for (name, weight), result in zip(meta, results, strict=True):
            if isinstance(result, BaseException):
                # Một nhánh lỗi không được làm hỏng cả truy vấn.
                logger.warning("Nhánh %s lỗi: %s", name, result)
                continue
            branches.append((name, weight, result))
            branch_hits[name] += len(result)

        t2 = time.perf_counter()
        fused = reciprocal_rank_fusion(branches, k=cfg.rrf_k, limit=top_k)
        timings["rrf"] = (time.perf_counter() - t2) * 1000
        return fused, branch_hits, timings

    async def retrieve(
        self,
        query: str,
        query_variants: list[str] | None = None,
        query_filter: models.Filter | None = None,
        top_n: int | None = None,
        rerank: bool = True,
    ) -> RetrievalResult:
        """Pipeline đầy đủ: hybrid -> RRF -> rerank."""
        cfg = self.settings
        # Truy vấn gốc luôn đứng đầu, biến thể trùng lặp bị loại.
        queries = [query, *[q for q in (query_variants or []) if q and q != query]]

        fused, branch_hits, timings = await self.search(queries, query_filter=query_filter)
        if not fused:
            return RetrievalResult(chunks=[], queries=queries, branch_hits=branch_hits,
                                   fused_count=0, timings_ms=timings)

        if not rerank:
            chunks = [
                RetrievedChunk(point_id=h.point_id, text=h.text, rerank_score=0.0,
                               rrf_score=h.rrf_score, payload=h.payload,
                               branch_ranks=h.branch_ranks)
                for h in fused[: top_n or cfg.rerank_top_n]
            ]
            return RetrievalResult(chunks=chunks, queries=queries, branch_hits=branch_hits,
                                   fused_count=len(fused), timings_ms=timings)

        from app.rag.reranker import get_reranker

        documents = [h.rerank_text for h in fused]
        t0 = time.perf_counter()
        ranked = await anyio.to_thread.run_sync(
            lambda: get_reranker().rerank(query, documents, top_n=top_n or cfg.rerank_top_n)
        )
        timings["rerank"] = (time.perf_counter() - t0) * 1000

        chunks = [
            RetrievedChunk(
                point_id=fused[r.index].point_id,
                text=fused[r.index].text,
                rerank_score=r.score,
                rrf_score=fused[r.index].rrf_score,
                payload=fused[r.index].payload,
                branch_ranks=fused[r.index].branch_ranks,
            )
            for r in ranked
        ]
        logger.info(
            "Retrieval: %d biến thể, nhánh=%s, fused=%d, giữ lại=%d, %s",
            len(queries), branch_hits, len(fused), len(chunks),
            {k: round(v) for k, v in timings.items()},
        )
        return RetrievalResult(chunks=chunks, queries=queries, branch_hits=branch_hits,
                               fused_count=len(fused), timings_ms=timings)


_retriever: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    global _retriever
    if _retriever is None:
        _retriever = HybridRetriever()
    return _retriever
