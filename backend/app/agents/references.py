"""Tham chiếu nguồn cho mọi câu chữ do LLM sinh ra.

Mục đích thực dụng: UI bấm vào `[1]` là hiện ngay đoạn nguồn. Muốn vậy thì mỗi
tham chiếu phải tự mang theo `snippet` - không bắt UI gọi ngược lại backend để
tra, vì lúc đó ngữ cảnh (chunk nào, khối nào, kỳ nào) đã mất rồi.

Workflow 1 đã có `Citation` riêng với hợp đồng API ổn định nên giữ nguyên; chỗ
này dành cho bốn workflow còn lại và vòng lặp agent, vốn trước đây sinh chữ mà
không để lại dấu vết nào về nguồn.

    kind        nguồn                        locator
    ---------------------------------------------------------------
    block       một khối trong văn bản đọc   {"block_id": "P07"}
    quy_dinh    chunk quy định từ RAG        {"doc_title", "section"}
    du_lieu     một mục số liệu từ SQL       {"key": "personnel.total"}
    cong_cu     một lời gọi tool             {"tool", "arguments", "step"}
"""

from __future__ import annotations

import json
import re
from typing import Any, TypedDict

MARKER_RE = re.compile(r"\[(\d{1,2})\]")

SNIPPET_LIMIT = 400


class Reference(TypedDict, total=False):
    id: int                 # số hiện trong dấu ngoặc: [1]
    kind: str
    label: str              # nhãn ngắn hiện cạnh câu trả lời
    snippet: str            # nguyên văn đoạn nguồn - thứ UI hiện khi bấm vào
    locator: dict[str, Any]  # đủ để UI mở lại đúng chỗ trong nguồn gốc
    score: float


def _snippet(text: str, limit: int = SNIPPET_LIMIT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "…"


def make(kind: str, label: str, snippet: str, locator: dict[str, Any] | None = None,
         score: float | None = None) -> Reference:
    ref: Reference = {"kind": kind, "label": label, "snippet": _snippet(snippet),
                      "locator": locator or {}}
    if score is not None:
        ref["score"] = round(float(score), 3)
    return ref


def number(refs: list[Reference], start: int = 1) -> list[Reference]:
    """Đánh số 1..n để khớp với marker [n] mà model viết trong câu trả lời."""
    return [{**ref, "id": start + i} for i, ref in enumerate(refs)]


def merge(*groups: list[Reference]) -> list[Reference]:
    """Gộp nhiều nhóm nguồn thành một danh sách đánh số liên tục 1..n."""
    return number([ref for group in groups for ref in group])


def render(refs: list[Reference]) -> str:
    """Khối nguồn đưa vào prompt. Model đọc số ở đây rồi trích dẫn lại đúng số đó."""
    if not refs:
        return "(không có)"
    return "\n\n".join(f"[{r['id']}] {r['label']}\n{r['snippet']}" for r in refs)


def from_blocks(blocks: list[Any]) -> list[Reference]:
    """Khối văn bản đọc được -> tham chiếu. `blocks` là `app.documents.parser.Block`."""
    return number([
        make("block", b.id, b.text, {"block_id": b.id})
        for b in blocks if not b.is_empty
    ])


def from_regulations(regulations: list[dict[str, Any]]) -> list[Reference]:
    return number([
        make("quy_dinh",
             f"{r.get('doc_title', '')}{' · ' + r['section'] if r.get('section') else ''}",
             r.get("text", ""),
             {"doc_title": r.get("doc_title", ""), "section": r.get("section", "")},
             r.get("score"))
        for r in regulations
    ])


def from_data(data: dict[str, Any], prefix: str = "") -> list[Reference]:
    """Cây số liệu SQL -> mỗi nhánh lá một tham chiếu.

    Chỉ đi xuống một mức có nghĩa nghiệp vụ (personnel.metrics, equipment.metrics)
    chứ không bung tới từng con số: model trích dẫn "bảng quân số", không trích
    dẫn "ô 113".
    """
    refs: list[Reference] = []
    for key, value in data.items():
        if key.endswith("_consistent") or value in (None, {}, []):
            continue
        name = f"{prefix}{key}"
        refs.append(make("du_lieu", name,
                         json.dumps(value, ensure_ascii=False, default=str),
                         {"key": name}))
    return number(refs)


def from_tool_log(tool_log: list[dict[str, Any]]) -> list[Reference]:
    """Mỗi lời gọi tool là một nguồn: tra gì, tham số nào, trả về ra sao.

    Số lấy từ `ref_id` đã phát trong vòng lặp chứ không đánh lại: model đã nhìn
    thấy số đó rồi, đánh lại là lệch hết trích dẫn.
    """
    refs: list[Reference] = []
    for item in tool_log:
        args = ", ".join(f"{k}={v!r}" for k, v in (item.get("arguments") or {}).items())
        refs.append(make(
            "cong_cu", f"{item.get('tool', '')}({args})",
            str(item.get("preview", "")),
            {"tool": item.get("tool", ""), "arguments": item.get("arguments") or {},
             "step": item.get("step"), "ok": item.get("ok", True)},
        ))
    ids = [item.get("ref_id") for item in tool_log]
    if all(isinstance(i, int) for i in ids):
        return [{**ref, "id": i} for ref, i in zip(refs, ids, strict=True)]
    return number(refs)


def used(text: str, refs: list[Reference]) -> list[Reference]:
    """Chỉ giữ nguồn thực sự được trích trong câu trả lời.

    Model quên đánh số thì trả về toàn bộ - thà thừa nguồn còn hơn câu trả lời
    trông như không có căn cứ nào.
    """
    ids = {int(n) for n in MARKER_RE.findall(text or "")}
    hit = [r for r in refs if r.get("id") in ids]
    return hit or refs


def renumber(texts: list[str], refs: list[Reference]) -> tuple[list[str], list[Reference]]:
    """Đánh số lại theo thứ tự xuất hiện trong câu chữ: [4][13][22] -> [1][2][3].

    Số ban đầu là địa chỉ nguồn (khối P13, bước tool 4) nên nhảy cóc. Với UI thì
    vô hại, nhưng câu trả lời đọc dạng chữ mà mang "[1][5][16][17]" thì trông như
    hỏng. Địa chỉ thật không mất - nó nằm trong `locator`.

    Các text truyền vào dùng CHUNG một dãy số, nên [1] ở đoạn tóm tắt và [1] ở
    một nhiệm vụ phía dưới vẫn trỏ cùng một nguồn.
    """
    by_id = {r["id"]: r for r in refs if "id" in r}
    thu_tu: list[int] = []
    for text in texts:
        for raw in MARKER_RE.findall(text or ""):
            cu = int(raw)
            if cu in by_id and cu not in thu_tu:
                thu_tu.append(cu)
    if not thu_tu:
        return list(texts), []

    anh_xa = {cu: moi for moi, cu in enumerate(thu_tu, start=1)}

    def _doi(match: re.Match[str]) -> str:
        cu = int(match.group(1))
        return f"[{anh_xa[cu]}]" if cu in anh_xa else ""

    return ([MARKER_RE.sub(_doi, text or "") for text in texts],
            [{**by_id[cu], "id": moi} for cu, moi in anh_xa.items()])


def shift(texts: list[str], refs: list[Reference], offset: int) -> tuple[list[str], list[Reference]]:
    """Dời cả marker lẫn id đi `offset` để ghép nhiều nguồn vào một câu trả lời.

    Mỗi bước của kế hoạch tự đánh số nguồn từ [1]; nối hai bước lại thì người đọc
    thấy hai cái [1] trỏ hai chỗ khác nhau. Dời dãy số của bước sau ra sau bước
    trước là cách rẻ nhất để một câu trả lời ghép vẫn có đúng một dãy nguồn.
    """
    if offset <= 0 or not refs:
        return list(texts), list(refs)

    def _doi(match: re.Match[str]) -> str:
        return f"[{int(match.group(1)) + offset}]"

    return ([MARKER_RE.sub(_doi, text or "") for text in texts],
            [{**ref, "id": ref["id"] + offset} for ref in refs if "id" in ref])


def strip_markers(text: str) -> str:
    """Bỏ marker khỏi câu chữ sẽ đổ vào DOCX/PPTX - văn bản hành chính không có "[1]"."""
    return re.sub(r"\s*\[\d{1,2}\]", "", text or "").strip()
