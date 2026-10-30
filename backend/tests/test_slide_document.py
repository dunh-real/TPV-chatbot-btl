"""Kiểm tra slide từ tài liệu: nguồn, giới hạn, renderer và lưu/chạy lại UC37."""
import json
from pathlib import Path

import pytest
from docx import Document
from pptx import Presentation
from pydantic import ValidationError

from app.agents.nodes import presentation as renderer, presentation_document as doc
from app.core.config import get_settings
from app.documents import slide_deck as sd
from app.schemas.presentations import PresentationRequest
from app.services import task_queue, tasks
from app.services.presenton import PresentonError
from test_uc37 import environment


BLOCKS = [dict(id="P01", page=None, kind="paragraph", text="Hoàn thành 18/20 nhiệm vụ.")]
OUTLINE = {"title": "Kết quả thực hiện", "slides": [{"title": "Kết quả", "items": [
    {"text": "Hoàn thành 18/20 nhiệm vụ", "source_ids": ["P01"]}]}]}


async def test_shorten_only_overlong_item_preserves_sources(monkeypatch):
    data = json.loads(json.dumps(OUTLINE))
    data['slides'][0]['items'].append({'text':'Nội dung dài. '*30, 'source_ids':['P01']})
    class LLM:
        async def chat_json(self, messages, **kwargs):
            assert set(json.loads(messages[1]['content'])) == {'0:1'}
            return {'items':{'0:1':'Hoàn thành 18/20 nhiệm vụ'}}
    monkeypatch.setattr(doc,'get_llm',LLM)
    await doc.shorten_items(data)
    assert data['slides'][0]['items'][0] == OUTLINE['slides'][0]['items'][0]
    assert data['slides'][0]['items'][1]['source_ids'] == ['P01']
    doc.validate_outline(doc.Outline.model_validate(data), BLOCKS)


async def test_shorten_rejects_response_still_too_long(monkeypatch):
    data = json.loads(json.dumps(OUTLINE)); data['slides'][0]['items'][0]['text']='x'*221
    class LLM:
        async def chat_json(self, *args, **kwargs): return {'items':{'0:0':'x'*221}}
    monkeypatch.setattr(doc,'get_llm',LLM)
    with pytest.raises(ValueError,match='220 ký tự'): await doc.shorten_items(data)


def source_file():
    path = Path(get_settings().upload_dir) / 'source.docx'
    source = Document(); source.add_paragraph(BLOCKS[0]['text']); source.save(path)
    return path


def test_api_requires_file_and_old_requests_work():
    assert PresentationRequest(request='Slide').nguon == 'csdl'
    with pytest.raises(ValidationError):
        PresentationRequest(request='Slide', nguon='tai_lieu')


def test_sources_numbers_and_limit():
    outline = doc.Outline.model_validate(OUTLINE)
    doc.validate_outline(outline, BLOCKS)
    outline.slides[0].items[0].text = 'Hoàn thành 999 nhiệm vụ'
    with pytest.raises(ValueError, match='Số liệu'): doc.validate_outline(outline, BLOCKS)
    outline.slides[0].items[0].source_ids = ['unknown']
    with pytest.raises(ValueError, match='nguồn'): doc.validate_outline(outline, BLOCKS)
    outline.slides *= 15
    with pytest.raises(ValueError, match='slide'): doc.validate_outline(outline, BLOCKS)


def test_citation_id_is_not_a_number_but_unknown_citation_is_checked():
    outline = doc.Outline.model_validate(OUTLINE)
    outline.slides[0].items[0].text += ' [P01]'
    doc.validate_outline(outline, BLOCKS)
    outline.slides[0].items[0].text += ' [P999]'
    with pytest.raises(ValueError,match='999'): doc.validate_outline(outline, BLOCKS)


def test_error_identifies_slide_item_number_and_source():
    outline = doc.Outline.model_validate(OUTLINE)
    outline.slides[0].items[0].text = 'WF37 có 999 nhiệm vụ'
    with pytest.raises(ValueError) as error: doc.validate_outline(outline, BLOCKS)
    assert all(s in str(error.value) for s in ['Slide 2', 'ý 1', '37', '999', 'P01'])


def test_technical_code_must_exist_in_cited_source():
    outline = doc.Outline.model_validate(OUTLINE)
    outline.slides[0].items[0].text = 'UC37 theo dõi tác vụ'
    blocks = BLOCKS + [dict(id='P02',page=None,kind='paragraph',text='UC37 theo dõi tác vụ AI')]
    with pytest.raises(ValueError,match='37'): doc.validate_outline(outline, blocks)
    outline.slides[0].items[0].source_ids = ['P02']
    doc.validate_outline(outline, blocks)


def test_title_numbering_is_not_data_but_quantity_still_checked():
    outline = doc.Outline.model_validate(OUTLINE)
    outline.slides[0].title = '1. Kết quả thực hiện'
    doc.validate_outline(outline, BLOCKS)
    outline.slides[0].title = '1. Kết quả 999 nhiệm vụ'
    with pytest.raises(ValueError,match='999'): doc.validate_outline(outline, BLOCKS)


async def test_read_limits_and_no_silent_cut(environment, monkeypatch):
    source_file()
    assert doc.read_source('upload:source.docx')[0]['text'] == BLOCKS[0]['text']
    monkeypatch.setattr(get_settings(), 'slide_document_max_chars', 1)
    with pytest.raises(ValueError, match='ký tự'): doc.read_source('upload:source.docx')


async def test_prepare_repairs_invalid_sources(environment, monkeypatch):
    source_file()
    calls = []
    class LLM:
        async def chat_json(self, messages, **kwargs):
            calls.append(len(messages))
            data = json.loads(json.dumps(OUTLINE))
            # Parser đặt ID theo tài liệu thật.
            data['slides'][0]['items'][0]['source_ids'] = ['unknown' if len(calls)==1 else doc.read_source('upload:source.docx')[0]['id']]
            return data
    monkeypatch.setattr(doc, 'get_llm', LLM)
    outline, blocks = await doc.prepare('upload:source.docx', 'Tóm lược')
    assert len(calls)==2
    plans = sd.build_document_plan(outline, blocks)
    template = json.loads((Path(__file__).parent/'fixtures/presenton_modern_template.json').read_text())
    # Fixture lưu cấu trúc đã chuẩn hóa bởi Presenton client.
    assert len(plans)==2 and blocks[0]['text'] in plans[1].note
    if isinstance(template['layouts'], list):
        template['layouts'] = {x['id']:x for x in template['layouts']}
    elif 'layouts' in template['layouts']:
        template['layouts'] = {x['id']:x for x in template['layouts']['layouts']}
    for i, plan in enumerate(plans): sd.build_slide(plan, i, 'test', template, {})


async def test_document_queue_minio_fallback_and_history(environment, monkeypatch):
    c,scope,*_ = environment
    original = source_file().read_bytes()
    class LLM:
        async def chat_json(self, messages, **kwargs):
            data = json.loads(json.dumps(OUTLINE))
            blocks = json.loads(messages[1]['content'].split('Tài liệu:\n',1)[1])
            data['slides'][0]['items'][0]['source_ids'] = [blocks[0]['id']]
            return data
    class Unavailable:
        async def get_template(self): raise PresentonError('offline')
    monkeypatch.setattr(doc,'get_llm',LLM)
    monkeypatch.setattr(renderer,'get_presenton',Unavailable)
    response = await c.post('/api/tasks',json={'kind':'Slide','parameters':{
        'request':'Tóm lược tài liệu', 'nguon':'tai_lieu','file_id':'upload:source.docx'}})
    assert response.status_code==202, response.text
    task=response.json()['task_id']
    Path(get_settings().upload_dir,'source.docx').unlink()
    row=await task_queue.claim(); await task_queue.execute(row)
    detail=(await c.get(f'/api/tasks/{task}')).json()
    assert detail['status']=='Completed', detail
    assert detail['result']['engine']=='local' and detail['result']['slide_count']==2
    assert detail['sources'] and detail['versions']
    assert (await c.get(f'/api/tasks/{task}/source')).content==original
    result=await c.get(f'/api/tasks/{task}/result')
    import io
    assert len(Presentation(io.BytesIO(result.content)).slides)==2


async def test_character_limit_before_llm(environment, monkeypatch):
    source_file()
    monkeypatch.setattr(get_settings(), 'slide_document_max_chars', 10)
    with pytest.raises(ValueError,match='10 ký tự'): await doc.prepare('upload:source.docx','Slide')


async def test_pdf_page_source_and_scan_rejected(environment):
    import fitz
    path = Path(get_settings().upload_dir)/'source.pdf'
    pdf = fitz.open(); page = pdf.new_page(); page.insert_text((72,72),'Completed 18 tasks.')
    pdf.save(path); pdf.close()
    blocks = doc.read_source('upload:source.pdf')
    assert blocks[0]['page']==1 and '18' in blocks[0]['text']
    path.unlink()
    pdf = fitz.open(); pdf.new_page(); pdf.save(path); pdf.close()
    with pytest.raises(ValueError,match='OCR'): doc.read_source('upload:source.pdf')


async def test_plan_and_actual_file_cap(environment, monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), 'slide_max_slides', 2)
    outline = doc.Outline.model_validate(OUTLINE).model_dump()
    state = dict(data={'document':BLOCKS},params={'ky':'2026-10'},sources=BLOCKS,document_outline=outline)
    state['document_outline']['slides'] *= 2
    result = await renderer._render_direct(state,tmp_path/'too-many.pptx')
    assert result['error'] and not result['output_path']
    from app.documents.pptx_builder import DeckSpec, SlideSpec, build_pptx
    path = build_pptx(DeckSpec(title='Test',slides=[SlideSpec(kind='title',title='Test')]*3),tmp_path/'bad.pptx')
    result = await renderer.verify_node({'output_path':str(path),'data':{},'params':{}})
    assert result['validation']['status']=='failed' and not result['output_path']


async def test_document_uses_presenton_renderer(environment, monkeypatch, tmp_path):
    from test_workflow5 import FakeDirectPresenton
    presenton = FakeDirectPresenton(tmp_path)
    monkeypatch.setattr(renderer, 'get_presenton', lambda:presenton)
    outline = doc.Outline.model_validate(OUTLINE).model_dump()
    state = dict(data={'document':BLOCKS},params={'ky':'2026-10'},sources=BLOCKS,document_outline=outline)
    result = await renderer._render_direct(state,tmp_path/'document.pptx')
    assert result['engine']=='presenton' and result['deck_check']['ok']
    assert len(presenton.slides)==2
    state.update(result)
    assert (await renderer.verify_node(state))['slide_count']==2
