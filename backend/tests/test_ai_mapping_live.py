"""Thử luồng API với SQL/MinIO/Qdrant thật; giả lập embedding, luôn dọn dữ liệu thử."""
import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.live


async def test_real_storage_flow(monkeypatch, tmp_path):
    from app.core.config import get_settings
    from app.core.context import Principal, use_principal
    from app.db import ai_session
    from app.db.ai_repository import AIRepository
    from app.db.erp_auth import ErpAuthRepository
    from app.api import documents, files
    from app.rag import ingestion
    from app.rag.chunking import Chunk
    from app.rag.embedding import HybridEmbedding, SparseEmbedding
    from app.rag.vectorstore import QdrantVectorStore
    from app.services import ai_documents, ai_outputs, ai_mindmaps
    from app.services.conversation import ConversationMemory
    from app.services.object_storage import ObjectStorage

    if not os.getenv("AI_TEST_STORAGE") or not ai_session.enabled():
        pytest.skip("Cần AI_DATABASE_URL và AI_TEST_STORAGE=1")
    cfg = get_settings()
    monkeypatch.setattr(cfg, "qdrant_collection", "tpv_ai_mapping_test_" + uuid.uuid4().hex)
    monkeypatch.setattr(cfg, "doc_card_enabled", False)
    monkeypatch.setattr(cfg, "legal_versioning_enabled", False)
    monkeypatch.setattr(cfg, "output_dir", str(tmp_path))
    store = QdrantVectorStore(cfg)
    pipe = ingestion.IngestionPipeline(cfg, store)
    monkeypatch.setattr(pipe.chunker, "split", lambda text, meta: [Chunk(text, 0, metadata=meta)])
    monkeypatch.setattr(ingestion, "embed_documents", lambda texts: [HybridEmbedding(
        [1.0] + [0.0] * (cfg.dense_vector_size-1), SparseEmbedding([1], [1.0]), SparseEmbedding([2], [1.0])) for _ in texts])
    monkeypatch.setattr(documents, "get_vector_store", lambda: store)
    monkeypatch.setattr(documents, "get_ingestion_pipeline", lambda: pipe)
    app = FastAPI()
    app.include_router(documents.router)
    objects = set()
    original_upload = ObjectStorage.upload
    def upload(self, path, filename):
        meta = original_upload(self, path, filename)
        objects.add((meta["storage_bucket"], meta["storage_object_key"]))
        return meta
    monkeypatch.setattr(ObjectStorage, "upload", upload)
    mapped = await ai_session.tables()
    marker = uuid.uuid4().hex
    try:
        async with ai_session.get_engine().connect() as conn:
            async with AsyncSession(bind=conn) as session:
                access = await ErpAuthRepository(session).find_user(user_id=int(os.getenv("AI_TEST_USER_ID", "407")))
            await conn.rollback()
            assert access and access.is_static_admin
            principal = Principal(access.tenant_id, str(access.user_id), access=access)
            transaction = await conn.begin()
            try:
                with use_principal(principal):
                    repo = AIRepository(conn, mapped)
                    @asynccontextmanager
                    async def scope():
                        yield repo
                    for module in (ai_session, ai_documents, ai_outputs, ai_mindmaps):
                        monkeypatch.setattr(module, "repository", scope)
                    await store.ensure_collection()
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
                        content = ("Tài liệu kiểm thử ánh xạ " + marker).encode()
                        response = await client.post("/api/documents/upload", files={"file": ("mapping.txt", content)})
                        assert response.status_code == 200, response.text
                        doc_id = response.json()["doc_id"]
                        assert (await repo.document(doc_id))["ChunkCount"] == 1
                        assert (await client.get(f"/api/documents/{doc_id}/detail")).status_code == 200
                        original = await client.get(f"/api/documents/{doc_id}/original")
                        assert original.content == content
                        repeated = await client.post("/api/documents/upload", files={"file": ("mapping.txt", content)})
                        assert repeated.json()["already_exists"]
                        memory = ConversationMemory()
                        await memory.append_turns(marker, [("user", "Câu hỏi"), ("assistant", "Trả lời")])
                        assert len(await memory.get_history(marker)) == 2
                        tree = {"id": "root", "title": "Thử", "children": []}
                        await ai_mindmaps.save(doc_id, tree)
                        assert (await ai_mindmaps.load(doc_id))["tree"] == tree
                        await ai_mindmaps.delete(doc_id)
                        assert await ai_mindmaps.load(doc_id) is None
                        path = tmp_path / ("test__t" + str(access.tenant_id) + "__" + marker + ".pptx")
                        path.write_bytes(b"fixture-output-" + marker.encode())
                        @ai_outputs.tracked_workflow("Slide")
                        async def generate(request):
                            return {"output_path": str(path), "brief": "Nội dung thử"}
                        result = await generate("Kiểm thử sản phẩm")
                        assert result["ai_task_id"]
                        path.unlink()  # Tải vẫn thành công khi không còn bản cục bộ.
                        download = await files.output_file_response(path.name)
                        assert download.media_type.endswith("presentation")
                        assert download.stream.read() == b"fixture-output-" + marker.encode()
                        download.stream.close()
                        download.stream.release_conn()
                        other = replace(access, user_id=access.user_id+10000000, is_static_admin=False)
                        with use_principal(replace(principal, user_id=str(other.user_id), access=other)):
                            private = AIRepository(conn, mapped)
                            assert await private.document(doc_id) is None
                            assert await private.history(marker, 20) == []
            finally:
                await transaction.rollback()
            table = mapped["AI_ChatConversations"]
            assert not (await conn.execute(select(table.c.Id).where(table.c.AiConversationId == marker))).first()
    finally:
        await store.client.delete_collection(store.collection)
        await store.close()
        storage = ObjectStorage()
        for bucket, key in objects:
            storage.client.remove_object(bucket, key)
        await ai_session.dispose()
