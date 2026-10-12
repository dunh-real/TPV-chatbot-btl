"""Dựng bảng Word và slide trực tiếp từ QueryResult, không phụ thuộc tên chỉ tiêu cố định."""
from __future__ import annotations

from app.documents.nhan_cot import lam_sach_ket_qua
from app.schemas.erp import QueryResult
from app.tools.base import ToolError


def cell(value):
    from app.documents.slide_deck import gia_tri_chu

    return "—" if value is None else gia_tri_chu(value)


def table(result):
    # Không bao giờ in tên trường CSDL làm tiêu đề cột: nhãn thiếu/không dấu thay bằng nhãn đọc được.
    query = QueryResult.model_validate(lam_sach_ket_qua(result))
    if query.truncated:
        raise ToolError("Kết quả bị cắt; cần thu hẹp hoặc tổng hợp dữ liệu trước khi xuất tài liệu")
    columns = [c.label + (f" ({c.unit})" if c.unit else "") for c in query.columns]
    return query, {"columns": columns, "rows": [[cell(v) for v in row] for row in query.rows]}


def query_sections(results):
    sections = []
    for index, result in enumerate(results):
        query, data = table(result)
        sections.append({"id": f"erp_{index}", "kind": "table" if query.rows else "data", "title": query.question.splitlines()[0][:200],
            "paragraphs": ["Nguồn số liệu: ERP.", *query.warnings,
                           *([] if query.rows else ["Không có dữ liệu thỏa điều kiện truy vấn."])],
            "table": data if query.rows else None, "image": None, "image_caption": "",
            "llm_written": [], "source_data": {"rows": result["rows"]}})
    return sections


def slide_tables(results):
    pages = []
    for result in results:
        query, data = table(result)
        # Giữ thứ tự dòng và tất cả cột; bảng rộng/dài được chia thành nhiều slide.
        for start in range(0, len(data["rows"]), 6):
            for column in range(0, len(data["columns"]), 5):
                pages.append(("Số liệu ERP", {
                    "columns": data["columns"][column:column+5],
                    "rows": [row[column:column+5] for row in data["rows"][start:start+6]]}))
    if len(pages) > 60:
        raise ToolError("Bảng kết quả cần quá 60 slide; hãy yêu cầu tổng hợp dữ liệu hoặc thu hẹp phạm vi")
    return pages


def query_slides(results, inputs=None):
    slides = ["# " + (inputs or {}).get("trich_yeu", "Báo cáo dữ liệu ERP")]
    def escaped(value):
        return value.replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    for title, data in slide_tables(results):
        lines = ["## " + title, "", "| " + " | ".join(map(escaped, data["columns"])) + " |",
                 "| " + " | ".join("---" for _ in data["columns"]) + " |"]
        lines += ["| " + " | ".join(map(escaped, row)) + " |" for row in data["rows"]]
        slides.append("\n".join(lines))
    warnings = list(dict.fromkeys(w for r in results for w in r.get("warnings", [])))
    if not any(r["rows"] for r in results):
        warnings.append("Không có dữ liệu thỏa điều kiện truy vấn.")
    if warnings:
        slides.append("## Phạm vi dữ liệu\n\n" + "\n".join("- " + w for w in warnings))
    return slides


def query_deck(results):
    from app.documents.pptx_builder import DeckSpec, SlideSpec, SlideTable
    slides = [SlideSpec(kind="title", title="Báo cáo dữ liệu ERP")]
    slides += [SlideSpec(kind="table", title=title, table=SlideTable(**data)) for title, data in slide_tables(results)]
    warnings = list(dict.fromkeys(w for r in results for w in r.get("warnings", [])))
    if not any(r["rows"] for r in results):
        warnings.append("Không có dữ liệu thỏa điều kiện truy vấn.")
    if warnings:
        slides.append(SlideSpec(kind="bullet", title="Phạm vi dữ liệu", bullets=warnings))
    return DeckSpec(title="Báo cáo dữ liệu ERP", slides=slides)
