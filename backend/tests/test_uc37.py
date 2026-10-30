"""UC37: kiểm tra API, cách ly tài khoản, hàng đợi và khôi phục trên DB thử."""
import io
import json
from contextlib import asynccontextmanager
from dataclasses import replace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, event
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import tasks as api
from app.core.config import get_settings
from app.core.context import Principal, use_principal
from app.db import ai_session
from app.db.ai_repository import AIRepository, dumps, now
from app.db.erp_auth import AccountAccess
from app.services import tasks, task_queue, ai_outputs, access


@pytest.fixture
async def environment(monkeypatch, tmp_path):
    md = MetaData()
    def audit():
        return [Column('Id', Integer, primary_key=True), Column('TenantId', Integer), Column('CreatorUserId', Integer),
            Column('CreationTime', DateTime), Column('IsDeleted', Boolean), Column('LastModificationTime', DateTime),
            Column('LastModifierUserId', Integer), Column('DeletionTime', DateTime), Column('DeleterUserId', Integer)]
    defs = {
        'Tasks': dict(UserId=Integer, TaskType=String, AgentCode=String, Status=String, ProgressPercentage=Integer,
            InputJson=Text, ResultJson=Text, OutputFileUrl=Text, ErrorMessage=Text, RetryOfTaskId=Integer,
            NotifyOnFinish=Boolean, StartTime=DateTime, EndTime=DateTime, TextSearch=String,
            RelatedEntityType=String, RelatedEntityId=Integer, TemplateId=Integer),
        'OutputVersions': dict(OwnerType=String, OwnerId=Integer, VersionNo=Integer, ContentJson=Text, FileUrl=Text, ChangeNote=Text),
        'SourceReferences': dict(OwnerType=String, OwnerId=Integer, DocumentId=Integer, ExternalSourceRef=Text,
            DocumentVersionNo=Integer, PageNumber=Integer, Snippet=Text),
        'KnowledgeDocuments': dict(AiServerDocId=String, DocumentNumber=String, DocCode=String, IndexStatus=String, Title=String, VersionNo=Integer),
        'ReportTemplate': dict(Code=String, Name=String),
        'Slides': dict(Title=String, Status=String, OutlineJson=Text, FileUrl=Text, VersionNo=Integer),
        'TemplateVersions': dict(TemplateId=Integer, VersionNo=Integer, FileUrl=Text, FileName=String),
        'ExecutionLogs': dict(UserId=Integer, TaskId=Integer, EventType=String, AgentCode=String, Status=String,
            HasCitations=Boolean, NoEvidence=Boolean, ErrorMessage=Text, DurationMs=Integer, OutputFileUrl=Text, UserQuery=Text),
        'DocumentReviews': dict(UserId=Integer, FileName=String, FileUrl=Text, ResultJson=Text, IssuesJson=Text,
            IssueCount=Integer, AcceptedCount=Integer, IgnoredCount=Integer)}
    mapped = {'AI_'+name: Table('AI_'+name, md, *audit(), *[Column(k, typ) for k,typ in cols.items()]) for name,cols in defs.items()}
    engine = create_async_engine('sqlite+aiosqlite:///:memory:')
    def jvalue(raw, path):
        try:
            v = json.loads(raw)
            for k in path.removeprefix('$.').split('.'): v = v[k]
            return 'true' if v is True else 'false' if v is False else v if not isinstance(v,(dict,list)) else None
        except (ValueError, TypeError, KeyError): return None
    def modify(raw, path, value):
        data=json.loads(raw); keys=path.removeprefix('$.').split('.'); parent=data
        for key in keys[:-1]: parent=parent.setdefault(key,{})
        parent[keys[-1]]=value
        return json.dumps(data)
    def valid(raw):
        try: json.loads(raw); return 1
        except (ValueError, TypeError): return 0
    @event.listens_for(engine.sync_engine,'connect')
    def functions(conn,_):
        conn.create_function('json_value',2,jvalue); conn.create_function('isjson',1,valid); conn.create_function('json_modify',3,modify)
    async with engine.begin() as conn: await conn.run_sync(md.create_all)
    perms=frozenset({'Ai.AiTask','Ai.AiTask.Delete','Ai.DocumentReview','Ai.Summarize','Ai.TaskExtract',
        'Ai.ReportDrafting','Ai.ReportSummary.Create','Ai.Slide.Create','Ai.AiChatbot'})
    actor=AccountAccess(10,94,'test','Test','',permissions=perms); principal=Principal(94,'10',access=actor)
    @asynccontextmanager
    async def scope():
        async with engine.begin() as conn: yield AIRepository(conn,mapped)
    async def tables(): return mapped
    async def resolve(identifier): return actor if identifier=='10' else None
    for module in (ai_session,api,ai_outputs): monkeypatch.setattr(module,'repository',scope)
    monkeypatch.setattr(ai_session,'tables',tables); monkeypatch.setattr(ai_session,'get_engine',lambda:engine)
    monkeypatch.setattr(access,'resolve_access',resolve)
    monkeypatch.setattr(get_settings(),'upload_dir',str(tmp_path/'uploads'))
    monkeypatch.setattr(get_settings(),'output_dir',str(tmp_path/'output'))
    (tmp_path/'uploads').mkdir(); (tmp_path/'uploads'/'source.docx').write_bytes(b'source')
    objects={}
    class Stream(io.BytesIO):
        def release_conn(self): pass
    class Store:
        def upload(self,path,name):
            key='files/'+path.name; objects[key]=path.read_bytes()
            return {'storage_bucket':'test','storage_object_key':key}
        def download(self,bucket,key,path,name): path.write_bytes(objects[key])
        def open(self,bucket,key): return Stream(objects[key])
    for module in (task_queue,ai_outputs,api): monkeypatch.setattr(module,'ObjectStorage',Store)
    app=FastAPI(); app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://test') as client:
        with use_principal(principal): yield client,scope,principal,mapped,engine,objects
    await engine.dispose()


async def seed(repo,status='Completed',kind='Review',**values):
    defaults=dict(UserId=repo.user_id,TaskType=kind,AgentCode='run_document_workflow',Status=status,
        ProgressPercentage=100 if status=='Completed' else 0,StartTime=now(),EndTime=now() if status in tasks.TERMINAL else None,
        InputJson=dumps({'_uc37':{'managed':True}}),ResultJson='{"summary":"Kết quả"}',NotifyOnFinish=True)
    return await repo.add('Tasks',**{**defaults,**values})


async def test_historical_template_and_source_download(environment):
    c,scope,p,_,_,objects=environment
    objects['template.docx']=b'template-v2'; objects['source.docx']=b'original'
    async with scope() as repo:
        template=await repo.add('ReportTemplate',Code='BC',Name='Mẫu báo cáo')
        await repo.add('TemplateVersions',TemplateId=template,VersionNo=2,
            FileUrl='minio://test/template.docx',FileName='mau.docx')
        task=await seed(repo,TemplateId=template,RelatedEntityType='AI_Tasks',RelatedEntityId=1,
            ResultJson=dumps({'template_id':template,'template_version':2}),
            InputJson=dumps({'source_url':'minio://test/source.docx','source_name':'nguon.docx'}))
    assert (await c.get(f'/api/tasks/{task}/template')).content==b'template-v2'
    assert (await c.get(f'/api/tasks/{task}/source')).content==b'original'
    with use_principal(replace(p,user_id='20',access=replace(p.access,user_id=20))):
        assert (await c.get(f'/api/tasks/{task}/template')).status_code==404
        assert (await c.get(f'/api/tasks/{task}/source')).status_code==404
    await c.delete(f'/api/tasks/{task}/output')
    assert (await c.get(f'/api/tasks/{task}/template')).status_code==404


async def test_list_filters_pagination_detail(environment):
    c,scope,*_=environment
    async with scope() as repo:
        task=await seed(repo); await seed(repo,status='Failed')
    r=await c.get('/api/tasks?page_size=1'); assert r.status_code==200 and r.json()['total']==2
    assert (await c.get('/api/tasks?status=Failed')).json()['items'][0]['status']=='Failed'
    assert (await c.get(f'/api/tasks/{task}')).json()['result']['summary']=='Kết quả'
    assert (await c.get('/api/tasks?status=invalid')).status_code==422
    assert (await c.get('/api/tasks?since=2026-12-01&until=2026-01-01')).status_code==422


async def test_owner_tenant_view_all_permissions(environment):
    c,scope,p,*_=environment
    async with scope() as repo: task=await seed(repo)
    other=replace(p.access,user_id=20)
    with use_principal(replace(p,user_id='20',access=other)):
        assert (await c.get(f'/api/tasks/{task}')).status_code==404
        assert (await c.get('/api/tasks')).json()['total']==0
    viewer=replace(other,permissions=other.permissions|{'Ai.AiTask.ViewAll'})
    with use_principal(replace(p,user_id='20',access=viewer)):
        assert (await c.get(f'/api/tasks/{task}')).status_code==200
        assert (await c.delete(f'/api/tasks/{task}/output')).status_code==403
    with use_principal(replace(p,tenant_id=95,access=replace(viewer,tenant_id=95))):
        assert (await c.get(f'/api/tasks/{task}')).status_code==404
    with use_principal(replace(p,access=replace(p.access,permissions=frozenset()))):
        assert (await c.get('/api/tasks')).status_code==403
        assert (await c.post('/api/tasks',json={'kind':'Review','parameters':{'file_id':'upload:source.docx'}})).status_code==403


async def test_retry_history_idempotence_and_revocation(environment):
    c,scope,p,*_=environment
    async with scope() as repo:
        task=await seed(repo,status='Failed',kind='Slide',AgentCode='run_presentation_workflow',InputJson='{"request":"Thử"}')
    r=await c.post(f'/api/tasks/{task}/retry'); assert r.status_code==202
    new=r.json()['task_id']; assert new!=task
    assert (await c.post(f'/api/tasks/{task}/retry')).json()['task_id']==new
    assert (await c.get(f'/api/tasks/{new}')).json()['retry_of_task_id']==task
    assert (await c.post(f'/api/tasks/{new}/retry')).status_code==409
    with use_principal(replace(p,access=replace(p.access,permissions=frozenset({'Ai.AiTask'})))):
        assert (await c.post(f'/api/tasks/{task}/retry')).status_code==403


async def test_notifications_opt_in_read_private(environment):
    c,scope,*_=environment
    async with scope() as repo:
        task=await seed(repo,status='Failed'); await seed(repo,status='Processing')
    rows=(await c.get('/api/tasks/notifications')).json()['items']; assert len(rows)==1 and not rows[0]['is_read']
    assert (await c.patch(f'/api/tasks/notifications/{task}/read')).status_code==204
    assert (await c.get('/api/tasks/notifications')).json()['items'][0]['is_read']
    assert (await c.patch(f'/api/tasks/{task}/notify',json={'enabled':False})).status_code==200
    assert not (await c.get('/api/tasks/notifications')).json()['items']


async def test_delete_output_tombstone_versions_audit(environment):
    c,scope,_,_,_,objects=environment; objects['output.docx']=b'output'
    async with scope() as repo:
        product=await repo.add('DocumentReviews',UserId=repo.user_id,FileName='source.docx')
        task=await seed(repo,RelatedEntityType='AI_DocumentReviews',RelatedEntityId=product,OutputFileUrl='minio://test/output.docx',TextSearch='output.docx')
        version=await repo.add('OutputVersions',OwnerType='Review',OwnerId=product,VersionNo=1,FileUrl='minio://test/output.docx',ContentJson='{}')
    assert (await c.get(f'/api/tasks/{task}/result?version_id={version}')).content==b'output'
    assert (await c.delete(f'/api/tasks/{task}/output')).status_code==204
    assert (await c.get(f'/api/tasks/{task}/result')).status_code==404
    assert (await c.get(f'/api/tasks/{task}')).json()['output_deleted']
    async with scope() as repo:
        row=await tasks.get_task(repo,task); assert row['TextSearch']=='output.docx' and row['ResultJson'] is None
        assert not await repo.rows('OutputVersions')
        assert any(r['EventType']=='OutputDeleted' for r in await repo.rows('ExecutionLogs'))


async def test_foreign_version_is_not_downloadable(environment):
    c,scope,*_=environment
    async with scope() as repo:
        task=await seed(repo,RelatedEntityType='AI_DocumentReviews',RelatedEntityId=100)
        version=await repo.add('OutputVersions',OwnerType='Review',OwnerId=200,VersionNo=1,ContentJson='{}')
    assert (await c.get(f'/api/tasks/{task}/result?version_id={version}')).status_code==404


async def test_queue_claim_summary_and_source_restore(environment,monkeypatch):
    c,scope,*_=environment
    @ai_outputs.tracked_workflow('Review')
    async def review(file_path,file_name='',noi_gui='',rule_set='',**options):
        from pathlib import Path
        assert Path(file_path).read_bytes()==b'source'
        return {'summary':'Tóm tắt','tasks':[{'task':'Làm việc'}],'all_refs':[]}
    monkeypatch.setattr(task_queue,'runner',lambda _:review)
    r=await c.post('/api/tasks',json={'kind':'Summarize','parameters':{'file_id':'upload:source.docx'},'notify_on_finish':True})
    assert r.status_code==202,r.text
    task=r.json()['task_id']; job=await task_queue.claim(); assert job['Id']==task and await task_queue.claim() is None
    await task_queue.execute(job)
    detail=(await c.get(f'/api/tasks/{task}')).json()
    assert detail['status']=='Completed' and detail['kind']=='Summarize' and detail['result']['summary']=='Tóm tắt'
    assert detail['versions'] and detail['sources'][0]['download_url']==f'/api/tasks/{task}/source'
    assert (await c.get(f'/api/tasks/{task}/source')).content==b'source'
    assert (await c.get(f'/api/tasks/{task}/result')).json()['summary']=='Tóm tắt'
    assert len((await c.get('/api/tasks/notifications')).json()['items'])==1


async def test_recover_fences_old_writer(environment):
    c,scope,*_=environment
    async with scope() as repo:
        task=await seed(repo,status='Processing',InputJson=dumps({'_uc37':{'managed':True,'origin':'inline','lease_token':'old','lease_until':'2000-01-01T00:00:00'}}))
    await task_queue.recover()
    assert (await c.get(f'/api/tasks/{task}')).json()['status']=='Failed'
    async with scope() as repo:
        with pytest.raises(task_queue.LeaseLost): await task_queue.fence(repo,task,'old')


async def test_running_delete_and_arbitrary_server_path_rejected(environment):
    c,scope,*_=environment
    async with scope() as repo: task=await seed(repo,status='Processing')
    assert (await c.delete(f'/api/tasks/{task}/output')).status_code==409
    r=await c.post('/api/tasks',json={'kind':'Review','parameters':{'file_path':'/etc/passwd'}})
    assert r.status_code==422


async def test_inline_failure_keeps_portable_source_for_retry(environment,monkeypatch):
    c,scope,*_=environment
    class BrokenStore:
        def upload(self,*args): raise RuntimeError("MinIO temporarily unavailable")
    monkeypatch.setattr(ai_outputs,'ObjectStorage',BrokenStore)
    @ai_outputs.tracked_workflow('Review')
    async def review(file_path,file_name=''):
        return {'summary':'unused'}
    review.__name__ = 'run_document_workflow'
    original_name = review.__wrapped__.__name__
    review.__wrapped__.__name__ = 'run_document_workflow'
    from pathlib import Path
    source = Path(get_settings().upload_dir)/'source.docx'
    with pytest.raises(RuntimeError): await review(str(source),'source.docx')
    row=(await c.get('/api/tasks')).json()['items'][0]
    async with scope() as repo:
        stored=await tasks.get_task(repo,row['id'])
        assert tasks.payload(stored)['file_id']=='upload:source.docx'
    response=await c.post('/api/tasks/'+str(row['id'])+'/retry')
    assert response.status_code==202,response.text
    source.unlink()
    async with scope() as repo:
        retry=await tasks.get_task(repo,response.json()['task_id'])
        assert tasks.payload(retry)['source_url'].startswith('minio:')


async def test_heartbeat_updates_only_lease(environment,monkeypatch):
    import asyncio
    c,scope,*_=environment
    monkeypatch.setattr(get_settings(),'task_lease_seconds',3)
    async with scope() as repo:
        task=await seed(repo,status='Processing',InputJson=dumps({'_uc37':{'managed':True,'origin':'inline',
            'lease_token':'live','lease_until':'2000-01-01T00:00:00','keep':'value'}}))
        row=await tasks.get_task(repo,task)
    async with task_queue.lease(row): await asyncio.sleep(1.1)
    async with scope() as repo:
        info=tasks.metadata(await tasks.get_task(repo,task))
        assert info['lease_until']>'2000-01-01T00:00:00' and info['keep']=='value'


async def test_erp_children_read_without_changing_product_link(environment):
    c,scope,p,_,_,objects=environment
    objects['erp-template.docx']=b'erp-template-v3'
    objects['erp-source.docx']=b'erp-source'
    async with scope() as repo:
        template=await repo.add('ReportTemplate',Code='ERP_BC',Name='Mẫu ERP')
        await repo.add('TemplateVersions',TemplateId=template,VersionNo=3,
            FileUrl='minio://test/erp-template.docx',FileName='template.docx')
        task=await seed(repo,RelatedEntityType='AI_ReportSummaries',RelatedEntityId=777,
            InputJson='{}',ResultJson='{}',TemplateId=None)
        version=await repo.add('OutputVersions',OwnerType='Task',OwnerId=task,VersionNo=1,
            ContentJson=dumps({'summary':'AI response','template_id':template,'template_version':3}))
        await repo.add('SourceReferences',OwnerType='Task',OwnerId=task,
            ExternalSourceRef='minio://test/erp-source.docx')
    detail=(await c.get(f'/api/tasks/{task}')).json()
    assert len(detail['versions'])==1 and detail['template']['version']==3
    assert (await c.get(f'/api/tasks/{task}/result?version_id={version}')).json()['summary']=='AI response'
    assert (await c.get(f'/api/tasks/{task}/source')).content==b'erp-source'
    assert (await c.get(f'/api/tasks/{task}/template')).content==b'erp-template-v3'
