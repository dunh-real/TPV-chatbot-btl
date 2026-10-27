import csv
import io
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from test_document_search import api
from app.core.context import current_principal, use_principal, Principal
from app.schemas.documents import SaveDocumentList
from app.services import document_lists, document_search
from app.rag.retrieval import RetrievalResult, RetrievedChunk


@pytest.fixture(autouse=True)
def local_lists(monkeypatch, tmp_path):
    monkeypatch.setattr(document_lists, 'get_settings', lambda: SimpleNamespace(saved_documents_dir=str(tmp_path)))
    return tmp_path


async def test_save_reopen_missing_delete_and_no_cross_user(api, local_lists):
    client, store = api
    response = await client.post('/api/documents/saved', json={'name': ' Hồ sơ nhân sự ', 'doc_ids': ['a', 'b', 'a']})
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved['name'] == 'Hồ sơ nhân sự' and saved['doc_ids'] == ['a', 'b']
    saved_id = saved['saved_id']
    assert list(local_lists.rglob('*.json')) and not list(local_lists.rglob('.saving-*'))
    assert len((await client.get('/api/documents/saved')).json()) == 1
    other = replace(current_principal(), user_id='456')
    for identity in [other, replace(current_principal(), tenant_id=999)]:
        with use_principal(identity):
            assert (await client.get('/api/documents/saved')).json() == []
            assert (await client.get('/api/documents/saved/' + saved_id)).status_code == 404
            assert (await client.delete('/api/documents/saved/' + saved_id)).status_code == 404
    await store.delete_document('b')
    reopened = (await client.get('/api/documents/saved/' + saved_id)).json()
    assert [d['doc_id'] for d in reopened['items']] == ['a']
    assert reopened['missing_doc_ids'] == ['b']
    assert (await client.delete('/api/documents/saved/' + saved_id)).status_code == 204
    assert (await client.get('/api/documents/saved')).json() == []


async def test_selection_limits_invalid_id_and_blank_name(api):
    client, _ = api
    for data in [{'name': ' ', 'doc_ids': ['a']}, {'name': 'x', 'doc_ids': []},
                 {'name': 'x', 'doc_ids': ['a'] * 201}]:
        assert (await client.post('/api/documents/saved', json=data)).status_code == 422
    assert (await client.get('/api/documents/saved/not-a-valid-id')).status_code == 422
    assert (await client.post('/api/documents/export', json={'doc_ids': ['a'] * 201})).status_code == 422


async def test_atomic_write_failure_keeps_old_file(api, local_lists, monkeypatch):
    request = SaveDocumentList(name='x', doc_ids=['a'])
    first = document_lists.save(request)
    def fail(*args):
        raise OSError('disk failure')
    monkeypatch.setattr(document_lists.os, 'replace', fail)
    with pytest.raises(OSError):
        document_lists.save(request)
    assert document_lists.load(first.saved_id).doc_ids == ['a']
    assert len(list(local_lists.rglob('*.json'))) == 1 and not list(local_lists.rglob('.saving-*'))


async def test_export_unicode_formula_missing_and_stored_metadata(api):
    client, store = api
    await store.client.set_payload(store.collection, {'doc_title': '  =HYPERLINK("evil")'}, points=[1, 2])
    response = await client.post('/api/documents/export', json={
        'doc_ids': ['a', 'missing', 'a'], 'title': 'client value must not be used'})
    assert response.status_code == 200
    assert response.content.startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
    assert len(rows) == 2
    assert rows[0]['ten_tai_lieu'].startswith("'  =")
    assert rows[0]['so_hieu'] == '123/QD'
    assert rows[0]['ban_goc_api'] == '/api/documents/a/original'
    assert rows[1]['trang_thai'] == 'missing'
    assert 'client value' not in response.text and '/private/' not in response.text


async def test_all_routes_require_read_permission(api):
    client, _ = api
    with use_principal(Principal()):
        for method, path, kwargs in [
            ('GET', '/api/documents/a/related', {}), ('GET', '/api/documents/saved', {}),
            ('POST', '/api/documents/saved', {'json': {'name': 'x', 'doc_ids': ['a']}}),
            ('GET', '/api/documents/saved/' + 'a' * 32, {}),
            ('DELETE', '/api/documents/saved/' + 'a' * 32, {}),
            ('POST', '/api/documents/export', {'json': {'doc_ids': ['a']}}),
        ]:
            assert (await client.request(method, path, **kwargs)).status_code == 403


async def test_related_excludes_self_and_uses_bounded_representative_text(api, monkeypatch):
    client, _ = api
    captured = {}
    async def retrieve(**kwargs):
        captured.update(kwargs)
        return RetrievalResult(chunks=[RetrievedChunk('3', 'content', .8, .02, {'doc_id': 'b'})], queries=['x'])
    monkeypatch.setattr(document_search, 'get_retriever', lambda: SimpleNamespace(retrieve=retrieve))
    monkeypatch.setattr(document_search, '_legal_candidates', _disabled)
    result = await client.get('/api/documents/a/related?limit=2')
    assert result.status_code == 200, result.text
    data = result.json()
    assert [item['doc_id'] for item in data['same_topic']] == ['b']
    assert data['same_number'] == data['legal_relations'] == [] and data['legal_status'] == 'disabled'
    assert any(c.key == 'doc_id' and c.match.any == ['a'] for c in captured['query_filter'].must_not)
    assert len(captured['query']) <= 4000
    assert (await client.get('/api/documents/unknown/related')).status_code == 404


async def _disabled(*args):
    return [], [], 'disabled'


async def test_legal_candidates_use_registered_number_and_applied_relations(monkeypatch):
    from app.db import models as db_models
    if not hasattr(db_models, 'LegalDocument'):
        pytest.skip('Bản yuu cũ chưa có tầng pháp luật; ca này chạy trên Mac với SQL app thử riêng')
    from app.db.models import Base, LegalDocument, LegalAction
    from app.db import session as db_session
    from app.core import config
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all([LegalDocument(legal_id='law-a', doc_id='a', so_hieu='123/QD', ten='A'),
                   LegalDocument(legal_id='law-b', doc_id='b', so_hieu='123/QD', ten='B'),
                   LegalDocument(legal_id='law-c', doc_id='c', so_hieu='456/QD', ten='C')])
        s.add_all([LegalAction(action_id='1', source_legal_id='law-c', target_legal_id='law-a', op='sua_doi', status='applied'),
                   LegalAction(action_id='2', source_legal_id='law-b', target_legal_id='law-a', op='bai_bo', status='quarantined')])
        await s.commit()
    @asynccontextmanager
    async def scope():
        async with factory() as s:
            yield s
    monkeypatch.setattr(config, 'get_settings', lambda: SimpleNamespace(legal_versioning_enabled=True))
    monkeypatch.setattr(db_session, 'session_scope', scope)
    try:
        numbers, links, status = await document_search._legal_candidates('a', 5)
        assert numbers == ['b'] and links == [('c', 'sua_doi', 'incoming')] and status == 'ok'
        assert (await document_search._legal_candidates('missing', 5))[2] == 'not_registered'
    finally:
        await engine.dispose()


async def test_missing_legal_layer_does_not_break_metadata(monkeypatch):
    from app.db import models
    from app.api import documents
    monkeypatch.setattr(documents, 'get_settings', lambda: SimpleNamespace(legal_versioning_enabled=True))
    monkeypatch.delattr(models, 'LegalDocument', raising=False)
    assert await documents._legal_info(['a']) == {}
