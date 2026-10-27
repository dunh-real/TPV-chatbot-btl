import io
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from minio.error import S3Error
from qdrant_client import AsyncQdrantClient, models

from app.api import documents
from app.agents.quyen import ThieuQuyen
from app.core.config import Settings
from app.core.context import Principal, use_principal
from app.rag.retrieval import RetrievedChunk, RetrievalResult
from app.rag.vectorstore import QdrantVectorStore
from app.services import document_search


@pytest.fixture
async def api(monkeypatch):
    store = QdrantVectorStore(Settings(), client=AsyncQdrantClient(':memory:'))
    await store.ensure_collection()
    await store.client.upsert(store.collection, [models.PointStruct(id=i, vector={}, payload={
        'doc_id': doc, 'doc_title': 'Title ' + doc, 'source': '/private/source/' + doc + '.pdf',
        **({'storage_bucket': 'test', 'storage_object_key': 'folder/a.pdf',
            'original_filename': 'a.pdf'} if doc == 'a' else {}),
        **({'latest': False} if i == 4 else {}),
    }) for i, doc in [(1, 'a'), (2, 'a'), (3, 'b'), (4, 'a')]])
    monkeypatch.setattr(documents, 'get_vector_store', lambda: store)
    monkeypatch.setattr(document_search, 'get_vector_store', lambda: store)
    async def legal(ids):
        return {'a': {'so_hieu': '123/QD', 'loai': 'Quyet dinh', 'ngay_hieu_luc': '2026-01-01'}}
    monkeypatch.setattr(documents, '_legal_info', legal)
    app = FastAPI()
    app.include_router(documents.router)
    @app.exception_handler(ThieuQuyen)
    async def forbidden(request, exc):
        return JSONResponse(status_code=403, content={'detail': exc.message})
    from app.db.erp_auth import AccountAccess
    access = AccountAccess(123, None, 'read-only', '', '', permissions=frozenset({'Ai.AiChatbot'}))
    with use_principal(Principal(user_id='123', access=access)):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, store
    await store.close()


async def test_group_rank_filters_and_hide_internal_metadata(api, monkeypatch):
    client, _ = api
    captured = {}
    async def retrieve(**kwargs):
        captured.update(kwargs)
        return RetrievalResult(chunks=[RetrievedChunk(str(i), 'Snippet', score, rrf,
            {'doc_id': doc, 'section': 'Dieu 1', 'page': 4})
            for i, doc, score, rrf in [(1, 'a', .8, .03), (2, 'a', .9, .02), (3, 'b', .7, .04)]], queries=['query'])
    monkeypatch.setattr(document_search, 'get_retriever', lambda: SimpleNamespace(retrieve=retrieve))
    response = await client.post('/api/documents/search', json={
        'query': 'query', 'filters': {'doc_types': ['pdf']}, 'limit': 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['returned_count'] == body['candidate_documents'] == 2
    assert [d['doc_id'] for d in body['items']] == ['a', 'b']
    first = body['items'][0]
    assert first['score'] == .9 and len(first['snippets']) == 2 and first['snippets'][0]['page'] == 4
    assert first['chunk_count'] == 2  # Historical snapshot excluded.
    assert first['so_hieu'] == '123/QD'
    assert first['source'] == 'a.pdf'
    assert 'storage_bucket' not in response.text and '/private/' not in response.text
    assert captured['query_filter'].must[0].key == 'doc_type'
    response = await client.post('/api/documents/search', json={'query': 'query', 'use_rerank': False})
    assert response.json()['score_kind'] == 'rrf'
    assert response.json()['items'][0]['doc_id'] == 'b'


async def test_detail_and_legacy_missing_link(api):
    client, _ = api
    body = (await client.get('/api/documents/a/detail')).json()
    assert body['original_available'] and body['original_url'] == '/api/documents/a/original'
    assert body['chunk_count'] == 2 and body['ngay_hieu_luc'] == '2026-01-01'
    legacy = (await client.get('/api/documents/b/detail')).json()
    assert not legacy['original_available'] and legacy['original_url'] is None
    assert (await client.get('/api/documents/b/original')).status_code == 404
    assert (await client.get('/api/documents/absent/detail')).status_code == 404


async def test_original_bytes_headers_and_cleanup(api, monkeypatch):
    client, _ = api
    stream = Mock(wraps=io.BytesIO(b'%PDF-original'))
    stream.release_conn = Mock()
    opened = Mock(return_value=stream)
    monkeypatch.setattr(documents, 'ObjectStorage', lambda: SimpleNamespace(open=opened))
    response = await client.get('/api/documents/a/original')
    assert response.content == b'%PDF-original'
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'].startswith('inline;')
    opened.assert_called_once_with('test', 'folder/a.pdf')
    stream.close.assert_called_once()
    stream.release_conn.assert_called_once()


@pytest.mark.parametrize('error,status', [
    (PermissionError(), 403), (RuntimeError('secret'), 502),
    (S3Error(response=None, code='NoSuchKey', message='missing', resource='/', request_id='req', host_id='host'), 404),
])
async def test_storage_errors(api, monkeypatch, error, status):
    client, _ = api
    monkeypatch.setattr(documents, 'ObjectStorage', lambda: SimpleNamespace(open=Mock(side_effect=error)))
    response = await client.get('/api/documents/a/original')
    assert response.status_code == status and 'secret' not in response.text


async def test_close_on_disconnect_before_first_chunk():
    from starlette.requests import ClientDisconnect
    stream = Mock()
    response = documents._OriginalResponse(stream)
    async def send(message):
        raise OSError('disconnected')
    async def receive():
        return {'type': 'http.disconnect'}
    with pytest.raises(ClientDisconnect):
        await response({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    stream.close.assert_called_once()
    stream.release_conn.assert_called_once()


async def test_close_on_stream_read_error():
    stream = Mock()
    stream.read.side_effect = RuntimeError('read failed')
    async def send(message):
        pass
    async def receive():
        return {'type': 'http.disconnect'}
    with pytest.raises(RuntimeError):
        await documents._OriginalResponse(stream)({'type': 'http', 'asgi': {'spec_version': '2.4'}}, receive, send)
    stream.close.assert_called_once()
    stream.release_conn.assert_called_once()


async def test_all_routes_deny_missing_read_permission(api):
    client, _ = api
    with use_principal(Principal()):
        for path in ['/api/documents/a/detail', '/api/documents/a/original']:
            assert (await client.get(path)).status_code == 403
        assert (await client.post('/api/documents/search', json={'query': 'x'})).status_code == 403


async def test_empty_results_and_validation(api, monkeypatch):
    client, _ = api
    async def retrieve(**kwargs):
        return RetrievalResult(chunks=[], queries=['x'])
    monkeypatch.setattr(document_search, 'get_retriever', lambda: SimpleNamespace(retrieve=retrieve))
    assert (await client.post('/api/documents/search', json={'query': 'x'})).json()['items'] == []
    assert (await client.post('/api/documents/search', json={'query': 'x', 'limit': 0})).status_code == 422
