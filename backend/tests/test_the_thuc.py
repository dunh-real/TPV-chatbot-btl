"""Số văn bản và ngày ban hành do người dùng nhập: đúng vị trí trên văn bản, kiểm định dạng, không ghi đè sổ."""

from __future__ import annotations

from datetime import date

import pytest

from app.agents.nodes import drafting
from app.documents import the_thuc as tt


@pytest.mark.parametrize(("raw", "expected"), [
    ("05/10/2026", date(2026, 10, 5)), ("5/10/2026", date(2026, 10, 5)), ("05-10-2026", date(2026, 10, 5)),
    ("05.10.2026", date(2026, 10, 5)), ("2026-10-05", date(2026, 10, 5)),
])
def test_doc_ngay_ban_hanh_nhieu_dinh_dang(raw, expected):
    assert tt.doc_ngay_ban_hanh({"ngay_ban_hanh": raw}) == expected


def test_ngay_trong_thi_hom_nay_sai_thi_bao_loi():
    assert tt.doc_ngay_ban_hanh({}) == date.today()
    assert tt.doc_ngay_ban_hanh({"ngay_ban_hanh": "  "}) == date.today()
    with pytest.raises(ValueError, match="không đúng định dạng"):
        tt.doc_ngay_ban_hanh({"ngay_ban_hanh": "mai"})
    with pytest.raises(ValueError, match="không có thật"):
        tt.doc_ngay_ban_hanh({"ngay_ban_hanh": "31/02/2026"})
    assert tt.ngay_ban_hanh_an_toan({"ngay_ban_hanh": "mai"}) == date.today()


def _state(inputs):
    return {"inputs": inputs, "params": {"ky": "2026-09", "thang": 9, "nam": 2026}, "request": "Báo cáo",
            "data": {"ten_don_vi": "Phòng A"}, "ma_don_vi": "PA", "so_ky_hieu": "07/BC-PA"}


TEMPLATE = {"ten_bao_cao": "Báo cáo nhân sự",
            "fields": {"meta": {"ngay_bao_cao": {"source": "today"}, "nguoi_ky": {"source": "input"}}}}


def test_meta_dung_ngay_ban_hanh_va_so_nguoi_dung_nhap():
    meta = drafting._resolve_meta(TEMPLATE, _state({"ngay_ban_hanh": "05/10/2026", "so_ky_hieu": "15/BC-PA"}))
    assert meta["ngay_bao_cao"] == "ngày 05 tháng 10 năm 2026"
    assert meta["so_ky_hieu"] == "15/BC-PA"


def test_o_trong_khong_che_so_he_thong_cap():
    """Giao diện gửi cả ô để trống: "Số:" vẫn là số sổ cấp, ngày vẫn là hôm nay."""
    meta = drafting._resolve_meta(TEMPLATE, _state({"so_ky_hieu": "", "ngay_ban_hanh": ""}))
    assert meta["so_ky_hieu"] == "07/BC-PA"
    assert meta["ngay_bao_cao"] == tt.ngay_tieng_viet(date.today())


def test_vi_tri_tren_van_ban_dung_code():
    """Đường dựng bằng code: số ở dòng "Số: ...", ngày ở dòng "<địa danh>, ngày ... tháng ... năm ..."."""
    import docx

    from app.documents.docx_builder import DocumentPayload, RenderedSection, build_docx

    meta = drafting._resolve_meta(TEMPLATE, _state({"ngay_ban_hanh": "05/10/2026", "so_ky_hieu": "15/BC-PA",
                                                     "nguoi_ky": "Nguyễn Văn A"}))
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = build_docx(DocumentPayload(meta=meta, sections=[RenderedSection(id="a", title="I. A", paragraphs=["x"])]),
                          Path(d) / "vb.docx")
        lines = [p.text for p in docx.Document(str(path)).paragraphs]
    assert "Số: 15/BC-PA" in lines
    assert "Hà Nội, ngày 05 tháng 10 năm 2026" in lines


class _Repo:
    def __init__(self, existing):
        self.existing = existing

    async def get(self, ma):
        return self.existing.get(ma)


async def test_so_trung_trong_so_thi_bao_loi(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock
    from app.db import ai_session
    repo = AsyncMock()
    repo.number_exists.side_effect = lambda number, **kw: number == "15/BC-PA"
    @asynccontextmanager
    async def scope():
        yield repo
    monkeypatch.setattr(ai_session, "repository", scope)
    assert "đã có trong sổ MSSQL" in await tt.kiem_so_van_ban("15/BC-PA")
    assert await tt.kiem_so_van_ban("16/BC-PA") == ""
    assert await tt.kiem_so_van_ban("") == ""
