"""Kiểm tra luật dùng chung nhưng vẫn cách ly tài liệu ERP và bộ lọc người dùng."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from qdrant_client import QdrantClient, models
from app.services import ai_documents, shared_legal
from app.core import runtime


@pytest.mark.parametrize('allowed',[['private94'],['private64']])
async def test_shared_or_tenant_filter(monkeypatch,allowed):
    repo=SimpleNamespace(document_ids=AsyncMock(return_value=allowed))
    @asynccontextmanager
    async def repository():yield repo
    monkeypatch.setattr(ai_documents,'repository',repository)
    monkeypatch.setattr(ai_documents,'enabled',lambda:True)
    monkeypatch.setattr(shared_legal,'metadata',AsyncMock(return_value={}))
    q=QdrantClient(':memory:');q.create_collection('test',vectors_config=models.VectorParams(size=1,distance=models.Distance.COSINE))
    payloads=[{'doc_id':'law','knowledge_scope':'shared_legal','legal_index_status':'ready'},
        {'doc_id':'private94'},{'doc_id':'private64'},
        {'doc_id':'unfinished','knowledge_scope':'shared_legal','legal_index_status':'building'},
        {'doc_id':'fake','knowledge_scope':'shared_legal','legal_index_status':'ready','document_id':422},
        {'doc_id':'unregistered'}]
    q.upsert('test',[models.PointStruct(id=i,vector=[1.0],payload=p) for i,p in enumerate(payloads)])
    result=await ai_documents.restrict()
    points,_=q.scroll('test',scroll_filter=result,limit=20)
    assert {p.payload['doc_id'] for p in points}=={'law',*allowed}
    q.close()


async def test_doc_ids_do_not_expand_to_all_shared(monkeypatch):
    async def document(key):return {'AiServerDocId':'private94'} if key=='sql:422' else None
    repo=SimpleNamespace(document_ids=AsyncMock(return_value=['private94']),document=document)
    @asynccontextmanager
    async def repository():yield repo
    monkeypatch.setattr(ai_documents,'repository',repository);monkeypatch.setattr(ai_documents,'enabled',lambda:True)
    monkeypatch.setattr(shared_legal,'metadata',AsyncMock(side_effect=lambda keys:{k:{} for k in keys if k=='law'}))
    assert await ai_documents.resolve_ids(['law','sql:422','other-private','sql:999'])==['private94','law']
    q=QdrantClient(':memory:');q.create_collection('test',vectors_config=models.VectorParams(size=1,distance=models.Distance.COSINE))
    q.upsert('test',[models.PointStruct(id=1,vector=[1.0],payload={'doc_id':'law','knowledge_scope':'shared_legal','legal_index_status':'ready'}),models.PointStruct(id=2,vector=[1.0],payload={'doc_id':'private94'})])
    result=await ai_documents.restrict(models.Filter(must=[models.FieldCondition(key='doc_id',match=models.MatchAny(any=['sql:422']))]))
    points,_=q.scroll('test',scroll_filter=result)
    assert [p.payload['doc_id'] for p in points]==['private94']
    q.close()


async def test_normal_ingest_cannot_replace_shared_document(monkeypatch):
    monkeypatch.setattr(shared_legal,'metadata',AsyncMock(return_value={'0123456789abcdef':{}}))
    write=AsyncMock()
    wrapped=ai_documents.tracked_ingestion(write)
    with pytest.raises(PermissionError):
        await wrapped(None,'Nội dung khác','Tên',doc_id='0123456789abcdef')
    write.assert_not_awaited()


async def test_public_id_keeps_shared_id(monkeypatch):
    repo=SimpleNamespace(document=AsyncMock(return_value=None))
    @asynccontextmanager
    async def repository():yield repo
    monkeypatch.setattr(ai_documents,'repository',repository);monkeypatch.setattr(ai_documents,'enabled',lambda:True)
    monkeypatch.setattr(shared_legal,'metadata',AsyncMock(return_value={'law':{'doc_title':'Luật'}}))
    assert await ai_documents.public_ids(['law'])=={'law':'law'}
    assert await ai_documents.document_metadata(['law'])=={'law':{'doc_title':'Luật'}}
