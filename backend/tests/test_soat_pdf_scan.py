"""Soát tài liệu (workflow 2) với PDF scan: OCR rồi soát, thay vì bỏ qua."""

import asyncio

from app.agents.nodes import document as node
from app.documents.parser import DocumentStructure, parse_ocr_pages
from app.documents.rules import RuleEngine

TRANG_1 = """# CHƯƠNG I
**QUY ĐỊNH CHUNG**
Điều 1. Phạm vi điều chỉnh
Nghị định này quy định về tuyển dụng
công chức.
Điều 2. Đối tượng áp dụng
1. Công chức.
2. Cơ quan quản lý công chức, bao gồm:
a) Bộ, cơ quan ngang Bộ;
b) Ủy ban nhân dân tỉnh."""

TRANG_2 = """| STT | Nội dung |
|---|---|
| 1 | Thi tuyển |
Điều 3. Hiệu lực thi hành
Nghị định này có hiệu lực từ ngày ký."""


def test_dung_khoi_tu_chu_ocr():
    s = parse_ocr_pages([TRANG_1, TRANG_2])
    assert s.source_format == "pdf_ocr" and s.page_count == 2 and not s.has_format_info
    texts = [b.text for b in s.blocks]
    # Markup mô hình tự thêm bị bỏ; dòng mở bằng chữ thường nối vào câu trước.
    assert texts[:3] == ["CHƯƠNG I", "QUY ĐỊNH CHUNG", "Điều 1. Phạm vi điều chỉnh"]
    assert "Nghị định này quy định về tuyển dụng công chức." in texts
    bang = [b for b in s.blocks if b.kind == "table"]
    assert len(bang) == 1 and bang[0].page == 2 and bang[0].text.count("\n") == 2
    assert {b.page for b in s.blocks} == {1, 2}


def test_rule_engine_kiem_to_chuc_noi_dung_tren_chu_ocr():
    result = RuleEngine().check(parse_ocr_pages([TRANG_1, TRANG_2]))
    assert result.status == "partial"
    assert "OCR" in result.reason
    assert "structure.empty_section" in result.passed


def _chay_parse(monkeypatch, pages):
    monkeypatch.setattr(node, "parse_document", lambda path: DocumentStructure(source_format="pdf_scan", page_count=2))
    monkeypatch.setattr(node, "doc_pdf_de_soat", lambda path: pages)
    return asyncio.run(node.parse_node({"file_path": "scan.pdf"}))


def test_parse_node_ocr_pdf_scan(monkeypatch):
    out = _chay_parse(monkeypatch, [TRANG_1, TRANG_2])
    assert out["structure"].source_format == "pdf_ocr"
    assert out["trace"]["ocr_pages"] == 2
    assert out["outline"].method == "legal_grammar"
    assert [h.text for h in out["outline"].headings][:2] == ["CHƯƠNG I QUY ĐỊNH CHUNG", "Điều 1. Phạm vi điều chỉnh"]


def test_parse_node_ocr_tat_hoac_khong_doc_duoc_giu_nhu_cu(monkeypatch):
    out = _chay_parse(monkeypatch, None)
    assert out["structure"].source_format == "pdf_scan" and out["trace"]["ocr_pages"] == 0
    assert RuleEngine().check(out["structure"]).status == "skipped"


def test_parse_node_ocr_loi_khong_lam_sap(monkeypatch):
    def hong(path):
        raise RuntimeError("vLLM không phản hồi")
    monkeypatch.setattr(node, "parse_document", lambda path: DocumentStructure(source_format="pdf_scan"))
    monkeypatch.setattr(node, "doc_pdf_de_soat", hong)
    out = asyncio.run(node.parse_node({"file_path": "scan.pdf"}))
    assert "error" not in out and out["structure"].source_format == "pdf_scan"


def test_pdf_co_chu_khong_goi_ocr(monkeypatch):
    goi = []
    monkeypatch.setattr(node, "parse_document", lambda path: parse_ocr_pages(["Điều 1. Phạm vi"]))
    monkeypatch.setattr(node, "doc_pdf_de_soat", lambda path: goi.append(path))
    asyncio.run(node.parse_node({"file_path": "x.pdf"}))
    assert goi == []
