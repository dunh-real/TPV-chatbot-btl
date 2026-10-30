"""Lớp truy cập Qdrant cho collection hybrid 3 nhánh.

Một collection duy nhất chứa song song 3 biểu diễn của cùng một chunk:
    dense   - vector đặc 1024-d (cosine)          -> vector search
    lexical - sparse, trọng số token BGE-M3       -> index search
    bm25    - sparse, TF chuẩn hoá + IDF phía server -> keyword search
Nhờ vậy 3 nhánh luôn trỏ tới cùng một point id, RRF chỉ việc gộp theo id.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, get_settings
from app.rag.embedding import HybridEmbedding, SparseEmbedding

logger = logging.getLogger(__name__)

DENSE_VECTOR = "dense"
LEXICAL_VECTOR = "lexical"
BM25_VECTOR = "bm25"
SPARSE_VECTORS = (LEXICAL_VECTOR, BM25_VECTOR)

# Namespace cố định để id của chunk ổn định giữa các lần ingest lại.
_POINT_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


@dataclass(slots=True)
class ChunkPoint:
    """Một chunk đã sẵn sàng ghi vào Qdrant."""

    doc_id: str
    chunk_index: int
    text: str
    embedding: HybridEmbedding
    payload: dict[str, Any]
    # Khoảng hiệu lực thứ mấy của văn bản (VBQPPL đã bị sửa đổi có nhiều bộ chunk).
    # 0 giữ nguyên id cũ để tài liệu thường không đổi id giữa các phiên bản code.
    snapshot: int = 0

    @property
    def point_id(self) -> str:
        key = f"{self.doc_id}:{self.chunk_index}" if not self.snapshot else \
            f"{self.doc_id}@{self.snapshot}:{self.chunk_index}"
        return str(uuid.uuid5(_POINT_NAMESPACE, key))


def to_qdrant_sparse(sparse: SparseEmbedding) -> models.SparseVector:
    return models.SparseVector(indices=sparse.indices, values=sparse.values)


class QdrantVectorStore:
    def __init__(self, settings: Settings | None = None, client: AsyncQdrantClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_collection
        self._client = client or AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=int(self.settings.qdrant_timeout),
        )

    @property
    def client(self) -> AsyncQdrantClient:
        return self._client

    async def close(self) -> None:
        await self._client.close()

    # ----------------------------------------------------------- schema --- #
    async def ensure_collection(self, recreate: bool = False) -> None:
        exists = await self._client.collection_exists(self.collection)
        if exists and recreate:
            await self._client.delete_collection(self.collection)
            exists = False
        if not exists:
            await self._client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self.settings.dense_vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    # Trọng số lexical đã do model học -> không nhân IDF nữa.
                    LEXICAL_VECTOR: models.SparseVectorParams(
                        index=models.SparseIndexParams()
                    ),
                    # BM25 cần IDF; để Qdrant tự tính trên toàn collection.
                    BM25_VECTOR: models.SparseVectorParams(
                        index=models.SparseIndexParams(),
                        modifier=models.Modifier.IDF,
                    ),
                },
            )
            logger.info("Đã tạo collection %s", self.collection)

        await self._ensure_payload_indexes()

    async def _ensure_payload_indexes(self) -> None:
        """Index payload để lọc nhanh theo tài liệu/nguồn khi truy vấn."""
        specs: dict[str, models.PayloadSchemaType] = {
            "doc_id": models.PayloadSchemaType.KEYWORD,
            "source": models.PayloadSchemaType.KEYWORD,
            "doc_type": models.PayloadSchemaType.KEYWORD,
            "chunk_index": models.PayloadSchemaType.INTEGER,
            # VBQPPL theo phiên bản (app.legal)
            "legal_id": models.PayloadSchemaType.KEYWORD,
            "dieus": models.PayloadSchemaType.KEYWORD,
            "valid_from": models.PayloadSchemaType.DATETIME,
            "valid_to": models.PayloadSchemaType.DATETIME,
            "latest": models.PayloadSchemaType.BOOL,
        }
        for field_name, schema in specs.items():
            try:
                await self._client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field_name,
                    field_schema=schema,
                    wait=True,
                )
            except Exception as exc:  # index đã tồn tại -> bỏ qua
                logger.debug("Bỏ qua tạo index %s: %s", field_name, exc)

    # ------------------------------------------------------------ ghi ---- #
    async def upsert_chunks(self, chunks: Sequence[ChunkPoint], batch_size: int = 64) -> int:
        total = 0
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            points = [
                models.PointStruct(
                    id=c.point_id,
                    vector={
                        DENSE_VECTOR: c.embedding.dense,
                        LEXICAL_VECTOR: to_qdrant_sparse(c.embedding.lexical),
                        BM25_VECTOR: to_qdrant_sparse(c.embedding.bm25),
                    },
                    payload={**c.payload, "doc_id": c.doc_id,
                             "chunk_index": c.chunk_index, "text": c.text},
                )
                for c in batch
            ]
            await self._client.upsert(collection_name=self.collection, points=points, wait=True)
            total += len(points)
        return total

    async def link_source(self, doc_id: str, metadata: dict) -> None:
        """Chỉ bổ sung trường thiếu; từ chối nếu có nguồn khác, không đổi vector."""
        query = models.Filter(must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))])
        points, offset = [], None
        while True:
            page, offset = await self.client.scroll(
                self.collection, scroll_filter=query, limit=256, offset=offset,
                with_vectors=False, with_payload=list(metadata))
            points.extend(page)
            if offset is None:
                break
        for point in points:
            payload = point.payload or {}
            for key in ("storage_bucket", "storage_object_key", "checksum_sha256"):
                if payload.get(key) and payload[key] != metadata[key]:
                    raise ValueError("Tài liệu đã liên kết tới nguồn khác; giữ nguyên nguồn hiện tại")
        for key, value in metadata.items():
            ids = [p.id for p in points if (p.payload or {}).get(key) in (None, "")]
            for start in range(0, len(ids), 256):
                await self.client.set_payload(self.collection, {key: value}, points=ids[start:start + 256], wait=True)

    async def delete_document(self, doc_id: str) -> None:
        await self._client.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                )
            ),
            wait=True,
        )

    async def count(self) -> int:
        result = await self._client.count(self.collection, exact=True)
        return result.count

    async def document_summary(self, doc_id: str) -> dict[str, Any] | None:
        """Tra đúng tài liệu, chỉ lấy metadata và đếm chunk, không tải text/vector."""
        query_filter = models.Filter(must=[
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))
        ])
        points, _ = await self._client.scroll(
            collection_name=self.collection, scroll_filter=query_filter, limit=1,
            with_payload=["doc_title", "source"], with_vectors=False,
        )
        if not points:
            return None
        count = await self._client.count(
            self.collection, count_filter=query_filter, exact=True,
        )
        payload = points[0].payload or {}
        return {"doc_id": doc_id, "doc_title": str(payload.get("doc_title") or doc_id),
                "source": str(payload.get("source") or ""), "chunk_count": count.count}

    async def document_metadata(self, doc_ids: Sequence[str]) -> dict[str, dict]:
        """Metadata và số chunk hiện hành; không tải text/vector hoặc quét toàn kho."""
        from app.db.ai_session import enabled
        if enabled():
            from app.services.ai_documents import document_metadata
            return await document_metadata(doc_ids)
        import asyncio
        gate = asyncio.Semaphore(8)
        fields = ["doc_title", "source", "doc_type", "ingested_at", "storage_bucket",
                  "storage_object_key", "original_filename", "file_size_bytes", "checksum_sha256"]
        async def read(doc_id):
            async with gate:
                query = models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))],
                    must_not=[models.FieldCondition(key="latest", match=models.MatchValue(value=False))])
                points, _ = await self.client.scroll(self.collection, scroll_filter=query,
                    limit=1, with_payload=fields, with_vectors=False)
                if not points:
                    return doc_id, None
                count = await self.client.count(self.collection, count_filter=query, exact=True)
                return doc_id, {**(points[0].payload or {}), "chunk_count": count.count}
        return {key: value for key, value in await asyncio.gather(*(read(d) for d in dict.fromkeys(doc_ids)))
                if value is not None}

    async def scroll_document(self, doc_id: str, page_size: int = 256) -> list[dict[str, Any]]:
        """Toàn bộ chunk của một tài liệu, xếp theo đúng thứ tự trong file gốc.

        Khác `search`: không xếp hạng và không được bỏ sót đoạn nào - dùng khi
        người dùng muốn đọc lại nguyên văn bản chứ không phải tìm đoạn liên quan.
        """
        from app.db.ai_session import enabled
        if enabled() and doc_id not in await self.document_metadata([doc_id]):
            return []
        if enabled():
            from app.services.ai_documents import resolve_ids
            resolved = await resolve_ids([doc_id])
            if not resolved:
                return []
            doc_id = resolved[0]
        payloads: list[dict[str, Any]] = []
        offset = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
                ),
                limit=page_size,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            # VBQPPL đã bị sửa đổi có một bộ chunk cho mỗi khoảng hiệu lực; đọc lại văn bản
            # thì lấy bản mới nhất (latest), không lặp nội dung các bản cũ.
            payloads.extend(dict(point.payload or {}) for point in points
                            if (point.payload or {}).get("latest", True))
            if offset is None:
                break
        return sorted(payloads, key=lambda p: p.get("chunk_index", 0))

    async def scroll_filtered(self, query_filter: models.Filter, limit: int = 256) -> list[dict[str, Any]]:
        """Payload (kèm text) của các chunk khớp bộ lọc, theo thứ tự trong file gốc."""
        from app.services.ai_documents import restrict
        query_filter = await restrict(query_filter)
        points, _ = await self._client.scroll(collection_name=self.collection, scroll_filter=query_filter,
                                              limit=limit, with_payload=True, with_vectors=False)
        return sorted((dict(p.payload or {}) for p in points), key=lambda p: p.get("chunk_index", 0))

    async def list_documents(self, page_size: int = 512) -> list[dict[str, Any]]:
        """Các tài liệu đang có trong kho, gộp từ chunk về theo doc_id.

        Qdrant không có khái niệm "tài liệu" - chỉ có point. Nên phải quét cả
        collection rồi gộp, và chỉ lấy đúng vài trường payload cần cho danh sách
        chứ không kéo cả `text` về: trường đó là toàn bộ nội dung kho.
        """
        from app.db.ai_session import enabled, repository
        if enabled():
            from app.services.ai_documents import metadata
            async with repository() as repo:
                table = repo.table("KnowledgeDocuments")
                allowed = set(await repo.document_ids())
                rows = await repo.rows("KnowledgeDocuments", table.c.IndexStatus == "Indexed", owned=True)
            return [{"doc_id": f"sql:{row['Id']}",
                     **metadata(row)} for row in rows]
        docs: dict[str, dict[str, Any]] = {}
        offset = None
        while True:
            points, offset = await self._client.scroll(
                collection_name=self.collection,
                limit=page_size,
                offset=offset,
                with_payload=["doc_id", "doc_title", "source", "doc_type", "ingested_at", "latest"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                doc_id = str(payload.get("doc_id") or "")
                if not doc_id:
                    continue
                entry = docs.setdefault(doc_id, {
                    "doc_id": doc_id,
                    "doc_title": str(payload.get("doc_title") or payload.get("source") or doc_id),
                    "source": str(payload.get("source") or ""),
                    "doc_type": str(payload.get("doc_type") or ""),
                    "chunk_count": 0,
                    "ingested_at": payload.get("ingested_at"),
                })
                # VBQPPL bị sửa có một bộ chunk mỗi khoảng hiệu lực: chỉ đếm bản mới nhất.
                if payload.get("latest", True):
                    entry["chunk_count"] += 1
            if offset is None:
                break
        return sorted(docs.values(), key=lambda d: d["doc_title"].lower())

    # ------------------------------------------------------- tìm kiếm ---- #
    async def search_dense(
        self, vector: list[float], limit: int, query_filter: models.Filter | None = None
    ) -> list[models.ScoredPoint]:
        response = await self._client.query_points(
            collection_name=self.collection,
            query=vector,
            using=DENSE_VECTOR,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return response.points

    async def search_sparse(
        self,
        vector_name: str,
        sparse: SparseEmbedding,
        limit: int,
        query_filter: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        if not sparse.indices:
            return []
        response = await self._client.query_points(
            collection_name=self.collection,
            query=to_qdrant_sparse(sparse),
            using=vector_name,
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )
        return response.points

    async def hybrid_search_server_rrf(
        self,
        embedding: HybridEmbedding,
        limit: int,
        branch_limit: int,
        query_filter: models.Filter | None = None,
    ) -> list[models.ScoredPoint]:
        """RRF do Qdrant thực hiện (1 round-trip).

        Dùng khi không cần biết rank của từng nhánh; mặc định pipeline QA dùng
        RRF phía client trong `app.rag.retrieval` để lấy được thông tin debug.
        """
        prefetch = [
            models.Prefetch(query=embedding.dense, using=DENSE_VECTOR,
                            limit=branch_limit, filter=query_filter),
        ]
        for name, sparse in ((LEXICAL_VECTOR, embedding.lexical), (BM25_VECTOR, embedding.bm25)):
            if sparse.indices:
                prefetch.append(
                    models.Prefetch(query=to_qdrant_sparse(sparse), using=name,
                                    limit=branch_limit, filter=query_filter)
                )
        response = await self._client.query_points(
            collection_name=self.collection,
            prefetch=prefetch,
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            limit=limit,
            with_payload=True,
        )
        return response.points


# ---------------------------------------------------------------- filter --- #
def build_filter(
    doc_ids: Iterable[str] | None = None,
    sources: Iterable[str] | None = None,
    doc_types: Iterable[str] | None = None,
) -> models.Filter | None:
    conditions: list[models.Condition] = []
    for key, values in (("doc_id", doc_ids), ("source", sources), ("doc_type", doc_types)):
        values = [v for v in (values or []) if v]
        if values:
            conditions.append(
                models.FieldCondition(key=key, match=models.MatchAny(any=list(values)))
            )
    return models.Filter(must=conditions) if conditions else None


_store: QdrantVectorStore | None = None


def get_vector_store() -> QdrantVectorStore:
    global _store
    if _store is None:
        _store = QdrantVectorStore()
    return _store
