"""Biểu đồ chuẩn dùng chung cho báo cáo và slide (app.documents.charts)."""

from __future__ import annotations

import struct

import matplotlib.pyplot as plt
import pytest

from app.documents import charts
from app.documents.slide_deck import Dataset


def _png_size(png: bytes) -> tuple[int, int]:
    assert png[1:4] == b"PNG"
    return struct.unpack(">II", png[16:24])


def _ds(categories, series) -> Dataset:
    return Dataset(key="k", title="T", short="T", group_label="Nhóm", unit="người",
                   categories=categories, series=series, table_columns=[], table_rows=[])


def test_anh_dung_ty_le_o_bieu_do():
    png = charts.dataset_chart(["A", "B"], [("Số", [3, 5])], size_px=(560, 334))
    w, h = _png_size(png)
    assert (w, h) == (560 * charts.DPI // 100, 334 * charts.DPI // 100)


def test_danh_muc_trung_ten_van_la_hai_cot(monkeypatch):
    """Hai phòng ban cùng tên phải là hai cột - không gộp/lấy trung bình như sns.barplot."""
    ve: list[list[float]] = []
    goc = plt.Axes.barh

    def ghi(self, y, width, *args, **kwargs):
        ve.append(list(width))
        return goc(self, y, width, *args, **kwargs)

    monkeypatch.setattr(plt.Axes, "barh", ghi)
    charts.dataset_chart(["Kế toán", "Kế toán", "Kỹ thuật", "Hành chính"], [("Nhân sự", [4, 6, 9, 1])])
    assert ve == [[4.0, 6.0, 9.0, 1.0]]


def test_nhieu_chuoi_va_cot_ngang_tu_nguong():
    ds = _ds([f"Phòng {i}" for i in range(6)], [("Kỳ này", [6, 5, 4, 3, 2, 1]), ("Kỳ trước", [5, 5, 4, 2, 2, 0])])
    assert ds.chart_type == "horizontal_bar"
    png = charts.chart_for(ds, (560, 334), transparent=True)
    assert _png_size(png)[0] == 560 * charts.DPI // 100


def test_so_kieu_viet_nam():
    assert charts._vn(1234) == "1.234" and charts._vn(31.25) == "31,2" and charts._vn(7.0) == "7"


@pytest.mark.parametrize("categories, series", [([], [("S", [])]), (["A", "B"], [("S", [1])]), (["A"], [])])
def test_du_lieu_le_thi_bao_loi(categories, series):
    with pytest.raises(ValueError):
        charts.dataset_chart(categories, series)


def test_chuoi_ve_sau_khong_bi_cat_mep(monkeypatch):
    """Giới hạn trục đặt sau khi vẽ xong mọi chuỗi: chuỗi lớn vẽ sau vẫn nằm trọn trong trục."""
    gioi_han = []
    goc = charts._finish

    def ghi(fig, **kw):
        gioi_han.append(fig.axes[0].get_xlim())
        return goc(fig, **kw)

    monkeypatch.setattr(charts, "_finish", ghi)
    charts.dataset_chart([f"T{i}" for i in range(1, 6)], [("Tuyển mới", [1, 2, 3, 1, 1]),
                                                          ("Cộng dồn", [1, 3, 6, 7, 48])], horizontal=True)
    assert gioi_han[0][1] > 48


def test_truc_ky_giu_thu_tu_thoi_gian_va_cot_dung():
    from app.documents import slide_deck as sd

    q = {"question": "Nhân sự cuối tháng", "rows": [["T1", 26], ["T2", 27], ["T3", 48], ["T4", 41]],
         "columns": [{"name": "thang", "data_type": "nvarchar", "label": "Tháng", "unit": None},
                     {"name": "nhan_su", "data_type": "int", "label": "Nhân sự", "unit": "người"}],
         "source_tables": [], "warnings": []}
    ds, = sd.query_parts({"queries": [q]}).datasets
    assert ds.time_axis and ds.categories == ["T1", "T2", "T3", "T4"] and ds.chart_type == "bar"
    assert "T3 cao nhất" in " ".join(ds.facts)          # nhận xét vẫn xếp theo giá trị
