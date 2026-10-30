"""Tóm tắt toàn bộ tài liệu theo lô, giữ nguồn gốc qua bước tổng hợp."""
import json
import re
from pathlib import Path

from app.agents import references
from app.agents.nodes.document import parse_node
from app.agents.prompts import SUMMARY_SYSTEM
from app.documents.verify import check_numbers, collect_known_numbers
from app.schemas.summaries import SummaryContent
from app.services.llm import get_llm

MAX_CHARS = 120000
BATCH_CHARS = 12000
LENGTHS = {"short": (100, 150), "medium": (250, 350), "long": (500, 700)}
MARKER = re.compile(r"\[(\d+)\]")


def source_batches(blocks):
    """Không cắt bỏ phần cuối; khối quá dài được chia nhưng giữ block_id và page."""
    blocks = [b for b in blocks if not b.is_empty]
    if not blocks:
        raise ValueError("Tài liệu không có nội dung đọc được; hãy kiểm tra file hoặc OCR")
    total = sum(len(b.text) for b in blocks)
    if total > MAX_CHARS:
        raise ValueError(f"Tài liệu vượt {MAX_CHARS} ký tự; hãy chọn phạm vi cần tóm tắt")
    batches, batch, size = [], [], 0
    for block in blocks:
        for offset in range(0, len(block.text), BATCH_CHARS):
            text = block.text[offset:offset+BATCH_CHARS]
            if batch and size + len(text) > BATCH_CHARS:
                batches.append(batch); batch, size = [], 0
            batch.append(dict(text=text, block_id=block.id, page=block.page, char_start=offset))
            size += len(text)
    if batch:
        batches.append(batch)
    index = 0
    for batch in batches:
        for item in batch:
            index += 1; item["id"] = index
    return batches, total


async def generate(content, sources, length, structure, focus):
    low, high = LENGTHS[length]
    messages = [{"role": "system", "content": SUMMARY_SYSTEM}, {"role": "user", "content":
        f"Mục tiêu {low}-{high} từ; cấu trúc {structure}; trọng tâm: {focus or 'các ý chính toàn văn'}.\n"
        "Nguồn (hoặc bản tóm lược từng phần đã dẫn nguồn gốc):\n" + json.dumps(content, ensure_ascii=False)}]
    for attempt in range(2):
        raw = await get_llm().chat_json(messages, temperature=0, max_tokens=2500 if length=="long" else 1600,
                                       thinking=False)
        try:
            result = SummaryContent.model_validate(raw)
            for section in result.sections:
                if not MARKER.sub("",section.text).strip(" -.*"):
                    raise ValueError("Bản tóm tắt không có nội dung")
                cited = {int(n) for n in MARKER.findall(section.text)}
                if not cited or not cited <= sources.keys():
                    raise ValueError("Mỗi mục cần trích dẫn nguồn có thật trong đầu vào")
                if any(not MARKER.search(p) for p in section.text.splitlines() if p.strip()):
                    raise ValueError("Mỗi đoạn/ý phải có trích dẫn riêng")
                if not {int(n) for n in MARKER.findall(section.heading)} <= cited:
                    raise ValueError("Nguồn trong tiêu đề phải khớp nguồn của mục")
                if structure=="sections" and not section.heading.strip():
                    raise ValueError("Cấu trúc chia mục cần tiêu đề cho mỗi mục")
                original = "\n".join(sources[i]["text"] for i in cited)
                heading = re.sub(r"^\s*\d{1,2}[.)]\s+", "", section.heading)
                checked = MARKER.sub("", heading + " " + section.text)
                missing = check_numbers(checked, collect_known_numbers(original)).unverified
                if missing:
                    raise ValueError("Số/mã không khớp nguồn được dẫn: " + ", ".join(missing))
            return result.sections
        except ValueError as exc:
            if attempt:
                raise ValueError("Không tạo được bản tóm tắt có nguồn hợp lệ: " + str(exc)) from exc
            messages.extend([{"role":"assistant", "content":json.dumps(raw,ensure_ascii=False)},
                             {"role":"user", "content":f"Sửa JSON, giữ nguyên nguồn gốc. Lỗi: {exc}"}])


async def summarize(file_path, file_name, length, structure, focus):
    if Path(file_path).stat().st_size > 20 * 1024**2:
        raise ValueError("File vượt giới hạn 20 MB")
    state = await parse_node({"file_path":file_path, "file_name":file_name, "trace":{}})
    if state.get("error"):
        raise ValueError(state["error"])
    batches, total = source_batches(state["structure"].blocks)
    sources = {i["id"]:i for batch in batches for i in batch}
    if len(batches)==1:
        sections = await generate(batches[0],sources,length,structure,focus)
    else:
        mapped = []
        for batch in batches:
            mapped.extend(await generate(batch,{i["id"]:i for i in batch},"medium","sections",focus))
        used = {int(n) for section in mapped for n in MARKER.findall(section.text)}
        sections = await generate([s.model_dump() for s in mapped],{i:sources[i] for i in used},
                                  length,structure,focus)
    texts = [s.text for s in sections]
    used = list(dict.fromkeys(int(n) for text in texts for n in MARKER.findall(text)))
    ids = {old:new for new,old in enumerate(used,1)}
    for section in sections:
        section.text = MARKER.sub(lambda m:f"[{ids[int(m[1])]}]",section.text)
        section.heading = MARKER.sub(lambda m:f"[{ids[int(m[1])]}]",section.heading)
    refs = [dict(references.make("block",sources[i]["block_id"],sources[i]["text"],
                {k:sources[i][k] for k in ("block_id","page","char_start")}),id=ids[i]) for i in used]
    if structure=="sections":
        summary = "\n\n".join((f"### {s.heading}\n" if s.heading else "") + s.text for s in sections)
    else:
        summary = ("\n" if structure=="bullets" else "\n\n").join(
            ("- " if structure=="bullets" else "") + s.text for s in sections)
    words = len(MARKER.sub("", " ".join(s.text for s in sections)).split())
    low,high = LENGTHS[length]
    return dict(summary=summary,sections=[s.model_dump() for s in sections],summary_refs=refs,all_refs=refs,
        length=length,structure=structure,word_count=words,
        coverage=dict(complete=True,batches=len(batches),source_chars=total),
        warnings=[] if low<=words<=high else [f"Bản tóm tắt có {words} từ; mục tiêu {low}-{high} từ."],error="")
