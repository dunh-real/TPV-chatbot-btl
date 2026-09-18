"""Lưu hội thoại bền trong một collection Qdrant riêng.

Hai tầng, hai vai khác nhau:
    ConversationStore  - bản gốc. Một point mỗi lượt, không mất khi restart,
                         không phụ thuộc tiến trình nào.
    ConversationMemory - mặt tiền cho phần còn lại của app: đọc cache trước cho
                         nhanh, cache trống thì dựng lại từ Qdrant rồi hâm nóng.

Tách khỏi `tpv_documents` vì hai thứ khác hẳn nhau: chunk tài liệu cần 3 biểu
diễn để hybrid search, lượt chat chỉ cần dense để tìm lại hội thoại cũ. Chung
collection thì mọi truy vấn RAG đều phải lọc bỏ lượt chat, sai một lần là câu
trả lời trích dẫn chính mình.

Điểm khác biệt quan trọng với `CacheService`: đây là nguồn sự thật, nên lỗi ghi
được log ở mức ERROR kèm conversation_id. Nuốt im lặng như cache chính là cái
bẫy đã làm bộ nhớ chat biến mất suốt thời gian qua.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

import anyio
from qdrant_client import AsyncQdrantClient, models

from app.core.config import Settings, get_settings
from app.rag.vectorstore import DENSE_VECTOR
from app.services.cache import CacheService, get_cache

logger = logging.getLogger(__name__)

# Namespace riêng để id lượt chat không bao giờ đụng id chunk tài liệu.
_TURN_NAMESPACE = uuid.UUID("3b1f6a52-0c4d-4b7e-9a21-7c5e8d4f1a90")

ROLES = ("user", "assistant")


@dataclass(slots=True)
class Turn:
    """Một lượt đã đọc lại từ Qdrant."""

    role: str
    content: str
    ts: float
    seq: int

    def as_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content, "ts": self.ts}


def _point_id(conversation_id: str, ts: float, seq: int) -> str:
    """Id tất định: ghi lại cùng một lượt không sinh bản sao."""
    return str(uuid.uuid5(_TURN_NAMESPACE, f"{conversation_id}:{ts:.6f}:{seq}"))


class ConversationStore:
    def __init__(self, settings: Settings | None = None,
                 client: AsyncQdrantClient | None = None) -> None:
        self.settings = settings or get_settings()
        self.collection = self.settings.qdrant_conversation_collection
        self._client = client or AsyncQdrantClient(
            url=self.settings.qdrant_url,
            api_key=self.settings.qdrant_api_key,
            timeout=int(self.settings.qdrant_timeout),
        )

    async def close(self) -> None:
        await self._client.close()

    # --------------------------------------------------------- schema ---- #
    async def ensure_collection(self, recreate: bool = False) -> None:
        exists = await self._client.collection_exists(self.collection)
        if exists and recreate:
            await self._client.delete_collection(self.collection)
            exists = False
        if not exists:
            # Chỉ dense: lượt chat được tìm theo ngữ nghĩa, không cần nhánh
            # lexical/BM25 như chunk tài liệu.
            await self._client.create_collection(
                collection_name=self.collection,
                vectors_config={
                    DENSE_VECTOR: models.VectorParams(
                        size=self.settings.dense_vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
            )
            logger.info("Đã tạo collection hội thoại %s", self.collection)

        for field_name, schema in (
            ("conversation_id", models.PayloadSchemaType.KEYWORD),
            ("ts", models.PayloadSchemaType.FLOAT),
        ):
            try:
                await self._client.create_payload_index(
                    collection_name=self.collection, field_name=field_name,
                    field_schema=schema, wait=True,
                )
            except Exception as exc:  # index đã tồn tại -> bỏ qua
                logger.debug("Bỏ qua tạo index %s: %s", field_name, exc)

    # ------------------------------------------------------------ ghi ---- #
    async def append_turns(self, conversation_id: str,
                           turns: list[tuple[str, str]]) -> int:
        """Ghi nhiều lượt trong một lần: một lần nhúng, một lần upsert.

        Cặp hỏi-đáp luôn được ghi cùng nhau nên gộp lại rẻ hơn hẳn gọi hai lần.
        """
        turns = [(role, content) for role, content in turns
                 if role in ROLES and content]
        if not turns:
            return 0

        from app.rag.embedding import embed_documents

        texts = [content for _, content in turns]
        try:
            embeddings = await anyio.to_thread.run_sync(embed_documents, texts)
        except Exception as exc:  # noqa: BLE001
            logger.error("Không nhúng được lượt hội thoại %s: %s", conversation_id, exc)
            return 0

        ts = time.time()
        points = [
            models.PointStruct(
                id=_point_id(conversation_id, ts, seq),
                vector={DENSE_VECTOR: embedding.dense},
                payload={"conversation_id": conversation_id, "role": role,
                         "content": content, "ts": ts, "seq": seq},
            )
            for seq, ((role, content), embedding) in enumerate(zip(turns, embeddings, strict=True))
        ]
        try:
            await self._client.upsert(collection_name=self.collection,
                                      points=points, wait=True)
        except Exception as exc:  # noqa: BLE001
            logger.error("MẤT %d lượt của hội thoại %s - Qdrant không ghi được: %s",
                         len(points), conversation_id, exc)
            return 0
        return len(points)

    # ------------------------------------------------------------ đọc ---- #
    async def get_history(self, conversation_id: str,
                          limit: int | None = None) -> list[dict[str, str]]:
        """Các lượt gần nhất, xếp theo thời gian tăng dần."""
        limit = limit or self.settings.history_max_turns
        turns = await self._scroll(conversation_id)
        turns.sort(key=lambda t: (t.ts, t.seq))
        return [t.as_dict() for t in turns[-limit:]]

    async def _scroll(self, conversation_id: str, page_size: int = 256) -> list[Turn]:
        """Toàn bộ lượt của một hội thoại.

        Sắp xếp làm ở phía Python chứ không dùng `order_by`: Qdrant bản nhúng
        (dùng trong test) không có index payload nên `order_by` không chạy, mà
        một hội thoại chỉ vài chục lượt - sắp ở đâu cũng như nhau.
        """
        condition = models.Filter(must=[models.FieldCondition(
            key="conversation_id", match=models.MatchValue(value=conversation_id))])

        turns: list[Turn] = []
        offset = None
        try:
            while True:
                points, offset = await self._client.scroll(
                    collection_name=self.collection, scroll_filter=condition,
                    limit=page_size, offset=offset, with_payload=True, with_vectors=False,
                )
                turns.extend(
                    Turn(role=str(p.payload.get("role", "")),
                         content=str(p.payload.get("content", "")),
                         ts=float(p.payload.get("ts", 0.0)),
                         seq=int(p.payload.get("seq", 0)))
                    for p in points if p.payload
                )
                if offset is None:
                    break
        except Exception as exc:  # noqa: BLE001 - Qdrant chết không được làm sập chat
            logger.error("Không đọc được hội thoại %s: %s", conversation_id, exc)
            return []
        return turns

    async def search(self, query: str, limit: int = 5,
                     conversation_id: str | None = None) -> list[dict[str, Any]]:
        """Tìm lượt cũ theo ngữ nghĩa - lý do chọn Qdrant thay vì một bảng SQL."""
        from app.rag.embedding import embed_query

        condition = None
        if conversation_id:
            condition = models.Filter(must=[models.FieldCondition(
                key="conversation_id", match=models.MatchValue(value=conversation_id))])
        try:
            embedding = await anyio.to_thread.run_sync(embed_query, query)
            result = await self._client.query_points(
                collection_name=self.collection, query=embedding.dense,
                using=DENSE_VECTOR, limit=limit, query_filter=condition, with_payload=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Tìm hội thoại cũ thất bại: %s", exc)
            return []
        return [{**(p.payload or {}), "score": p.score} for p in result.points]

    async def clear_history(self, conversation_id: str) -> None:
        try:
            await self._client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(filter=models.Filter(
                    must=[models.FieldCondition(key="conversation_id",
                                                match=models.MatchValue(value=conversation_id))])),
                wait=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Không xoá được hội thoại %s: %s", conversation_id, exc)

    async def count(self) -> int:
        result = await self._client.count(self.collection, exact=True)
        return result.count


class ConversationMemory:
    """Cache đọc trước, Qdrant là bản gốc.

    Cache trống không còn đồng nghĩa với "hội thoại mới": nó có thể chỉ là vừa
    restart, hoặc request rơi vào worker khác. Nên trống thì dựng lại từ Qdrant
    rồi hâm nóng cache cho các lượt sau.
    """

    def __init__(self, cache: CacheService | None = None,
                 store: ConversationStore | None = None) -> None:
        self._cache = cache
        self._store = store

    @property
    def cache(self) -> CacheService:
        if self._cache is None:
            self._cache = get_cache()
        return self._cache

    @property
    def store(self) -> ConversationStore:
        if self._store is None:
            self._store = ConversationStore()
        return self._store

    async def get_history(self, conversation_id: str,
                          limit: int | None = None) -> list[dict[str, str]]:
        if not conversation_id:
            return []
        cached = await self.cache.get_history(conversation_id, limit=limit)
        if cached:
            return cached

        history = await self.store.get_history(conversation_id, limit=limit)
        for turn in history:
            await self.cache.append_turn(conversation_id, turn["role"], turn["content"])
        if history:
            logger.info("Dựng lại %d lượt của hội thoại %s từ Qdrant",
                        len(history), conversation_id)
        return history

    async def append_turns(self, conversation_id: str,
                           turns: list[tuple[str, str]]) -> None:
        if not conversation_id:
            return
        for role, content in turns:
            await self.cache.append_turn(conversation_id, role, content)
        await self.store.append_turns(conversation_id, turns)

    async def clear_history(self, conversation_id: str) -> None:
        await self.cache.clear_history(conversation_id)
        await self.store.clear_history(conversation_id)


_memory: ConversationMemory | None = None


def get_memory() -> ConversationMemory:
    global _memory
    if _memory is None:
        _memory = ConversationMemory()
    return _memory
