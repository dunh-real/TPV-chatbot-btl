"""Chỉ mục nguồn riêng: lọc tài khoản/tenant/file, tái dùng và hồi phục nạp dở."""
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from qdrant_client import AsyncQdrantClient

from app.core.config import get_settings
from app.core.context import Principal, use_principal
from app.rag.embedding import HybridEmbedding, SparseEmbedding
from app.rag.vectorstore import QdrantVectorStore
from app.services import report_sources
from app.services.storage import FileRef


@pytest.fixture
async def source_env(tmp_path, monkeypatch):
    cfg = get_settings().model_copy(update=dict(qdrant_collection='knowledge',
        report_source_collection='private_sources', dense_vector_size=2))
    client = AsyncQdrantClient(':memory:')
    stores = []
    def store(settings):
        result = QdrantVectorStore(settings, client=client)
        result.close = AsyncMock()
        stores.append(result)
        return result
    monkeypatch.setattr(report_sources, 'get_settings', lambda: cfg)
    monkeypatch.setattr(report_sources, 'QdrantVectorStore', store)
    path = tmp_path/'nguon.txt'; path.write_text('nguồn kiểm thử', encoding='utf-8')
    ref = FileRef('upload:nguon.txt', path, 'upload')
    text = 'Chế độ giao ban lúc 8 giờ sáng thứ Hai. ' * 2000
    convert = Mock(return_value=SimpleNamespace(text=text))
    monkeypatch.setattr(report_sources, 'get_converter', lambda: SimpleNamespace(convert=convert))
    chunks = [SimpleNamespace(index=i, text=f'Phần {i}: '+text[i*1000:(i+1)*1000], section=f'Mục {i}') for i in range(12)]
    monkeypatch.setattr(report_sources, 'Chunker', lambda _: SimpleNamespace(split=lambda _: chunks))
    vector = HybridEmbedding(dense=[1.,0.], lexical=SparseEmbedding(indices=[1],values=[1.]),
                             bm25=SparseEmbedding(indices=[2],values=[1.]))
    monkeypatch.setattr(report_sources, 'embed_documents', lambda texts: [vector]*len(texts))
    monkeypatch.setattr(report_sources, 'embed_query', lambda _: vector)
    class Storage:
        @staticmethod
        def checksum(path):return hashlib.sha256(path.read_bytes()).hexdigest()
        def upload(self,path,name):return dict(checksum_sha256=self.checksum(path),storage_bucket='test',storage_object_key='uploads/hash.txt')
    monkeypatch.setattr(report_sources,'ObjectStorage',Storage)
    from app.rag import reranker
    monkeypatch.setattr(reranker,'get_reranker',lambda:SimpleNamespace(rerank=lambda q,docs,**kw:
        [SimpleNamespace(index=i,score=1.) for i in range(min(4,len(docs)))]))
    yield ref, cfg, client, convert, stores
    await client.close()


async def test_index_large_source_reuse_and_owner_isolation(source_env):
    ref, cfg, client, convert, stores = source_env
    field = SimpleNamespace(id='giao_ban',label='Giao ban',definition='Thời gian họp giao ban')
    with use_principal(Principal(94,'407')):
        async with report_sources.ReportSource(ref) as source:
            first = source.doc_id
            assert source.count == 12 and not source.reused
            hits = await source.retrieve('Chế độ giao ban',field)
            assert hits and len(hits)<=4
            assert all(h['field_ids']==['giao_ban'] and h['locator']['source_id']==first for h in hits)
            assert sum(len(h['text']) for h in hits)<20000
        async with report_sources.ReportSource(ref) as source:
            assert source.reused and source.doc_id==first
    assert convert.call_count==1
    with use_principal(Principal(94,'408')):
        async with report_sources.ReportSource(ref) as other:
            assert other.doc_id!=first and not other.reused
            hits = await other.retrieve('Chế độ giao ban',field)
            assert all(h['locator']['source_id']==other.doc_id for h in hits)
    with use_principal(Principal(95,'407')):
        async with report_sources.ReportSource(ref) as other:
            assert other.doc_id!=first
            hits=await other.retrieve('Giao ban',field)
            assert all(h['locator']['source_id']==other.doc_id for h in hits)
    assert not await client.collection_exists('knowledge')
    assert convert.call_count==3


async def test_incomplete_index_is_not_reused(source_env, monkeypatch):
    ref, *_ = source_env
    original = QdrantVectorStore.upsert_chunks
    failed = False
    async def fail_once(store, points, *args, **kwargs):
        nonlocal failed
        if points[0].chunk_index==8 and not failed:
            failed=True
            raise RuntimeError('Nạp dở')
        return await original(store,points,*args,**kwargs)
    monkeypatch.setattr(QdrantVectorStore,'upsert_chunks',fail_once)
    with use_principal(Principal(94,'407')):
        with pytest.raises(RuntimeError,match='Nạp dở'):
            async with report_sources.ReportSource(ref):pass
        async with report_sources.ReportSource(ref) as source:
            assert not source.reused and source.count==12
        async with report_sources.ReportSource(ref) as source:
            assert source.reused


def test_requires_identity_and_separate_collection(source_env):
    ref, cfg, *_ = source_env
    with use_principal(Principal(94,None)):
        with pytest.raises(Exception,match='tenant và tài khoản'):
            report_sources.ReportSource(ref)
    cfg.report_source_collection='knowledge'
    with use_principal(Principal(94,'407')):
        with pytest.raises(Exception,match='tách khỏi kho'):
            report_sources.ReportSource(ref)


async def test_expiry_only_cleans_current_owner(source_env):
    ref, cfg, client, convert, _ = source_env
    with use_principal(Principal(94,'407')):
        async with report_sources.ReportSource(ref) as first:
            scope = first.scope()
    with use_principal(Principal(94,'408')):
        async with report_sources.ReportSource(ref):pass
    await client.set_payload('private_sources',payload={'expires_at':1.},points=scope,wait=True)
    with use_principal(Principal(94,'407')):
        async with report_sources.ReportSource(ref) as first:
            assert not first.reused
    with use_principal(Principal(94,'408')):
        async with report_sources.ReportSource(ref) as other:
            assert other.reused
    assert convert.call_count==3
