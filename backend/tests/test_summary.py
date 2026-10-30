"""UC31: API độc lập, độ dài/cấu trúc, bao phủ nguồn và tương thích UC37."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.agents.nodes import summarization as node
from app.agents.quyen import ThieuQuyen
from app.api import documents
from app.core.config import get_settings
from app.core.context import use_principal
from app.documents.parser import Block
from app.services import task_queue
from test_uc37 import environment, seed


class LLM:
    def __init__(self): self.calls=[]
    async def chat_json(self,messages,**kwargs):
        content=json.loads(messages[1]['content'].split('\n',2)[2]); self.calls.append(messages[1]['content'])
        if 'id' in content[0]:
            body=f"Nội dung chính của tài liệu [{content[-1]['id']}]."
        else:
            body=' '.join(s['text'] for s in content)
        return {'sections':[{'heading':'Ý chính','text':body}]}


@pytest.mark.parametrize('length',['short','medium','long'])
@pytest.mark.parametrize('structure',['paragraphs','bullets','sections'])
async def test_lengths_and_structures(tmp_path,monkeypatch,length,structure):
    path=tmp_path/'source.txt'; path.write_text('Tài liệu hướng dẫn sử dụng hệ thống.')
    llm=LLM(); monkeypatch.setattr(node,'get_llm',lambda:llm)
    result=await node.summarize(str(path),path.name,length,structure,'Các ý chính')
    assert result['length']==length and result['structure']==structure and result['coverage']['complete']
    assert result['summary_refs'] and result['sections'] and len(llm.calls)==1
    low,high=node.LENGTHS[length]; assert f'{low}-{high}' in llm.calls[0]
    assert result['summary'].startswith('###' if structure=='sections' else '-' if structure=='bullets' else 'Nội dung')


async def test_long_document_includes_tail_and_original_references(tmp_path,monkeypatch):
    path=tmp_path/'source.txt'; path.write_text(('Thông tin đầu tài liệu. '*600)+'\n\nNhiệm vụ quan trọng ở phần cuối.')
    llm=LLM();monkeypatch.setattr(node,'get_llm',lambda:llm)
    result=await node.summarize(str(path),path.name,'long','sections','')
    assert result['coverage']['batches']>1 and len(llm.calls)==result['coverage']['batches']+1
    assert any('phần cuối' in r['snippet'] for r in result['summary_refs'])
    assert all(r['kind']=='block' and r['locator']['block_id'] for r in result['summary_refs'])


def test_no_silent_truncation_or_empty_document():
    with pytest.raises(ValueError,match='không có nội dung'): node.source_batches([])
    with pytest.raises(ValueError,match='vượt'): node.source_batches([Block('P1','a'*(node.MAX_CHARS+1))])
    batches,total=node.source_batches([Block('P1','a'*(node.BATCH_CHARS+3))])
    assert total==node.BATCH_CHARS+3
    assert ''.join(p['text'] for b in batches for p in b)=='a'*total


@pytest.mark.parametrize('body',['Ý chính [999].','Có 999 người [1].','Ý chính không có nguồn'])
async def test_invalid_citations_and_numbers_rejected(monkeypatch,body):
    class Invalid:
        async def chat_json(self,*args,**kwargs): return {'sections':[{'text':body}]}
    monkeypatch.setattr(node,'get_llm',Invalid)
    with pytest.raises(ValueError,match='hợp lệ'):
        await node.generate([{'id':1,'text':'Có 18 người'}],{1:{'text':'Có 18 người'}},'short','bullets','')


async def test_api_permission_validation_storage_and_queue(environment,monkeypatch):
    client,scope,principal,*_=environment
    from fastapi.responses import JSONResponse
    @client._transport.app.exception_handler(ThieuQuyen)
    async def denied(request,exc): return JSONResponse({'detail':str(exc)},status_code=403)
    client._transport.app.include_router(documents.router)
    path=Path(get_settings().upload_dir)/'summary.txt'; path.write_text('Nội dung cần được tóm tắt.')
    monkeypatch.setattr(node,'get_llm',LLM)
    request={'file_id':'upload:summary.txt','length':'short','structure':'sections','focus':'Ý chính'}
    response=await client.post('/api/documents/summarize',json=request)
    assert response.status_code==200,response.text
    result=response.json(); assert result['ai_task_id'] and result['coverage']['complete']
    detail=(await client.get(f"/api/tasks/{result['ai_task_id']}")).json()
    assert detail['result']['sections'] and detail['result']['length']=='short' and detail['sources']
    only_summary=replace(principal.access,permissions=frozenset({'Ai.Summarize'}))
    with use_principal(replace(principal,access=only_summary)):
        assert (await client.post('/api/documents/summarize',json=request)).status_code==200
    with use_principal(replace(principal,access=replace(principal.access,permissions=frozenset()))):
        assert (await client.post('/api/documents/summarize',json=request)).status_code==403
    assert (await client.post('/api/documents/summarize',json={**request,'length':'huge'})).status_code==422
    assert (await client.post('/api/documents/summarize',json={**request,'file_id':'upload:missing.txt'})).status_code==422
    queued=await client.post('/api/tasks',json={'kind':'Summarize','parameters':request})
    assert queued.status_code==202,queued.text
    path.unlink(); row=await task_queue.claim(); await task_queue.execute(row)
    detail=(await client.get(f"/api/tasks/{queued.json()['task_id']}")).json()
    assert detail['status']=='Completed' and detail['result']['structure']=='sections'
    assert detail['input']['focus']=='Ý chính' and detail['versions']
    # Lần chạy cũ vẫn dùng operation cũ, không ép thêm tham số mới.
    async with scope() as repo:
        old=await seed(repo,status='Failed',kind='Summarize',AgentCode='run_analysis_workflow',
                       InputJson=json.dumps({'file_id':'upload:source.docx','noi_gui':'','rule_set':''}))
    assert (await client.post(f'/api/tasks/{old}/retry')).status_code==202
