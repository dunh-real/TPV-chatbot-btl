"""Chỉ mục nguồn soạn báo cáo: tách kho chung, bắt buộc tenant/chủ sở hữu/file."""
import hashlib
import json
import time

import anyio
from qdrant_client import models

from app.core.config import get_settings
from app.core.context import current_principal
from app.rag.chunking import Chunker
from app.rag.converter import get_converter
from app.rag.embedding import embed_documents, embed_query
from app.rag.vectorstore import ChunkPoint, QdrantVectorStore
from app.services.integration_errors import IntegrationError
from app.services.object_storage import ObjectStorage


class ReportSource:
    def __init__(self, ref):
        self.ref = ref
        self.cfg = get_settings()
        principal = current_principal()
        if principal.tenant_id is None or not principal.user_id:
            raise IntegrationError("PARTITION_DENIED", "Tài liệu nguồn cần tenant và tài khoản xác định", 403)
        self.tenant, self.owner = principal.tenant_id, str(principal.user_id)
        collection = self.cfg.report_source_collection or self.cfg.qdrant_collection + "_report_sources"
        if collection in {self.cfg.qdrant_collection, self.cfg.qdrant_conversation_collection}:
            raise IntegrationError("CONFIG_INVALID", "Chỉ mục nguồn phải tách khỏi kho và hội thoại", 422)
        self.store = QdrantVectorStore(self.cfg.model_copy(update={"qdrant_collection": collection}))
        self.doc_id = ""
        self.reused = False
        self.count = 0

    def scope(self, *, kind=None, expired=False, document=True):
        terms = [models.FieldCondition(key=key, match=models.MatchValue(value=value))
            for key, value in (("tenant_id", self.tenant), ("owner_id", self.owner))]
        if document:
            terms.append(models.FieldCondition(key="doc_id", match=models.MatchValue(value=self.doc_id)))
        if kind:
            terms.append(models.FieldCondition(key="kind", match=models.MatchValue(value=kind)))
        terms.append(models.FieldCondition(key="expires_at", range=models.Range(
            **({"lt": time.time()} if expired else {"gt": time.time()}))))
        return models.Filter(must=terms)

    async def __aenter__(self):
        try:
            await self.prepare()
            return self
        except BaseException:
            await self.store.close()
            raise

    async def __aexit__(self, *args):
        await self.store.close()

    async def prepare(self):
        if self.ref.size > 20 * 1024**2:
            raise IntegrationError("CONTEXT_TOO_LONG", "Tài liệu nguồn tối đa 20 MB", 413)
        checksum = await anyio.to_thread.run_sync(lambda: ObjectStorage.checksum(self.ref.path))
        signature = {key: getattr(self.cfg, key) for key in ("embedding_model", "dense_vector_size",
            "chunk_min_tokens", "chunk_ideal_tokens", "chunk_max_tokens", "chunk_hard_cap", "chunk_overlap")}
        signature.update(version=1, checksum=checksum, tenant=self.tenant, owner=self.owner)
        self.doc_id = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
        await self.store.ensure_collection()
        for key, schema in (("owner_id", models.PayloadSchemaType.KEYWORD),
                            ("kind", models.PayloadSchemaType.KEYWORD), ("expires_at", models.PayloadSchemaType.FLOAT)):
            await self.store.client.create_payload_index(self.store.collection, key, field_schema=schema, wait=True)
        # Dọn lười chỉ mục hết hạn của chính người gọi, không xóa file gốc MinIO.
        await self.store.client.delete(self.store.collection,
            points_selector=models.FilterSelector(filter=self.scope(expired=True, document=False)), wait=True)
        ready, _ = await self.store.client.scroll(self.store.collection,
            scroll_filter=self.scope(kind="ready"), limit=1, with_payload=True, with_vectors=False)
        if ready:
            self.count = ready[0].payload["chunk_count"]
            actual = await self.store.client.count(self.store.collection, count_filter=self.scope(kind="chunk"), exact=True)
            if actual.count == self.count:
                self.reused = True
                return
        try:
            loaded = await anyio.to_thread.run_sync(lambda: get_converter().convert(self.ref.path))
        except ValueError as exc:
            raise IntegrationError("INVALID_REQUEST", "Không đọc được tài liệu nguồn; hãy kiểm tra định dạng file", 422) from exc
        if not loaded.text.strip():
            raise IntegrationError("NO_CONTEXT", "Tài liệu không có nội dung đọc được", 422)
        if len(loaded.text) > 2_000_000:
            raise IntegrationError("CONTEXT_TOO_LONG", "Tài liệu vượt 2 triệu ký tự trích xuất", 413)
        chunk_cfg = self.cfg.model_copy(update={"chunk_legal_structure": False})
        chunks = await anyio.to_thread.run_sync(lambda: Chunker(chunk_cfg).split(loaded.text))
        if not chunks or len(chunks) > 2000:
            raise IntegrationError("CONTEXT_TOO_LONG", "Tài liệu cần từ 1 đến 2.000 chunk", 413)
        storage = await anyio.to_thread.run_sync(lambda: ObjectStorage().upload(self.ref.path, self.ref.name))
        if storage["checksum_sha256"] != checksum:
            raise IntegrationError("INVALID_REQUEST", "File thay đổi trong khi lập chỉ mục; hãy tải lại", 409)
        expires = time.time() + self.cfg.report_source_ttl_days * 86400
        metadata = dict(tenant_id=self.tenant, owner_id=self.owner, kind="chunk", expires_at=expires,
            checksum_sha256=checksum, storage_bucket=storage["storage_bucket"],
            storage_object_key=storage["storage_object_key"], source_name=self.ref.name)
        await self.store.client.delete(self.store.collection,
            points_selector=models.FilterSelector(filter=self.scope()), wait=True)
        first = None
        for start in range(0, len(chunks), 8):
            batch = chunks[start:start+8]
            vectors = await anyio.to_thread.run_sync(lambda: embed_documents([c.text for c in batch]))
            points = [ChunkPoint(self.doc_id, c.index, c.text, vector,
                {**metadata, "section": c.section}) for c, vector in zip(batch, vectors, strict=True)]
            await self.store.upsert_chunks(points)
            first = first or vectors[0]
        self.count = len(chunks)
        # Chỉ đánh dấu hoàn tất sau khi tất cả chunk được ghi thành công.
        await self.store.upsert_chunks([ChunkPoint(self.doc_id, -1, "", first,
            {**metadata, "kind": "ready", "chunk_count": self.count})])

    async def retrieve(self, question, field):
        query = f"{question}\n{field.label}: {field.definition}"
        vector = await anyio.to_thread.run_sync(lambda: embed_query(query))
        hits = await self.store.hybrid_search_server_rrf(vector, limit=12, branch_limit=16,
                                                        query_filter=self.scope(kind="chunk"))
        if not hits:
            return []
        from app.rag.reranker import get_reranker
        ranked = await anyio.to_thread.run_sync(lambda: get_reranker().rerank(query,
            [p.payload["text"] for p in hits], top_n=4, score_threshold=0.0))
        sources, size = [], 0
        for result in ranked:
            point = hits[result.index]
            payload = point.payload
            source = dict(id=str(point.id), kind="block", text=payload["text"], field_ids=[field.id],
                locator={"file_id": self.ref.file_id, "source_id": self.doc_id,
                    "chunk_index": payload["chunk_index"], "section": payload.get("section", "")})
            length = len(json.dumps(source, ensure_ascii=False))
            if size + length <= 20000:
                sources.append(source)
                size += length
        return sources
