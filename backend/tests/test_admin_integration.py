"""Hợp đồng tích hợp mới: auth, trace, lỗi, cấu hình cô lập và metadata fail-closed."""
import asyncio
import json
import uuid
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from qdrant_client import models

from app.api.admin import router
from app.api.integration import IntegrationMiddleware
from app.core import runtime
from app.core.config import Settings, get_settings
from app.core.context import Principal, use_principal
from app.schemas.admin import AgentConfig, AgentTest, ModelTest
from app.services import agent_config, model_registry, vector_metadata
from app.services.integration_errors import IntegrationError
from app.services.llm import LLMClient


@pytest.fixture
def config(tmp_path):
    cfg = Settings(_env_file=None, admin_api_key='test-shared-key', agent_config_dir=str(tmp_path),
                   ai_database_url='', erp_database_url='', metadata_filter_required=True)
    token = runtime.runtime_settings.set(cfg)
    yield cfg
    runtime.runtime_settings.reset(token)


@pytest.fixture
def draft(config):
    return AgentConfig.model_validate({'model': {'code': 'test-model', 'endpointUrl': 'http://localhost:15000/v1'},
        'systemPrompt': 'Nội dung thử', 'parameters': {'temperature': 0.4, 'maxAnswerTokens': 300},
        'allowedPartitionIds': [12]})


@pytest.fixture
def app(config):
    application = FastAPI()
    application.include_router(router)
    application.add_middleware(IntegrationMiddleware)
    @application.get('/bad')
    async def bad():
        raise HTTPException(403, 'Không được phép')
    @application.get('/timeout')
    async def timeout():
        raise TimeoutError()
    @application.get('/business-error')
    async def business():
        return {'error': 'Không sinh được kết quả'}
    @application.get('/boom')
    async def boom():
        raise RuntimeError('secret-internal-detail')
    return application


async def call(app, path, method='GET', **kwargs):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as c:
        return await c.request(method, path, **kwargs)


@pytest.mark.parametrize('key', [None, 'wrong'])
async def test_admin_key_required(app, key):
    r = await call(app, '/api/admin/agents', headers={'X-Api-Key': key} if key else {})
    assert r.status_code == 401 and r.json()['error']['code'] == 'UNAUTHORIZED'
    assert r.json()['error']['requestId'] == r.headers['x-request-id']


async def test_put_config_atomic_and_shared_reload(app, draft):
    rid = str(uuid.uuid4())
    headers = {'X-Api-Key': 'test-shared-key', 'X-Request-Id': rid}
    r = await call(app, '/api/admin/agents/qa/config', 'PUT', headers=headers, json=draft.model_dump())
    assert r.status_code == 200 and r.headers['x-request-id'] == rid
    assert agent_config.load('qa') == draft
    broken = draft.model_dump(); broken['parameters']['maxAnswerTokens'] = 999999
    r = await call(app, '/api/admin/agents/qa/config', 'PUT', headers=headers, json=broken)
    assert r.status_code == 422 and r.json()['error']['code'] == 'CONFIG_INVALID'
    assert agent_config.load('qa') == draft
    r = await call(app, '/api/admin/agents/not-real/config', 'PUT', headers=headers, json=draft.model_dump())
    assert r.status_code == 404


@pytest.mark.parametrize('path,status,code', [('/bad',403,'PARTITION_DENIED'),('/timeout',504,'TIMEOUT'),
    ('/business-error',502,'MODEL_ERROR'),('/boom',500,'INTERNAL_ERROR')])
async def test_error_envelope(app, path, status, code):
    rid = str(uuid.uuid4())
    r = await call(app, path, headers={'X-Request-Id': rid})
    assert r.status_code == status
    assert r.json()['error']['code'] == code and r.json()['error']['requestId'] == rid
    assert 'secret-internal-detail' not in r.text


async def test_invalid_request_id(app):
    r = await call(app, '/bad', headers={'X-Request-Id': 'not-uuid'})
    assert r.status_code == 400 and r.json()['error']['code'] == 'INVALID_REQUEST'
    assert uuid.UUID(r.headers['x-request-id']).version == 4


async def test_config_isolated_between_requests(config, draft):
    async def one():
        async with agent_config.activate(draft):
            await asyncio.sleep(.01)
            assert get_settings().llm_model == 'test-model'
            assert runtime.runtime_llm.get() is not None
    async def other():
        await asyncio.sleep(.005)
        assert get_settings().llm_model == config.llm_model
        assert runtime.agent_config.get() is None
    await asyncio.gather(one(), other())
    assert get_settings() is config and runtime.runtime_llm.get() is None


def test_llm_applies_config_and_context_limit(config, draft):
    token = runtime.agent_config.set(draft)
    client = LLMClient(config, client=SimpleNamespace())
    try:
        p = client._payload([{'role':'user','content':'Thử'}], model='old',temperature=1,max_tokens=2000,stream=False,extra=None,thinking=False)
        assert p['model']=='test-model' and p['temperature']==.4 and p['max_tokens']==300
        assert p['messages'][0]['role'] == 'system'
        assert 'Nội dung thử' in p['messages'][0]['content']
        with pytest.raises(IntegrationError, match='context'):
            client._payload([{'role':'user','content':'a'*40000}], model=None,temperature=None,max_tokens=None,stream=False,extra=None,thinking=False)
    finally:
        runtime.agent_config.reset(token)


def test_filter_exact_ids_and_intersection(config, draft):
    token = runtime.agent_config.set(draft)
    ft = runtime.metadata_filters.set({'partition_ids': [12, 13], 'security_level_ids': [5, 9]})
    try:
        with use_principal(Principal(94, '407')):
            f = vector_metadata.restrict(None)
        conditions = [v for v in f.must if isinstance(v, models.FieldCondition)]
        assert any(v.key=='tenant_id' and v.match.value==94 for v in conditions)
        assert [v.match.any for v in conditions if v.key=='partition_id']==[[12,13],[12]]
        assert next(v for v in conditions if v.key=='security_level_id').match.any == [5,9]
        assert all(v.range is None for v in conditions)
    finally:
        runtime.metadata_filters.reset(ft); runtime.agent_config.reset(token)


def test_empty_scope_does_not_remove_filter(config):
    token = runtime.metadata_filters.set({'partition_ids': []})
    try:
        with use_principal(Principal(94,'407')):
            f = vector_metadata.restrict(None)
        assert f.must[-1].match.any == []
        with use_principal(Principal(None,'407')), pytest.raises(IntegrationError):
            vector_metadata.restrict(None)
    finally:
        runtime.metadata_filters.reset(token)


async def test_model_unknown_local_and_disallowed_endpoint(config):
    with pytest.raises(IntegrationError):
        await model_registry.test_model(ModelTest.model_validate({'model': {'code':'unknown','type':'embedding'}}))
    with pytest.raises(IntegrationError):
        await model_registry.test_model(ModelTest.model_validate({'model': {'code':'x','endpointUrl':'http://example.org/v1'}}))


async def test_business_token_separate_from_admin(app, config):
    @app.get('/api/probe')
    async def probe():
        return {'ok': True}
    config.public_access_token = 'business-key'
    r = await call(app, '/api/probe', headers={'X-Api-Key': 'test-shared-key'})
    assert r.status_code == 401
    r = await call(app, '/api/probe', headers={'X-Access-Token': 'business-key'})
    assert r.status_code == 200
    r = await call(app, '/api/admin/agents', headers={'X-Access-Token': 'business-key'})
    assert r.status_code == 401


async def test_sse_error_has_request_id(app):
    from starlette.responses import StreamingResponse
    @app.get('/stream')
    async def stream():
        async def events():
            yield b'event: delta\ndata: {"text":"test"}\n\n'
            raise IntegrationError('NO_CONTEXT','Không có nguồn',422)
        return StreamingResponse(events(), media_type='text/event-stream')
    rid = str(uuid.uuid4())
    r = await call(app, '/stream', headers={'X-Request-Id': rid})
    assert r.status_code == 200 and 'event: error' in r.text
    assert 'NO_CONTEXT' in r.text and rid in r.text


@pytest.mark.parametrize('status,code', [(429,'OVERLOADED'), (503,'OVERLOADED'), (500,'MODEL_ERROR')])
async def test_remote_model_error_classification(app, monkeypatch, status, code):
    async def failed(request):
        req = httpx.Request('POST','http://localhost/v1/chat/completions')
        raise httpx.HTTPStatusError('model failed',request=req,response=httpx.Response(status,request=req))
    monkeypatch.setattr(model_registry,'test_model',failed)
    r = await call(app,'/api/admin/models/test','POST',headers={'X-Api-Key':'test-shared-key'},
        json={'model':{'code':'x','endpointUrl':'http://localhost/v1'}})
    assert r.status_code in (502,503) and r.json()['error']['code']==code


async def test_model_unreachable(app, monkeypatch):
    async def failed(request):
        raise httpx.ConnectError('connection refused')
    monkeypatch.setattr(model_registry,'test_model',failed)
    r=await call(app,'/api/admin/models/test','POST',headers={'X-Api-Key':'test-shared-key'},json={'model':{'code':'x'}})
    assert r.status_code==502 and r.json()['error']['code']=='MODEL_UNREACHABLE'


async def test_timeout_for_model_is_total_budget(config, monkeypatch):
    monkeypatch.setattr(model_registry,'MODEL_TEST_SECONDS',.01)
    class Slow:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,*args,**kwargs): await asyncio.sleep(1)
    monkeypatch.setattr(model_registry.httpx,'AsyncClient',Slow)
    with pytest.raises(TimeoutError):
        await model_registry.test_model(ModelTest.model_validate({'model':{'code':'x','endpointUrl':'http://localhost/v1'}}))


async def test_llm_wrapped_network_error_keeps_code():
    from app.services.integration_errors import classify
    from app.services.llm import LLMError
    try:
        try: raise httpx.ConnectError('internal host')
        except httpx.ConnectError as cause: raise LLMError('wrapper') from cause
    except LLMError as exc:
        error=classify(exc)
    assert error.code=='MODEL_UNREACHABLE'


async def test_metadata_integer_ids_from_erp():
    row={'Id':429,'TenantId':94,'PartitionId':123,'AccessLevelCategoryId':5,'SecurityLevelCategoryId':9}
    result=await vector_metadata.from_document(None,row)
    assert result=={'document_id':429,'tenant_id':94,'partition_id':123,'access_level_id':5,'security_level_id':9}
    with pytest.raises(IntegrationError):
        await vector_metadata.from_document(None,{**row,'PartitionId':None})


async def test_agent_draft_config_does_not_write_db_or_history(app, config, draft, monkeypatch, tmp_path):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from app.api import identity
    from app.agents.nodes import summarization
    from app.db import ai_session
    from app.services.conversation import ConversationMemory
    request = AgentTest(
        config=draft,request='Tóm tắt',file_id='upload:source.txt')
    config.upload_dir=str(tmp_path/'uploads')
    Path(config.upload_dir).mkdir(); (Path(config.upload_dir)/'source.txt').write_text('Tài liệu')
    access=SimpleNamespace(tenant_id=94,user_id=407,has=lambda permission:True)
    monkeypatch.setattr(identity,'_kem_quyen',AsyncMock(return_value=Principal(94,'407',access=access)))
    @asynccontextmanager
    async def forbidden_db():
        raise AssertionError('Lượt test không được ghi nghiệp vụ')
        yield
    monkeypatch.setattr(ai_session,'repository',forbidden_db)
    async def fake_summary(*args,**kwargs):
        assert runtime.test_mode.get()
        memory=ConversationMemory(cache=SimpleNamespace(),store=SimpleNamespace())
        await memory.append_turns('test',[('user','test')])
        assert await memory.get_history('test')==[]
        return {'summary':'Nội dung','error':'','word_count':2}
    monkeypatch.setattr(summarization,'summarize',fake_summary)
    r=await call(app,'/api/admin/agents/summarize/test','POST',json=request.model_dump(),
        headers={'X-Api-Key':'test-shared-key','X-User-Id':'407','X-Tenant-Id':'94'})
    assert r.status_code==200,r.text
    assert r.json()['test'] and r.json()['result']['summary']=='Nội dung'
    assert agent_config.load('summarize') is None and not runtime.test_mode.get()


@pytest.mark.parametrize('available',[True,False])
async def test_health_includes_local_components(config,monkeypatch,available):
    from unittest.mock import AsyncMock
    from app import main
    from app.db import ai_session
    from app.rag import embedding,reranker
    store=SimpleNamespace(count=AsyncMock(return_value=0),collection='test')
    monkeypatch.setattr(main,'get_vector_store',lambda:store)
    monkeypatch.setattr(main,'get_memory',lambda:SimpleNamespace(store=store))
    monkeypatch.setattr(main,'get_llm',lambda:SimpleNamespace(health=AsyncMock(return_value=True)))
    monkeypatch.setattr(ai_session,'healthcheck',AsyncMock(return_value=True))
    monkeypatch.setattr(embedding,'_embedder',object() if available else None)
    monkeypatch.setattr(reranker,'_reranker',object())
    result=await main.health()
    assert result.embedding==available and result.reranker and result.qdrant and result.llm
    assert result.status==('ok' if available else 'degraded')


@pytest.mark.parametrize('kind',['embedding','reranker'])
async def test_local_model_resolved_by_code_without_endpoint(config,monkeypatch,kind):
    if kind=='embedding':
        from app.rag import embedding
        monkeypatch.setattr(embedding,'get_embedder',lambda:SimpleNamespace(encode=lambda values:([[1.,2.]],[])))
        code=config.embedding_model
    else:
        from app.rag import reranker
        monkeypatch.setattr(reranker,'get_reranker',lambda:SimpleNamespace(score=lambda q,docs:[.8]*len(docs)))
        code=config.reranker_model
    result=await model_registry.test_model(ModelTest.model_validate({'code':code,'endpointUrl':None}))
    assert result['ok'] and result['type']==kind


async def test_agent_timeout_resets_context(config,draft,monkeypatch):
    from app.agents import graph
    from app.services import agent_test
    from unittest.mock import AsyncMock
    monkeypatch.setattr(agent_test,'AGENT_TEST_SECONDS',.01)
    monkeypatch.setattr(graph,'build_initial_state',AsyncMock(return_value={}))
    async def slow(*args): await asyncio.sleep(1)
    monkeypatch.setattr(graph,'run_qa',slow)
    access=SimpleNamespace(tenant_id=94,user_id=407,has=lambda p:True)
    with use_principal(Principal(94,'407',access=access)),pytest.raises(TimeoutError):
        await agent_test.run('qa',AgentTest(config=draft,request='test'))
    assert not runtime.test_mode.get() and runtime.agent_config.get() is None
