"""Mẫu được duyệt một lần; lần soạn chỉ dùng mapping, số và nguồn đã đối chiếu."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from docx import Document
from docx.shared import Pt

from app.documents import template_binding as binding
from app.schemas.templates import TemplateMapping
from app.services import report_fields, template_analyzer
from app.services.integration_errors import IntegrationError


@pytest.fixture
def sample(tmp_path):
    path = tmp_path / "mau.docx"
    doc = Document()
    doc.add_paragraph("BÁO CÁO NHÂN SỰ")
    p = doc.add_paragraph()
    p.add_run("Người ký: ").bold = True
    p.add_run("...").font.size = Pt(14)
    doc.add_paragraph("...")
    doc.add_paragraph("...")
    cell = doc.add_table(rows=1, cols=1).cell(0, 0)
    cell.text = "..."
    doc.sections[0].header.paragraphs[0].text = "LOGO / TÊN ĐƠN VỊ"
    doc.save(path)
    document, sha = binding.read_template(path)
    targets = [r["target"] for r in binding.regions(document) if r["writable"]]
    mapping = TemplateMapping(file_sha256=sha, fields=[
        dict(id="nguoi_ky", label="Người ký", kind="input", target=targets[0]),
        dict(id="tong", label="Nhân sự", kind="value", data_type="integer", definition="Nhân sự cuối kỳ", target=targets[1]),
        dict(id="nhan_xet", label="Nhận xét", kind="narrative", definition="Nhận xét cơ cấu", target=targets[2]),
        dict(id="moi", label="Tuyển mới", kind="value", data_type="integer", definition="Tuyển mới trong kỳ", target=targets[3]),
    ])
    return path, mapping


def test_render_keeps_static_text_styles_and_cells(sample, tmp_path):
    path, mapping = sample
    original = path.read_bytes()
    results = [dict(id=f.id, value=value, status="verified") for f, value in zip(
        mapping.fields, ["Nguyễn A", "13", "Có 13 nhân sự.", "0"])]
    output = binding.render(path, mapping, results, tmp_path / "draft.docx")
    doc = Document(output)
    assert doc.paragraphs[0].text == "BÁO CÁO NHÂN SỰ"
    assert doc.paragraphs[1].text == "Người ký: Nguyễn A"
    assert doc.paragraphs[1].runs[0].bold
    assert doc.paragraphs[1].runs[1].font.size == Pt(14)
    assert doc.tables[0].cell(0, 0).text == "0"
    assert doc.sections[0].header.paragraphs[0].text == "LOGO / TÊN ĐƠN VỊ"
    assert path.read_bytes() == original


def test_hash_and_fixed_region_rejected(sample):
    path, mapping = sample
    bad = mapping.model_copy(deep=True)
    bad.file_sha256 = "0" * 64
    with pytest.raises(IntegrationError, match="không khớp"):
        binding.validate(path, bad)
    doc, _ = binding.read_template(path)
    bad = mapping.model_copy(deep=True)
    bad.fields[0].target = binding.regions(doc)[0]["target"]
    with pytest.raises(IntegrationError, match="cố định"):
        binding.validate(path, bad)


def test_source_verification_zero_and_fabricated_quote(sample):
    _, mapping = sample
    sources = [dict(id="cell", kind="du_lieu", text='{"Nhân sự":13,"Tuyển mới":0}',
        value=0, locator={"query_id": "q1", "row": 0, "column": "TuyenMoi"})]
    raw = [dict(id="moi", value=999, evidence=[dict(source_id="cell", quote='"Tuyển mới":0')]),
        dict(id="tong", value=13, evidence=[dict(source_id="cell", quote="Bịa nguồn 13")]),
        dict(id="nhan_xet", value="Có 17 người", evidence=[dict(source_id="cell", quote='"Nhân sự":13')])]
    results = {r["id"]: r for r in report_fields.resolve_fields(mapping, raw, sources, {})}
    assert results["moi"]["value"] == "0"  # Dùng ô nguồn, bỏ giá trị LLM tự chép.
    assert results["moi"]["refs"][0]["locator"]["query_id"] == "q1"
    assert results["tong"]["status"] == results["nhan_xet"]["status"] == "needs_review"
    assert results["nguoi_ky"]["status"] == "missing"


def test_calculation_and_zero_divisor(sample):
    _, mapping = sample
    mapping.fields[2] = mapping.fields[2].model_copy(update=dict(kind="calculation",
        operation="ratio_percent", operands=["tong", "moi"], data_type="decimal"))
    sources = [dict(id=str(v), kind="du_lieu", text=str(v), value=v, locator={}) for v in (13, 0)]
    raw = [dict(id=k, evidence=[dict(source_id=str(v), quote=str(v))]) for k, v in (("tong", 13), ("moi", 0))]
    results = report_fields.resolve_fields(mapping, raw, sources, {})
    assert results[2]["value"] is None and results[2]["status"] == "needs_review"
    mapping.fields[2].operation = "sum"
    assert report_fields.resolve_fields(mapping, raw, sources, {})[2]["value"] == "13"


async def test_confirm_no_llm_and_draft_reuses_mapping(sample, tmp_path, monkeypatch):
    path, mapping = sample
    monkeypatch.setattr(template_analyzer, "template_path", lambda _: path)
    monkeypatch.setattr(template_analyzer, "get_llm", lambda: pytest.fail("Không phân tích lại khi xác nhận"))
    confirmed = await template_analyzer.confirm("upload:mau.docx", mapping)
    assert confirmed["structure_json"]["binding"]["file_sha256"] == mapping.file_sha256
    template = SimpleNamespace(fields=confirmed["structure_json"], file_path=path,
        ai_id=12, ai_version=2, ma_template="BC", ten_bao_cao="Báo cáo")
    loader = AsyncMock(return_value=template)
    monkeypatch.setattr(report_fields, "get_version", loader)
    sources = [dict(id="p1", kind="block", text="Có 13 nhân sự, tuyển mới 0 người.", locator={"file_id": "f"})]
    monkeypatch.setattr(report_fields, "gather", AsyncMock(return_value=(sources, [])))
    writer = AsyncMock(return_value={"fields": [dict(id=k, value=v,
        evidence=[dict(source_id="p1", quote=sources[0]["text"])])
        for k, v in (("tong", "13"), ("moi", "0"), ("nhan_xet", "Có 13 nhân sự."))]})
    monkeypatch.setattr(report_fields, "get_llm", lambda: SimpleNamespace(chat_json=writer))
    monkeypatch.setattr(report_fields, "get_settings", lambda: SimpleNamespace(output_dir=tmp_path))
    result = await report_fields.draft_from_mapping("Báo cáo tháng 8", 12, 2,
        {"nguoi_ky": "Nguyễn A"}, "tai_lieu", "f", [], None, [])
    assert result["validation"]["status"] == "passed"
    assert Document(result["output_path"]).paragraphs[2].text == "13"
    loader.assert_awaited_once_with(12, 2)
    assert writer.await_count == 3  # Soạn riêng từng vùng; không gọi analyzer.


async def test_required_missing_returns_preview_without_export(sample, tmp_path, monkeypatch):
    path, mapping = sample
    template = SimpleNamespace(fields={"binding": mapping.model_dump()}, file_path=path,
        ai_id=12, ai_version=2, ma_template="BC", ten_bao_cao="Báo cáo")
    monkeypatch.setattr(report_fields, "get_version", AsyncMock(return_value=template))
    monkeypatch.setattr(report_fields, "gather", AsyncMock(return_value=([], [])))
    result = await report_fields.draft_from_mapping("Báo cáo", 12, 2, {}, "tai_lieu", "f", [], None, [])
    assert result["missing_input"] and not result["output_path"]
    assert result["field_results"] and result["validation"]["status"] == "warning"


async def test_explicit_version_uses_scoped_repository(monkeypatch,sample):
    import json
    from app.services import ai_templates
    repo = SimpleNamespace(templates=AsyncMock(return_value=[]), template_version=AsyncMock())
    @asynccontextmanager
    async def scoped():
        yield repo
    monkeypatch.setattr(ai_templates, "repository", scoped)
    with pytest.raises(IntegrationError, match="phạm vi quyền"):
        await ai_templates.get_version(12, 2)
    repo.template_version.assert_not_awaited()
    repo.templates.return_value = [dict(Id=12, Code="BC", Name="Báo cáo")]
    repo.template_version.return_value = dict(FileUrl="s3://bucket/mau.docx", FieldsJson="{}",
        StructureJson=json.dumps({'binding':sample[1].model_dump()}), FileHashSha256=sample[1].file_sha256, VersionNo=2)
    monkeypatch.setattr(ai_templates, "materialize", AsyncMock(side_effect=lambda t: t))
    result = await ai_templates.get_version(12, 2)
    assert result.ai_version == 2 and result.ai_id == 12
    repo.template_version.assert_awaited_once_with(repo.templates.return_value[0], 2)
    repo.template_version.return_value['FileHashSha256']='0'*64
    with pytest.raises(IntegrationError,match='Hash mapping'):
        await ai_templates.get_version(12,2)


async def test_analyze_proposes_only_real_regions(sample, monkeypatch):
    path, mapping = sample
    monkeypatch.setattr(template_analyzer, "template_path", lambda _: path)
    writer = AsyncMock(return_value=dict(fields=[f.model_dump() for f in mapping.fields], questions=[]))
    monkeypatch.setattr(template_analyzer, "get_llm", lambda: SimpleNamespace(chat_json=writer))
    result = await template_analyzer.analyze("f")
    assert result["mapping"]["file_sha256"] == mapping.file_sha256
    writer.return_value["fields"][0]["target"] = "/w:document/w:body/w:p[1]"
    result = await template_analyzer.analyze("f")
    assert result['questions'] and len(result['mapping']['fields']) == 3


def test_table_copies_erp_rows_and_refuses_column_mismatch(sample, tmp_path):
    path, mapping = sample
    field = mapping.fields[1].model_copy(update=dict(kind="table", columns=["Đơn vị", "Số người"]))
    table_mapping = TemplateMapping(file_sha256=mapping.file_sha256, fields=[field])
    source = dict(id="erp", kind="du_lieu", text='{"Đơn vị":"A","Số người":0}',
        value=[["A", 0]], columns=field.columns, locator={"query_id": "q"})
    raw = [dict(id=field.id, value=[["B", 999]], evidence=[dict(source_id="erp", quote=source["text"])])]
    result = report_fields.resolve_fields(table_mapping, raw, [source], {})
    assert result[0]["value"] == [["A", 0]]
    output = binding.render(path, table_mapping, result, tmp_path / "bang.docx")
    assert Document(output).tables[0].cell(1, 1).text == "0"
    source["columns"] = ["Số người", "Đơn vị"]
    assert report_fields.resolve_fields(table_mapping, raw, [source], {})[0]["status"] == "needs_review"


def test_draft_contract_requires_published_version_and_selected_sources():
    from app.schemas.reports import DraftRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        DraftRequest(request="Báo cáo", template_id=12)
    with pytest.raises(ValidationError):
        DraftRequest(request="Báo cáo", nguon="kho_tri_thuc", template_id=12, template_version=2)
    assert DraftRequest(request="Báo cáo").nguon == "csdl"


def test_existing_example_needs_explicit_replacement_approval(sample, tmp_path):
    path, _ = sample
    doc = Document(path)
    doc.paragraphs[2].text = "Nội dung báo cáo mẫu của kỳ trước."
    doc.save(path)
    doc, sha = binding.read_template(path)
    row = binding.regions(doc)[2]
    mapping = TemplateMapping(file_sha256=sha, fields=[dict(id="noi_dung", target=row['target'],
        label="Nội dung", definition="Nhận xét kỳ báo cáo mới", kind="narrative")])
    with pytest.raises(IntegrationError):
        binding.validate(path, mapping)
    mapping.fields[0].replace_existing = True
    output = binding.render(path, mapping, [dict(id="noi_dung", value="Nội dung kỳ mới.", status="verified")], tmp_path/'moi.docx')
    assert Document(output).paragraphs[2].text == "Nội dung kỳ mới."


def test_number_substring_is_not_valid_evidence(sample):
    _, mapping = sample
    raw = [dict(id='tong', value='13', evidence=[dict(source_id='p1', quote='Có 113 người')])]
    sources = [dict(id='p1', kind='block', text='Có 113 người', locator={})]
    assert report_fields.resolve_fields(mapping, raw, sources, {})[1]['status'] == 'needs_review'


async def test_preview_upload_does_not_use_published_registry(sample, tmp_path, monkeypatch):
    from app.api.reports import preview_template
    from app.schemas.templates import TemplatePreviewRequest
    path, mapping = sample
    mapping.fields = [mapping.fields[0]]
    monkeypatch.setattr(template_analyzer, 'template_path', lambda _: path)
    monkeypatch.setattr(report_fields, 'get_version', AsyncMock(side_effect=AssertionError('Không đọc/ghi registry ở chế độ xem thử')))
    monkeypatch.setattr(report_fields, 'get_settings', lambda: SimpleNamespace(output_dir=tmp_path))
    result = await preview_template(TemplatePreviewRequest(file_id='upload:mau.docx', mapping=mapping,
        request='Xem thử mẫu', inputs={'nguoi_ky':'Nguyễn A'}))
    assert result.template_id is None and result.ai_task_id is None
    assert result.download_url.startswith('/api/reports/download/')
    assert Document(result.output_path).paragraphs[1].text == 'Người ký: Nguyễn A'


def test_fixed_table_has_metric_unit_period_and_renders_in_place(tmp_path):
    path = tmp_path/'bang.docx'
    doc = Document()
    table = doc.add_table(rows=3, cols=4)
    for row, values in zip(table.rows, [
        ['Chỉ tiêu','Đơn vị','Quý II/2026','Quý III/2026'],
        ['Số yêu cầu tiếp nhận','Lượt','........','........'],
        ['Tỷ lệ xử lý đúng SLA','%','........','........']]):
        for cell, value in zip(row.cells, values): cell.text=value
    doc.save(path)
    doc, sha = binding.read_template(path)
    candidates = [r for r in binding.regions(doc) if r['writable']]
    assert len(candidates)==4
    context = candidates[1]['table_context']
    assert context['row_index']==2 and context['column_index']==4
    assert context['column_context']==[['Quý III/2026']]
    assert [c['text'] for c in context['row_cells']][:2]==['Số yêu cầu tiếp nhận','Lượt']
    mapping = TemplateMapping(file_sha256=sha, fields=[dict(id='q3',label='Số yêu cầu quý III',
        kind='value',definition='Số yêu cầu quý III/2026, lượt',target=candidates[1]['target'])])
    out=binding.render(path,mapping,[dict(id='q3',value='125',status='verified')],tmp_path/'out.docx')
    result=Document(out).tables[0]
    assert result.cell(1,3).text=='125'
    assert result.cell(1,2).text=='........'
    assert result.cell(0,3).text=='Quý III/2026'


def test_merged_column_headers_keep_grid_positions(tmp_path):
    path=tmp_path/'gop.docx';doc=Document();table=doc.add_table(rows=3,cols=4)
    table.cell(0,0).merge(table.cell(0,1)).text='Thông tin chỉ tiêu'
    table.cell(0,2).merge(table.cell(0,3)).text='Năm 2026'
    for cell,value in zip(table.rows[1].cells,['Chỉ tiêu','Đơn vị','Quý II','Quý III']):cell.text=value
    for cell,value in zip(table.rows[2].cells,['Số yêu cầu','Lượt','...','...']):cell.text=value
    doc.save(path)
    rows=binding.regions(Document(path))
    target=next(r for r in rows if r.get('table_context',{}).get('row_index')==3
                and r.get('table_context',{}).get('column_index')==4)
    assert target['table_context']['column_context']==[['Năm 2026'],['Quý III']]


@pytest.mark.parametrize('value,kind,expected', [('12.100','integer','12100'),
    ('13.585','integer','13585'),('91,73','decimal','91.73'),('4,40','decimal','4.40'),
    ('1.886','decimal','1886'),('0','integer','0')])
def test_vietnamese_source_numbers_keep_display(sample,value,kind,expected):
    _, mapping=sample
    field=mapping.fields[1].model_copy(update={'data_type':kind})
    mapping=TemplateMapping(file_sha256=mapping.file_sha256,fields=[field])
    source=dict(id='p',kind='block',text='Giá trị xác nhận: '+value,locator={})
    raw=[dict(id=field.id,value=value,evidence=[dict(source_id='p',quote=source['text'])])]
    result=report_fields.resolve_fields(mapping,raw,[source],{})[0]
    assert result['status']=='verified'
    assert result['value']==value and result['numeric_value']==expected


def test_erp_decimal_is_not_parsed_as_vietnamese():
    assert report_fields.numeric('1.886','decimal')=='1.886'
    with pytest.raises(ValueError):
        report_fields.numeric('12,3,4','decimal',vietnamese=True)


@pytest.mark.parametrize('value,kind', [('13.585','integer'),('90,64','decimal'),('ABC-2027','text')])
def test_scalar_accepts_multiple_corroborating_sources(sample,value,kind):
    _,mapping=sample
    field=mapping.fields[1].model_copy(update={'data_type':kind})
    mapping=TemplateMapping(file_sha256=mapping.file_sha256,fields=[field])
    sources=[dict(id='table',kind='block',text='| Chỉ tiêu | '+value+' |',locator={}),
             dict(id='note',kind='block',text='Kết quả xác nhận: '+value,locator={})]
    evidence=[dict(source_id=s['id'],quote=s['text']) for s in sources]
    raw=[dict(id=field.id,value=value,evidence=evidence)]
    result=report_fields.resolve_fields(mapping,raw,sources,{})[0]
    assert result['status']=='verified' and result['value']==value and len(result['refs'])==2
    sources[1]['text']='Kết quả khác: 999'
    evidence[1]['quote']=sources[1]['text']
    assert report_fields.resolve_fields(mapping,raw,sources,{})[0]['status']=='needs_review'
    sources[1]['text']='Không có mã';evidence[1]['quote']='Bằng chứng bịa '+value
    assert report_fields.resolve_fields(mapping,raw,sources,{})[0]['status']=='needs_review'


def test_scalar_multiple_erp_cells_must_agree(sample):
    _,mapping=sample
    mapping.fields=[mapping.fields[1]]
    sources=[dict(id=str(i),kind='du_lieu',text='Ô nguồn',value=v,locator={}) for i,v in enumerate([13,14])]
    raw=[dict(id='tong',evidence=[dict(source_id=s['id'],quote=s['text']) for s in sources])]
    assert report_fields.resolve_fields(mapping,raw,sources,{})[0]['status']=='needs_review'
    sources[1]['value']=13
    assert report_fields.resolve_fields(mapping,raw,sources,{})[0]['status']=='verified'


@pytest.mark.parametrize('period,previous',[('2027-Q1','2026-Q4'),('2027-01','2026-12'),('2027','2026')])
def test_reusable_period_mapping_does_not_mutate_saved_template(sample,period,previous):
    _,mapping=sample
    mapping.fields[1].label='Kết quả {{ky}} so với {{ky_truoc}}'
    mapping.fields[1].definition='Chỉ tiêu trong kỳ {{ky}}'
    mapping.fields[0].input_key='ky'
    mapping.fields[0].text_template='BÁO CÁO {{ky}}'
    snapshot=mapping.model_dump()
    bound,inputs=report_fields.bind_period(mapping,period)
    assert bound.fields[1].label=='Kết quả '+period+' so với '+previous
    assert bound.fields[1].definition=='Chỉ tiêu trong kỳ '+period
    assert inputs['nguoi_ky']=='BÁO CÁO '+period
    assert mapping.model_dump()==snapshot
    with pytest.raises(IntegrationError,match='cần gửi period'):
        report_fields.bind_period(mapping,'')


async def test_confirmation_returns_durable_file_without_db_write(sample,monkeypatch):
    from app.services import object_storage
    path,mapping=sample
    monkeypatch.setattr(template_analyzer,'template_path',lambda _:path)
    upload=Mock(return_value=dict(checksum_sha256=mapping.file_sha256,storage_bucket='btl-ai',storage_object_key='uploads/template.docx'))
    monkeypatch.setattr(object_storage,'ObjectStorage',lambda:SimpleNamespace(upload=upload))
    result=await template_analyzer.confirm('f',mapping,store_file=True)
    assert result['file_url']=='minio://btl-ai/uploads/template.docx'
    assert result['structure_json']['binding']['file_sha256']==mapping.file_sha256
    upload.assert_called_once_with(path,path.name)


async def test_saved_template_uses_runtime_content_and_period(sample,tmp_path,monkeypatch):
    path,mapping=sample
    field=mapping.fields[1].model_copy(update={'definition':'Chỉ tiêu kỳ {{ky}}'})
    mapping.fields=[field]
    template=SimpleNamespace(fields={'binding':mapping.model_dump()},file_path=path,
        ai_id=12,ai_version=2,ma_template='BC',ten_bao_cao='Báo cáo')
    monkeypatch.setattr(report_fields,'get_version',AsyncMock(return_value=template))
    gather=AsyncMock(return_value=([],[]));monkeypatch.setattr(report_fields,'gather',gather)
    write=AsyncMock(return_value={'fields':[dict(id=field.id,value='13',evidence=[dict(source_id='user_content',quote='Có 13 người')])]})
    monkeypatch.setattr(report_fields,'get_llm',lambda:SimpleNamespace(chat_json=write))
    monkeypatch.setattr(report_fields,'get_settings',lambda:SimpleNamespace(output_dir=tmp_path))
    result=await report_fields.draft_from_mapping('Soạn báo cáo',12,2,{},'csdl','',[],None,[],period='2027-01',content='Có 13 người')
    assert result['field_results'][0]['value']=='13' and result['period']=='2027-01'
    assert '2027-01' in gather.call_args.args[0]
    assert result['all_refs'][0]['input_key']=='content'


async def test_workflow_forwards_selected_version_period_and_content(monkeypatch):
    from app.agents.graph import run_draft_workflow
    execute=AsyncMock(return_value={'request':'Báo cáo'})
    monkeypatch.setattr(report_fields,'draft_from_mapping',execute)
    # Gọi hàm nghiệp vụ để không tạo bản ghi tác vụ trong test này.
    result=await run_draft_workflow.__wrapped__(request='Báo cáo',template_id=12,
        template_version=2,period='2027-Q1',content='Thông tin thực tế')
    assert result['request']=='Báo cáo'
    assert execute.call_args.args[1:3]==(12,2)
    assert execute.call_args.kwargs=={'period':'2027-Q1','content':'Thông tin thực tế'}


async def test_two_report_periods_reuse_same_mapping_and_keep_headers(sample,tmp_path,monkeypatch):
    path,_=sample
    doc=Document(path)
    doc.paragraphs[2].text='KỲ HIỆN TẠI';doc.paragraphs[3].text='KỲ TRƯỚC';doc.save(path)
    doc,sha=binding.read_template(path)
    regions=binding.regions(doc)
    mapping=TemplateMapping(file_sha256=sha,fields=[
        dict(id='title',target=regions[2]['target'],label='Tiêu đề',kind='input',input_key='ky',
            replace_existing=True,text_template='BÁO CÁO {{ky}}'),
        dict(id='previous',target=regions[3]['target'],label='So sánh',kind='input',input_key='ky_truoc',
            replace_existing=True,text_template='KỲ SO SÁNH {{ky_truoc}}')])
    template=SimpleNamespace(fields={'binding':mapping.model_dump()},file_path=path,
        ai_id=12,ai_version=2,ma_template='BC',ten_bao_cao='Báo cáo')
    monkeypatch.setattr(report_fields,'get_version',AsyncMock(return_value=template))
    monkeypatch.setattr(report_fields,'get_settings',lambda:SimpleNamespace(output_dir=tmp_path))
    monkeypatch.setattr(report_fields,'get_llm',lambda:pytest.fail('Không gọi LLM cho mẫu chỉ có kỳ'))
    for current,previous in [('2027-Q1','2026-Q4'),('2027-Q2','2027-Q1')]:
        result=await report_fields.draft_from_mapping('Báo cáo',12,2,{},'csdl','',[],None,[],period=current)
        output=Document(result['output_path'])
        assert output.paragraphs[2].text=='BÁO CÁO '+current
        assert output.paragraphs[3].text=='KỲ SO SÁNH '+previous
    assert Document(path).paragraphs[2].text=='KỲ HIỆN TẠI'
