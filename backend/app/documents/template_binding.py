"""Đọc và điền mẫu Word theo mapping/hash; không cho LLM sửa cấu trúc XML."""
import hashlib
import re
from pathlib import Path
from zipfile import ZipFile, BadZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from app.schemas.templates import TemplateMapping
from app.services.integration_errors import IntegrationError

BLANK = re.compile(r"[.．_]{3,}|…+")


def read_template(path):
    path = Path(path)
    try:
        if path.suffix.lower() != '.docx' or path.stat().st_size > 10*1024**2:
            raise ValueError('Mẫu phải là DOCX, tối đa 10 MB')
        with ZipFile(path) as z:
            if sum(f.file_size for f in z.infolist()) > 60*1024**2:
                raise ValueError('Mẫu vượt giới hạn giải nén 60 MB')
            for name in z.namelist():
                if 'vbaProject' in name or '/embeddings/' in name:
                    raise ValueError('Mẫu có macro/tệp nhúng chưa được hỗ trợ')
                if name.endswith('.rels') and b'TargetMode="External"' in z.read(name):
                    raise ValueError('Mẫu có liên kết ngoài; hãy nhúng tài nguyên vào file')
        return Document(path), hashlib.sha256(path.read_bytes()).hexdigest()
    except (ValueError, BadZipFile, KeyError, OSError) as exc:
        raise IntegrationError('INVALID_REQUEST', str(exc), 422) from exc


def table_context(doc):
    """Địa chỉ theo lưới Word, giữ cột gộp và ngữ cảnh hàng/cột cho Analyzer."""
    result = {}
    for number, table in enumerate(doc.element.body.iter(qn('w:tbl')), 1):
        rows = []
        for row in table.findall(qn('w:tr')):
            before = row.find('./' + qn('w:trPr') + '/' + qn('w:gridBefore'))
            col = int(before.get(qn('w:val'))) if before is not None else 0
            cells = []
            for cell in row.findall(qn('w:tc')):
                span = cell.find('./' + qn('w:tcPr') + '/' + qn('w:gridSpan'))
                width = int(span.get(qn('w:val'))) if span is not None else 1
                text = ' '.join(t.text or '' for t in cell.iter(qn('w:t')))
                cells.append((cell, col, width, text))
                col += width
            rows.append(cells)
        for i, cells in enumerate(rows):
            for cell, col, width, text in cells:
                headers = []
                for previous in rows[:min(i, 3)]:
                    headers.append([value for _, start, size, value in previous
                                    if start < col + width and col < start + size])
                info = dict(table_index=number, row_index=i+1, column_index=col+1,
                    column_span=width, row_cells=[dict(column=start+1, text=value) for _,start,_,value in cells],
                    column_context=headers)
                for p in cell.findall(qn('w:p')):
                    result[p.getroottree().getpath(p)] = info
    return result


def regions(doc):
    result, context = [], ''
    tables = table_context(doc)
    for element in doc.element.body.iter(qn('w:p')):
        if element.xpath('ancestor::w:txbxContent'):
            continue
        paragraph = Paragraph(element, doc)
        text = paragraph.text
        safe = not element.xpath('.//w:drawing | .//w:pict | .//w:fldChar | .//w:hyperlink | .//w:fldSimple')
        result.append(dict(target=element.getroottree().getpath(element), text=text, context=context[-200:], safe=safe,
            writable=safe and (not text.strip() or len(BLANK.findall(text)) == 1 or bool(re.fullmatch(r'\{\{\w+\}\}', text.strip()))),
            in_table=bool(element.xpath('ancestor::w:tc'))))
        if result[-1]['target'] in tables:
            result[-1]['table_context'] = tables[result[-1]['target']]
        if text.strip():
            context = text.strip()
    if len(result)>500 or sum(len(r['text']) for r in result)>60000:
        raise IntegrationError('CONTEXT_TOO_LONG','Mẫu vượt 500 đoạn/60.000 ký tự',413)
    return result


def validate(path, mapping):
    mapping = TemplateMapping.model_validate(mapping)
    doc, checksum = read_template(path)
    if mapping.file_sha256 != checksum:
        raise IntegrationError('INVALID_REQUEST','File mẫu không khớp mapping đã duyệt; cần phiên bản mới',409)
    validate_regions(regions(doc), mapping)
    return doc, mapping


def validate_regions(rows, mapping):
    candidates = {r['target']:r for r in rows}
    for field in mapping.fields:
        row = candidates.get(field.target)
        if row is None or not row['safe'] or (not row['writable'] and not field.replace_existing):
            raise IntegrationError('INVALID_REQUEST','Vị trí không hợp lệ hoặc có nội dung cố định: '+field.id,422)
        if field.kind in {'narrative','table'} and not field.replace_existing and row['text'].strip() and not BLANK.fullmatch(row['text'].strip()) and not re.fullmatch(r'\{\{\w+\}\}',row['text'].strip()):
            raise IntegrationError('INVALID_REQUEST','Vùng nội dung/bảng phải đứng riêng: '+field.id,422)
        if field.kind == 'table' and row['in_table']:
            raise IntegrationError('INVALID_REQUEST','Không chèn bảng lồng trong ô; hãy ánh xạ từng ô dữ liệu',422)


def replace_range(paragraph,start,end,value):
    offset=0; touched=[]
    for run in paragraph.runs:
        lo,hi=offset,offset+len(run.text);offset=hi
        if lo<end and hi>start:touched.append((run,lo))
    if not touched:
        paragraph.add_run(value); return
    first,lo=touched[0];last,last_lo=touched[-1]
    prefix,suffix=first.text[:start-lo],last.text[end-last_lo:]
    first.text=prefix+value+(suffix if first is last else '')
    for run,_ in touched[1:]:run.text=suffix if run is last else ''


def render(path,mapping,results,output):
    doc,mapping=validate(path,mapping)
    values={r['id']:r for r in results}
    # Chốt mọi vị trí trước khi chèn thêm đoạn/hàng, tránh lệch chỉ số XPath.
    targets={f.id:doc.element.xpath(f.target)[0] for f in mapping.fields}
    for field in mapping.fields:
        result=values[field.id];paragraph=Paragraph(targets[field.id],doc)
        value=result.get('value')
        if field.kind=='table' and result['status']=='verified':
            from app.documents.docx_builder import RenderedTable,_insert_table_after
            _insert_table_after(paragraph,doc,RenderedTable(field.columns,value))
            paragraph._p.getparent().remove(paragraph._p)
            continue
        text=str(value) if value is not None else '[Cần bổ sung: '+field.label+']'
        original=paragraph.text;match=None if field.replace_existing else BLANK.search(original)
        start,end=match.span() if match else (0,len(original))
        replace_range(paragraph,start,end,text)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    doc.save(output)
    # Mở lại để kiểm tra gói DOCX hợp lệ trước khi trả file.
    Document(output)
    return output
