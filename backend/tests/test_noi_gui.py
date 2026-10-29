"""Dòng nơi gửi (đơn vị ban hành) căn giữa trên file Word; WF4 có giá trị mặc định khi ô để trống."""

from __future__ import annotations

import tempfile
from pathlib import Path

import docx
from docx.enum.text import WD_ALIGN_PARAGRAPH

from app.agents.nodes import report
from app.documents.docx_builder import DocumentPayload, RenderedSection, build_docx


def test_noi_gui_can_giua_tren_word():
    meta = {"noi_gui": "PHÒNG HÀNH CHÍNH QUẢN TRỊ", "so_ky_hieu": "15/BC-HCQT", "dia_danh": "Hà Nội",
            "ngay_bao_cao": "ngày 05 tháng 10 năm 2026", "trich_yeu": "V/v báo cáo", "nguoi_ky": "A", "chuc_vu_ky": "B"}
    with tempfile.TemporaryDirectory() as d:
        path = build_docx(DocumentPayload(meta=meta, sections=[RenderedSection(id="a", title="I. A", paragraphs=["x"])]),
                          Path(d) / "vb.docx")
        paras = docx.Document(str(path)).paragraphs
    noi_gui = next(p for p in paras if p.text == "PHÒNG HÀNH CHÍNH QUẢN TRỊ")
    assert noi_gui.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert "Số: 15/BC-HCQT" in [p.text for p in paras]


def test_wf4_noi_gui_mac_dinh():
    assert report.NOI_GUI_MAC_DINH == "PHÒNG HÀNH CHÍNH QUẢN TRỊ"
