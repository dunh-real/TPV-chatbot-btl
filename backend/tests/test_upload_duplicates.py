"""Upload trùng bỏ qua OCR/embedding; force vẫn nạp lại."""
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from qdrant_client import AsyncQdrantClient, models

from app.api import documents
from app.core.config import Settings
from app.rag.ingestion import IngestResult, make_doc_id_file
from app.rag.vectorstore import QdrantVectorStore


@pytest.fixture
async def upload_app(monkeypatch, tmp_path, fake_object_storage):
    store = QdrantVectorStore(Settings(), client=AsyncQdrantClient(':memory:'))
    await store.ensure_collection()
    monkeypatch.setattr(documents, 'get_vector_store', lambda: store)
    monkeypatch.setattr(documents.storage, 'root', lambda kind: tmp_path)
    app = FastAPI()
    app.include_router(documents.router)
    for route in documents.router.routes:
        for dependency in route.dependencies:
            app.dependency_overrides[dependency.dependency] = lambda: None
    state = SimpleNamespace(calls=0)

    class Pipeline:
        async def ingest_file(self, path, doc_type='', **kwargs):
            state.calls += 1
            return IngestResult(make_doc_id_file(path.name, path), path.stem, 3, 10, path.name)

    monkeypatch.setattr(documents, 'get_ingestion_pipeline', lambda: Pipeline())
    path = tmp_path / 'known.md'
    path.write_bytes(b'known content')
    doc_id = make_doc_id_file(path.name, path)
    await store.client.upsert(store.collection, [
        models.PointStruct(id=i, vector={}, payload={
            'doc_id': doc_id, 'doc_title': 'Stored title', 'source': 'known.md',
        }) for i in (1, 2)
    ])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield client, state, store, doc_id
    await store.close()


async def test_duplicate_returns_existing_without_ingesting(upload_app):
    client, state, _, doc_id = upload_app
    response = await client.post('/api/documents/upload', files={'file': ('known.md', b'known content')})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['already_exists'] is True
    assert body['doc_id'] == doc_id
    assert body['doc_title'] == 'Stored title'
    assert body['chunk_count'] == 2
    assert state.calls == 0


@pytest.mark.parametrize('name,content,form', [
    ('known.md', b'changed content', {}),
    ('known.md', b'known content', {'force_reingest': 'true'}),
])
async def test_new_changed_or_forced_file_is_ingested(upload_app, name, content, form):
    client, state, _, _ = upload_app
    response = await client.post('/api/documents/upload', files={'file': (name, content)}, data=form)
    assert response.status_code == 200, response.text
    assert response.json()['already_exists'] is False
    assert response.json()['chunk_count'] == 3
    assert state.calls == 1


async def test_lookup_failure_does_not_ingest(upload_app, monkeypatch):
    client, state, store, _ = upload_app
    async def unavailable(*args, **kwargs):
        raise RuntimeError('Qdrant unavailable')
    monkeypatch.setattr(store.client, 'scroll', unavailable)
    response = await client.post('/api/documents/upload', files={'file': ('known.md', b'known content')})
    assert response.status_code == 502
    assert state.calls == 0

async def test_same_bytes_different_name_is_duplicate(upload_app):
    client, state, _, doc_id = upload_app
    response = await client.post('/api/documents/upload', files={'file': ('renamed.md', b'known content')})
    assert response.status_code == 200, response.text
    assert response.json()['already_exists'] is True
    assert response.json()['doc_id'] == doc_id
    assert state.calls == 0

async def test_legacy_id_is_still_recognized(upload_app, tmp_path):
    import hashlib
    client, state, store, _ = upload_app
    legacy_id = hashlib.sha256(b'legacy.md|legacy content').hexdigest()[:16]
    await store.client.upsert(store.collection, [models.PointStruct(
        id=3, vector={}, payload={'doc_id': legacy_id, 'doc_title': 'Legacy', 'source': 'legacy.md'})])
    response = await client.post('/api/documents/upload', files={'file': ('legacy.md', b'legacy content')})
    assert response.status_code == 200, response.text
    assert response.json()['already_exists'] is True
    assert response.json()['doc_id'] == legacy_id
    assert state.calls == 0


async def test_existing_object_ingested_without_upload(upload_app, fake_object_storage):
    client, state, _, _ = upload_app
    fake_object_storage.objects[('test', 'folder/a.md')] = b'new content'
    response = await client.post('/api/documents/ingest-minio', json={
        'bucket': 'test', 'object_key': 'folder/a.md'})
    assert response.status_code == 200, response.text
    assert state.calls == 1 and len(fake_object_storage.objects) == 1
    assert all(not p.exists() for p in fake_object_storage.paths)


async def test_duplicate_links_all_chunks_without_reembedding(upload_app, fake_object_storage):
    client, state, store, doc_id = upload_app
    response = await client.post('/api/documents/upload', files={'file': ('known.md', b'known content')})
    assert response.status_code == 200
    chunks = await store.scroll_document(doc_id)
    assert len(chunks) == 2
    assert all(c['storage_bucket'] == 'test' for c in chunks)
    assert state.calls == 0
    assert all(not p.exists() for p in fake_object_storage.paths)


async def test_different_source_is_conflict_and_keeps_original(upload_app, fake_object_storage):
    client, state, store, doc_id = upload_app
    await client.post('/api/documents/upload', files={'file': ('known.md', b'known content')})
    before = await store.scroll_document(doc_id)
    fake_object_storage.objects[('test', 'other/known.md')] = b'known content'
    response = await client.post('/api/documents/ingest-minio', json={
        'bucket': 'test', 'object_key': 'other/known.md', 'force_reingest': True})
    assert response.status_code == 409
    assert await store.scroll_document(doc_id) == before
    assert state.calls == 0


async def test_ingest_failure_keeps_object_cleans_temp(upload_app, fake_object_storage, monkeypatch):
    client, _, _, _ = upload_app
    class Broken:
        async def ingest_file(self, *args, **kwargs):
            raise RuntimeError('failed')
    monkeypatch.setattr(documents, 'get_ingestion_pipeline', lambda: Broken())
    response = await client.post('/api/documents/upload', files={'file': ('a.md', b'new content')})
    assert response.status_code == 502
    assert len(fake_object_storage.objects) == 1
    assert all(not p.exists() for p in fake_object_storage.paths)


async def test_reject_bucket_before_ingest(upload_app):
    client, state, _, _ = upload_app
    response = await client.post('/api/documents/ingest-minio', json={
        'bucket': 'blocked', 'object_key': 'a.md'})
    assert response.status_code == 403 and state.calls == 0


async def test_metadata_reaches_pipeline(upload_app, monkeypatch):
    client, _, _, _ = upload_app
    captured = {}
    class Pipeline:
        async def ingest_file(self, path, **kwargs):
            captured.update(kwargs)
            return IngestResult(kwargs['doc_id'], 'a', 1, 1, kwargs['source'])
    monkeypatch.setattr(documents, 'get_ingestion_pipeline', lambda: Pipeline())
    response = await client.post('/api/documents/upload', files={'file': ('a.md', b'new content')})
    assert response.status_code == 200
    assert captured['source'] == 'a.md'
    assert len(captured['metadata']['checksum_sha256']) == 64
    assert captured['metadata']['storage_object_key'].startswith('uploads/')


async def test_missing_write_permission_blocks_both_routes(fake_object_storage):
    from app.core.context import Principal, use_principal
    app = FastAPI()
    app.include_router(documents.router)
    from app.agents.quyen import ThieuQuyen
    from fastapi.responses import JSONResponse
    @app.exception_handler(ThieuQuyen)
    async def forbidden(request, exc):
        return JSONResponse(status_code=403, content={'detail': exc.message})
    from app.db.erp_auth import AccountAccess
    no_access = AccountAccess(123, None, "no-access", "", "", permissions=frozenset())
    with use_principal(Principal(user_id="123", access=no_access)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for route, kwargs in [('/api/documents/upload', {'files': {'file': ('a.md', b'a')}}),
                                   ('/api/documents/ingest-minio', {'json': {'bucket': 'test', 'object_key': 'a.md'}})]:
                assert (await client.post(route, **kwargs)).status_code == 403
    assert not fake_object_storage.objects
