"""Services for creating Word documents from templates and LLM mappings.
This module exposes functions used by the pipeline to:
 - load a template .docx
 - extract placeholder fields (e.g., {{field_name}})
 - call the LLM to map user chat to fields and generate paragraph text
 - fill the template with values and save to output directory
"""
import os
import re
import json
import uuid
import datetime
from typing import List, Dict, Tuple, Optional
from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH

# local imports
from src.services.llm_service import get_ollama_llm
from src.services.prompt_service import PromptBuilder

PLACEHOLDER_REGEX = re.compile(r"\{\{\s*([^\}]+?)\s*\}\}")


def ensure_output_dir(output_dir: str):
    os.makedirs(output_dir, exist_ok=True)


def load_template(template_path: str) -> Document:
    """Load .docx template and return python-docx Document"""
    if not os.path.exists(template_path):
        raise FileNotFoundError(f"Template not found: {template_path}")
    return Document(template_path)


def find_placeholders_in_doc(doc: Document) -> List[str]:
    """Scan paragraphs and table cells for placeholders like {{field}} and return unique list."""
    found = []

    def scan_text(text: str):
        if not text:
            return
        for m in PLACEHOLDER_REGEX.findall(text):
            nm = m.strip()
            if nm and nm not in found:
                found.append(nm)

    # paragraphs
    for p in doc.paragraphs:
        scan_text(p.text)

    # tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                scan_text(cell.text)

    # headers/footers (best-effort)
    try:
        for section in doc.sections:
            header = section.header
            for p in header.paragraphs:
                scan_text(p.text)
            footer = section.footer
            for p in footer.paragraphs:
                scan_text(p.text)
    except Exception:
        # not all docs expose header/footer or may be empty
        pass

    return found


def extract_template_fields(template_path: str) -> List[str]:
    """Convenience: load template and extract placeholders."""
    doc = load_template(template_path)
    return find_placeholders_in_doc(doc)


def map_user_info_to_fields(template_fields: List[str], user_chat: str, template_summary: str = "") -> Dict[str, str]:
    """Call LLM to map user_chat into template_fields and synthesize paragraph text.
    Returns a mapping for fields and optional generated_paragraphs under key '__generated_paragraphs'.
    """
    llm = get_ollama_llm()
    pb = PromptBuilder()

    messages = pb.build_doc_generation_messages(template_fields, user_chat, template_summary)

    raw_text, err = llm.invoke_raw(messages)
    if err:
        raise RuntimeError(f"LLM error: {err}")

    # parse raw_text as JSON
    try:
        parsed = json.loads(raw_text)
    except Exception as e:
        # attempt to extract JSON substring
        jmatch = re.search(r"(\{[\s\S]*\})", raw_text)
        if jmatch:
            try:
                parsed = json.loads(jmatch.group(1))
            except Exception:
                raise ValueError(f"Không thể parse JSON từ LLM output: {str(e)} -- raw: {raw_text}")
        else:
            raise ValueError(f"Không tìm thấy JSON trong output của LLM. Raw output: {raw_text}")

    fields_map = parsed.get('fields', {}) if isinstance(parsed, dict) else {}
    generated = parsed.get('generated_paragraphs', {}) if isinstance(parsed, dict) else {}

    # normalize: ensure keys for all template_fields
    result_map = {k: (fields_map.get(k, "") if isinstance(fields_map, dict) else "") for k in template_fields}
    # attach generated paragraphs under special key
    if generated:
        result_map['__generated_paragraphs'] = generated

    return result_map


def _replace_in_paragraph(paragraph, mapping: Dict[str, str]):
    """Replace placeholders in a paragraph's runs. This is a best-effort method.
    python-docx may split runs; so we perform a simple full-text replace by reconstructing text and then replacing.
    """
    text = paragraph.text
    if not text:
        return
    new_text = text
    for k, v in mapping.items():
        token = f"{{{{{k}}}}}"
        # also accept whitespace inside braces
        new_text = re.sub(r"\{\{\s*" + re.escape(k) + r"\s*\}\}", v or "", new_text)

    if new_text != text:
        # clear existing runs and add single run
        for i in range(len(paragraph.runs)-1, -1, -1):
            paragraph._element.remove(paragraph.runs[i]._r)
        paragraph.add_run(new_text)


def fill_template_with_mapping(doc: Document, mapping: Dict[str, str]):
    """Fill placeholders throughout the document using mapping (field->value)."""
    # paragraphs
    for p in doc.paragraphs:
        _replace_in_paragraph(p, mapping)

    # tables
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _replace_in_paragraph(p, mapping)

    # headers/footers
    try:
        for section in doc.sections:
            header = section.header
            for p in header.paragraphs:
                _replace_in_paragraph(p, mapping)
            footer = section.footer
            for p in footer.paragraphs:
                _replace_in_paragraph(p, mapping)
    except Exception:
        pass


def save_document(doc: Document, template_path: str, output_dir: str) -> str:
    ensure_output_dir(output_dir)
    base = os.path.splitext(os.path.basename(template_path))[0]
    ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    uid = uuid.uuid4().hex[:6]
    out_name = f"{base}_generated_{ts}_{uid}.docx"
    out_path = os.path.join(output_dir, out_name)
    doc.save(out_path)
    return out_path


def generate_document(template_path: str, user_chat: str, output_dir: str = 'src/output_templates') -> str:
    """Main pipeline: load template, extract fields, call LLM, fill template, save and return path."""
    # load
    doc = load_template(template_path)

    # get fields
    template_fields = find_placeholders_in_doc(doc)

    # derive a tiny template summary (first 300 chars) to help LLM
    try:
        first_texts = []
        for p in doc.paragraphs[:10]:
            if p.text.strip():
                first_texts.append(p.text.strip())
        template_summary = ' '.join(first_texts)[:800]
    except Exception:
        template_summary = ''

    # map user info to fields
    mapping = map_user_info_to_fields(template_fields, user_chat, template_summary)

    # if generated paragraphs exist, insert them into the document at special placeholders
    generated = mapping.pop('__generated_paragraphs', {}) if isinstance(mapping, dict) else {}

    # fill template placeholders
    fill_template_with_mapping(doc, mapping)

    # Optionally insert generated paragraphs into markers like {{__intro}} or simply append at the end
    if generated:
        for section_name, paragraph_text in generated.items():
            # try to replace a placeholder matching the section_name first
            token = f"{{{{{section_name}}}}}"
            replaced = False
            for p in doc.paragraphs:
                if token in p.text:
                    _replace_in_paragraph(p, {section_name: paragraph_text})
                    replaced = True
                    break
            if not replaced and paragraph_text:
                pnew = doc.add_paragraph(paragraph_text)
                pnew.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    # save
    out_path = save_document(doc, template_path, output_dir)
    return out_path


# if run as script for quick manual test
if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Generate document from template and user chat')
    parser.add_argument('template', help='Path to template .docx')
    parser.add_argument('user_chat', help='User chat/content to map into template (wrap in quotes)')
    parser.add_argument('--out', default='src/output_templates', help='Output folder')
    args = parser.parse_args()

    out = generate_document(args.template, args.user_chat, args.out)
    print(json.dumps({'file_path': out}))
