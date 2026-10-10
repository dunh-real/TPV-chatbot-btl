"""Workflow 5, đường dựng thẳng: kế hoạch deck và ui slide do code điền từ layout template.

Fixture `presenton_modern_template.json` là đúng thứ `GET /api/v1/ppt/template/modern` của
Presenton đang chạy trả về (layout + theme), nên các test dựng ui ở đây chạy trên layout thật.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from app.agents.nodes import presentation as pr
from app.documents import slide_deck as sd
from app.documents.verify import collect_known_numbers

TEMPLATE = json.loads((Path(__file__).parent / "fixtures" / "presenton_modern_template.json")
                      .read_text(encoding="utf-8"))
PARAMS = {"ky": "2026-09", "thang": 9, "nam": 2026, "compare_to": "2026-08"}


def _personnel(n: int = 3) -> dict:
    rows = [{"ma_nhom": f"{i:05d}", "ten_nhom": f"Phòng {i}", "nhan_su": 10 + i, "nhan_su_ky_truoc": 9 + i,
             "tuyen_moi": 1, "nghi_viec": 0} for i in range(1, n + 1)]
    total = sum(r["nhan_su"] for r in rows)
    return {
        "metrics": {"total_personnel": {"value": total, "prev": total - n, "delta": n, "delta_pct": 5.0,
                                        "share_pct": None},
                    "new_hires": {"value": n, "prev": None, "delta": None, "delta_pct": None, "share_pct": 2.5}},
        "scope": {"nguon": "Hrm_EmployeeProfile", "ghi_chu": "Số liệu kỳ dựng lại từ ERP.",
                  "khong_co_chi_tieu": ["có mặt", "vắng"], "units_requested": n},
        "breakdown": rows,
        "breakdown_columns": [{"key": "ten_nhom", "label": "Đơn vị"}, {"key": "nhan_su", "label": "Nhân sự"},
                              {"key": "nhan_su_ky_truoc", "label": "Kỳ trước"},
                              {"key": "tuyen_moi", "label": "Tuyển mới"}, {"key": "nghi_viec", "label": "Nghỉ việc"}],
        "dimension": "phong_ban", "consistency": [],
    }


def _equipment() -> dict:
    """Giống dữ liệu ERP thật: thiết bị chưa gán đơn vị đã nằm sẵn trong breakdown."""
    rows = [{"ten_nhom": "Phòng A", "ten_thiet_bi": "Bàn", "so_luong": 4, "tinh_trang": "Trạng thái 0"},
            {"ten_nhom": "Phòng A", "ten_thiet_bi": "Ghế", "so_luong": 6, "tinh_trang": "Trạng thái 0"},
            {"ten_nhom": "(chưa gán phòng ban)", "ten_thiet_bi": "Máy in", "so_luong": 30,
             "tinh_trang": "Trạng thái 0"}]
    return {
        "metrics": {"total_equipment": {"value": 40, "prev": 38, "delta": 2, "delta_pct": 5.3, "share_pct": None},
                    "equipment_types": {"value": 3, "prev": None, "delta": None, "delta_pct": None,
                                        "share_pct": None}},
        "scope": {"nguon": "Asm_Assets", "chua_gan_don_vi": 30, "units_requested": 2},
        "breakdown": rows,
        "breakdown_columns": [{"key": "ten_nhom", "label": "Đơn vị"}, {"key": "ten_thiet_bi", "label": "Thiết bị"},
                              {"key": "so_luong", "label": "Số lượng"}, {"key": "tinh_trang", "label": "Tình trạng"}],
        "dimension": "phong_ban",
        "consistency": [{"ma_don_vi": "", "message": "30/40 thiết bị chưa gán phòng ban."}],
    }


def _data(n: int = 3) -> dict:
    return {"personnel": _personnel(n), "equipment": _equipment(),
            "reporting": {"units_total": 3, "units_reported": 1, "reported": [],
                          "missing": [{"ma_don_vi": "00002", "ten_don_vi": "Phòng 2"},
                                      {"ma_don_vi": "00003", "ten_don_vi": "Phòng 3"}]}}


def _plan(data: dict, summary: dict | None = None) -> list[sd.SlidePlan]:
    return sd.build_plan(data, PARAMS, "Đơn vị thử", sd.build_datasets(data, PARAMS), summary, "09/10/2026")


# --------------------------------------------------------------- bộ dữ liệu -- #
def test_bieu_do_thiet_bi_khong_dem_trung_thiet_bi_chua_gan():
    """Lỗi đã gặp: cộng thêm `chua_gan_don_vi` vào dòng "(chưa gán...)" có sẵn -> hai cột 380."""
    ds = {d.key: d for d in sd.build_datasets(_data(), PARAMS)}["equipment"]
    assert sum(ds.series[0][1]) == 40
    assert ds.categories.count("(chưa gán phòng ban)") == 1
    assert "Chưa gán đơn vị" not in ds.categories


def test_nhom_chua_gan_khong_duoc_xep_hang_nhieu_nhat():
    ds = {d.key: d for d in sd.build_datasets(_data(), PARAMS)}["equipment"]
    nhieu_nhat = dict(ds.highlights)["Nhiều nhất"]
    assert nhieu_nhat.startswith("Phòng A: 10")


def test_bieu_do_nhan_su_so_ky_nay_voi_ky_truoc():
    ds = sd.build_datasets(_data(), PARAMS)[0]
    assert [name for name, _ in ds.series] == ["Tháng 9/2026", "Tháng 8/2026"]
    assert ds.categories[0] == "Phòng 3"  # xếp giảm dần theo kỳ này


def test_bieu_do_qua_nhieu_cot_thi_gop_phan_con_lai():
    data = {"personnel": _personnel(20)}
    ds = sd.build_datasets(data, PARAMS)[0]
    assert len(ds.categories) == 20  # nhân sự không gộp - mọi đơn vị đều lên biểu đồ
    eq = _equipment()
    eq["breakdown"] = [{"ten_nhom": f"Loại {i}", "so_luong": i} for i in range(1, 21)]
    ds = sd.build_datasets({"equipment": eq}, PARAMS)[0]
    assert len(ds.categories) == sd.CHART_MAX_BARS
    assert ds.grouped_others == 20 - sd.CHART_MAX_BARS + 1
    assert sum(ds.series[0][1]) == sum(range(1, 21))


# ---------------------------------------------------------------- kế hoạch -- #
def test_bang_chi_tiet_cat_trang_du_dong_va_tieu_de_khong_bi_cat():
    data = _data(23)
    plan = _plan(data)
    pages = [p for p in plan if p.role == "table" and "nhân sự" in p.texts["main_heading"]]
    assert len(pages) == 3
    assert sum(len(p.table[1]) for p in pages) == 23
    for i, p in enumerate(pages, start=1):
        slide, rep = sd.build_slide(p, 0, "pid", TEMPLATE)
        assert f"({i}/3)" in rep.written[0]  # ô tiêu đề đã giãn: không cắt mất số trang


def test_bang_rong_thi_khong_co_slide_bang_rong():
    data = _data()
    data["equipment"]["breakdown"] = []
    plan = _plan(data)
    assert not [p for p in plan if p.role == "table" and "thiết bị" in p.texts["main_heading"]]


def test_slide_ket_luan_chi_co_khi_qwen_viet_duoc():
    assert "conclusion" not in [p.role for p in _plan(_data())]
    summary = {"tong_quan": "Tổng quan.", "nhan_xet": [],
               "ket_luan": [{"tieu_de": "A", "noi_dung": "a"}, {"tieu_de": "B", "noi_dung": "b"},
                            {"tieu_de": "C", "noi_dung": "c"}]}
    assert _plan(_data(), summary)[-1].role == "conclusion"


def test_moi_slide_dung_duoc_tu_layout_that():
    plan = _plan(_data())
    for i, p in enumerate(plan):
        slide, rep = sd.build_slide(p, i, "pid", TEMPLATE)
        assert slide["layout"] == p.layout
        assert not rep.unmapped, (p.role, rep.unmapped)


def test_so_cua_code_gom_ty_le_va_so_trang():
    data = _data(23)
    known = sd.code_numbers(_plan(data))
    assert {"1", "2", "3"} <= known
    # Tỷ lệ "chiếm x,y%" do code tính, không có trong dữ liệu gốc: bước đối chiếu phải biết.
    ds = sd.build_datasets(data, PARAMS)[0]
    pct = dict(ds.highlights)["Đông nhất"].split("chiếm ")[1].rstrip("%.")
    assert collect_known_numbers([pct]) <= known
    assert not collect_known_numbers([pct]) <= collect_known_numbers(data)


def test_ui_bang_chua_dung_so_lieu():
    plan = _plan(_data())
    p = next(p for p in plan if p.role == "table")
    slide, _ = sd.build_slide(p, 0, "pid", TEMPLATE)
    texts = sd.ui_texts(slide["ui"])
    assert "Phòng 3" in texts and "13" in texts
    assert not any(e.get("type") == "chart" for c in slide["ui"]["components"] for e in c["elements"])


# ---------------------------------------------------------------- đối chiếu -- #
def _built(plan):
    built = [sd.build_slide(p, i, "pid", TEMPLATE) for i, p in enumerate(plan)]
    return {"slides": [s for s, _ in built]}, [r for _, r in built]


def test_doi_chieu_deck_dung_thi_dat():
    plan = _plan(_data())
    deck, reports = _built(plan)
    check = sd.verify_deck(deck, plan, reports)
    assert check["ok"], check["issues"]
    assert check["charts_ok"] == check["charts_total"] == 2


def test_doi_chieu_bat_bieu_do_bi_doi_so():
    plan = _plan(_data())
    deck, reports = _built(plan)
    deck = copy.deepcopy(deck)
    chart_slide = next(s for s, p in zip(deck["slides"], plan) if p.role == "chart")
    for c in chart_slide["ui"]["components"]:
        for e in c["elements"]:
            if e.get("type") == "chart":
                e["series"][0]["values"][0] += 1
    check = sd.verify_deck(deck, plan, reports)
    assert not check["ok"]
    assert any(i["type"] == "chart_mismatch" for i in check["issues"])


def test_doi_chieu_bat_chu_la_va_chu_han():
    plan = _plan(_data())
    deck, reports = _built(plan)
    deck = copy.deepcopy(deck)
    el = next(e for c in deck["slides"][2]["ui"]["components"] for e in c["elements"]
              if e.get("type") == "text" and e.get("name"))
    el["runs"][0]["text"] = "Phạm vi检查"
    types = {i["type"] for i in sd.verify_deck(deck, plan, reports)["issues"]}
    assert {"foreign_text", "foreign_script"} <= types


# ------------------------------------------------------------- Qwen tóm tắt -- #
def test_tom_tat_bi_bat_so_bia_chu_han_va_thieu_bang():
    data = _data()
    datasets = sd.build_datasets(data, PARAMS)
    known = collect_known_numbers(data) | sd.code_numbers(_plan(data))
    out = {"tong_quan": "Tổng nhân sự 999 người.",
           "nhan_xet": [{"bang": "personnel", "noi_dung": "Tăng检查."}],
           "ket_luan": [{"tieu_de": "A", "noi_dung": "a"}] * 3}
    problems = " | ".join(pr._summary_problems(out, datasets, known))
    assert "999" in problems and "检" in problems and "equipment" in problems


def test_tom_tat_dung_so_lieu_thi_khong_loi():
    data = _data()
    datasets = sd.build_datasets(data, PARAMS)
    known = collect_known_numbers(data) | sd.code_numbers(_plan(data))
    out = {"tong_quan": "Tổng nhân sự 36 người, tăng 3 so với kỳ trước.",
           "nhan_xet": [{"bang": "personnel", "noi_dung": "Phòng 3 đông nhất với 13 người."},
                        {"bang": "equipment", "noi_dung": "Tổng 40 thiết bị, 30 chưa gán phòng ban."}],
           "ket_luan": [{"tieu_de": "Nhân sự", "noi_dung": "36 người."}] * 3}
    assert pr._summary_problems(out, datasets, known) == []


@pytest.mark.parametrize(("text", "limit", "expected"), [
    ("Ngắn.", 30, "Ngắn."),
    ("Câu một dài. Câu hai rất dài không vừa", 20, "Câu một dài."),
    ("mot hai ba bon nam sau bay", 12, "mot hai ba…"),
])
def test_fit_cat_o_ranh_gioi_cau_tu(text, limit, expected):
    assert sd.fit(text, limit) == expected


def test_ky_khong_co_du_lieu_thi_canh_bao_dung_dau():
    data = _data()
    data["equipment"]["scope"]["ghi_chu"] = ("Kỳ 2020-01 chưa có dữ liệu thiết bị trong ERP. Số liệu dưới đây "
                                             "là HIỆN TRẠNG tại 09/10/2026.")
    ds = {d.key: d for d in sd.build_datasets(data, PARAMS)}["equipment"]
    assert ds.facts[0].startswith("Lưu ý: Kỳ 2020-01 chưa có dữ liệu thiết bị")
    assert sd.note_cards(data)[0] == ("Số liệu không theo kỳ",
                                      "Kỳ 2020-01 chưa có dữ liệu thiết bị trong ERP. Số trên slide là hiện trạng.")


def test_khong_co_don_vi_nao_thi_bao_loi_khong_dung_deck_rong():
    rong = {"personnel": {**_personnel(1), "breakdown": []},
            "reporting": {"units_total": 0, "units_reported": 0, "missing": [], "reported": []}}
    assert pr._khong_co_gi_de_trinh_bay(rong)
    assert not pr._khong_co_gi_de_trinh_bay(_data())
