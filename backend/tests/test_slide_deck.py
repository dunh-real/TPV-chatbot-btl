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
    ("mot hai ba bon nam sau bay", 12, "mot hai ba"),      # cắt ở ranh giới từ, không thêm "…"
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


# ------------------------------------------------- số liệu từ truy vấn động (Text-to-SQL) -- #
def _q(columns: list[tuple[str, str, str, str | None]], rows: list[list], question: str = "Số lượng tài sản theo danh mục",
       warnings: list[str] | None = None) -> dict:
    """Một kết quả `query_erp` đúng dạng `QueryResult.as_dict()` (Decimal đã thành chuỗi)."""
    return {"query_id": "q", "question": question, "row_count": len(rows), "truncated": False,
            "columns": [{"name": n, "data_type": t, "label": l, "unit": u} for n, t, l, u in columns],
            "rows": rows, "schema_version": "s", "policy_version": "p", "source_tables": ["dbo.Asm_Assets"],
            "warnings": warnings or []}


DANH_MUC = _q([("category", "nvarchar", "Danh mục", None), ("quantity", "int", "Số lượng", "cái")],
              [["Máy chủ", 6], ["Máy in", 8], ["Bàn", 0]])


def test_truy_van_dong_nhom_va_so_thanh_bieu_do():
    ds, = sd.query_parts({"queries": [DANH_MUC]}).datasets
    assert ds.key == "bang_1" and ds.title == "Số lượng theo danh mục"
    assert ds.categories == ["Máy in", "Máy chủ", "Bàn"]                 # biểu đồ xếp giảm dần
    assert ds.series == [("Số lượng", [8, 6, 0])]
    assert ds.table_columns == ["Danh mục", "Số lượng (cái)"]
    assert ds.table_rows == [["Máy chủ", "6"], ["Máy in", "8"], ["Bàn", "0"]]   # bảng giữ thứ tự SQL
    assert ds.total_card == ("14 cái", "Tổng số lượng")
    assert ds.facts[:2] == ["Tổng số lượng là 14 cái, trên 3 danh mục.", "Máy in cao nhất với 8 cái, chiếm 57,1%."]
    assert "Có 1/3 danh mục chưa có số liệu số lượng." in ds.facts


def test_cot_ty_le_khong_cong_thanh_tong_va_giu_so_goc():
    q = _q([("dept", "nvarchar", "Đơn vị", None), ("rate", "decimal(5,2)", "Tỷ lệ hoàn thành", "%")],
           [["Phòng A", "45.50"], ["Phòng B", "80.25"]])
    ds, = sd.query_parts({"queries": [q]}).datasets
    assert ds.total_card is None and not any(f.startswith("Tổng") for f in ds.facts)
    assert ds.series == [("Tỷ lệ hoàn thành", [80.25, 45.5])]
    assert ds.table_rows == [["Phòng A", "45.50"], ["Phòng B", "80.25"]]        # đối chiếu số khớp dữ liệu gốc


def test_cot_id_va_nam_khong_phai_so_lieu():
    q = _q([("DepartmentId", "bigint", "Mã phòng", None), ("year", "int", "Năm", None),
            ("total", "int", "Nhân sự", "người")], [[11, 2026, 5], [12, 2026, 7]])
    ds, = sd.query_parts({"queries": [q]}).datasets
    assert [n for n, _ in ds.series] == ["Nhân sự"]
    assert ds.categories == ["12", "11"]            # cột năm giống nhau ở mọi dòng không làm trục nhóm


def test_ket_qua_mot_dong_thanh_the_chi_tieu_chu_thanh_bang_rong_thanh_ghi_chu():
    tong = _q([("total", "int", "Tổng nhân sự", "người"), ("hires", "int", "Tuyển mới", "người")], [[120, 4]])
    danh_sach = _q([("name", "nvarchar", "Họ tên", None), ("dept", "nvarchar", "Đơn vị", None)],
                   [["Nguyễn A", "Phòng 1"]], question="Danh sách trưởng phòng")
    rong = _q([("category", "nvarchar", "Danh mục", None), ("quantity", "int", "Số lượng", None)], [],
              question="Thiết bị hỏng tháng 9", warnings=["Chỉ tính tài sản chưa thanh lý."])
    parts = sd.query_parts({"queries": [tong, danh_sach, rong]})
    assert parts.datasets == []
    assert parts.kpis == [("120 người", "Tổng nhân sự"), ("4 người", "Tuyển mới")]
    assert parts.tables == [("Danh sách trưởng phòng", ["Họ tên", "Đơn vị"], [["Nguyễn A", "Phòng 1"]])]
    assert ("Lưu ý phạm vi dữ liệu", "Chỉ tính tài sản chưa thanh lý.") in parts.notes
    assert any(t == "Không có dữ liệu" and "Thiết bị hỏng tháng 9" in x for t, x in parts.notes)


def test_deck_tu_truy_van_dong_dung_layout_template_va_tu_doi_chieu_duoc():
    tong = _q([("total", "int", "Tổng tài sản", "cái")], [[14]], question="Tổng tài sản")
    data = {"queries": [DANH_MUC, tong]}
    datasets = sd.build_datasets(data, PARAMS)
    plan = sd.build_plan(data, PARAMS, "Đơn vị thử", datasets, None, "09/10/2026")
    roles = [p.role for p in plan]
    assert roles[:3] == ["title", "toc", "scope"]
    assert {"kpi", "chart", "highlights", "table", "notes"} <= set(roles)
    assert {p.layout for p in plan} <= set(TEMPLATE["layouts"])
    scope = next(p for p in plan if p.role == "scope").lists["card_stack"]
    assert scope[1]["card_description"] == "Hệ thống ERP của đơn vị, truy vấn chỉ đọc trên 1 bảng dữ liệu."
    assert scope[3] == {"card_title": "Nội dung báo cáo", "card_description": "Số lượng tài sản theo danh mục"}
    built = [sd.build_slide(p, i, "pid", TEMPLATE) for i, p in enumerate(plan)]
    check = sd.verify_deck({"slides": [s for s, _ in built]}, plan, [r for _, r in built])
    assert check["ok"], check["issues"]
    assert check["charts_ok"] == check["charts_total"] == 1


def test_khong_co_ky_thi_khong_bia_thang():
    plan = sd.build_plan({"queries": [DANH_MUC]}, {}, "Đơn vị thử",
                         sd.build_datasets({"queries": [DANH_MUC]}, {}), None, "09/10/2026")
    assert plan[0].texts["primary_heading"] == "Báo cáo số liệu"
    ky = next(p for p in plan if p.role == "scope").lists["card_stack"][2]
    assert ky["card_description"] == "Theo câu hỏi truy vấn."


# ---------------------------------------------------- chuẩn hoá chữ + template momentum -- #
MOMENTUM = json.loads((Path(__file__).parent / "fixtures" / "presenton_momentum_template.json")
                      .read_text(encoding="utf-8"))


@pytest.mark.parametrize(("text", "kieu", "expected"), [
    ("tổng  27 người , trên 7 phòng ban", "cau", "Tổng 27 người, trên 7 phòng ban."),
    ("Phòng Kinh doanh · KIDO…", "cau", "Phòng Kinh doanh, KIDO."),
    ("Kết luận.", "tieu_de", "Kết luận"),
    ("Tỷ lệ 44,4%", "cau", "Tỷ lệ 44,4%"),
    ("a;b,c..", "cau", "A; b, c."),
])
def test_chuan_hoa_dau_cau(text, kieu, expected):
    assert sd.chuan_hoa(text, kieu) == expected


def test_noi_dung_bao_cao_bo_cau_lenh():
    assert sd.noi_dung_cau_hoi("Tạo slide báo cáo số lượng nhân sự theo phòng ban tháng 10/2026") == \
        "Số lượng nhân sự theo phòng ban tháng 10/2026"
    assert sd.noi_dung_cau_hoi("Lập báo cáo Word tài sản theo danh mục") == "Tài sản theo danh mục"


def _momentum_plan(rows: int = 9) -> list[sd.SlidePlan]:
    q = _q([("tenphongban", "nvarchar", "Phòng ban", None), ("maphongban", "nvarchar", "Mã phòng ban", None),
            ("soluongnhansu", "int", "Số nhân sự", "người")],
           [[f"Phòng {i}", f"P{i:02d}", i] for i in range(1, rows + 1)],
           question="Tạo slide báo cáo số nhân sự theo phòng ban tháng 10/2026",
           warnings=["SQL đã kiểm tra quyền và khả năng chạy."])
    data = {"queries": [q]}
    summary = {"tong_quan": "Tháng 10/2026 có 45 người.", "nhan_xet": [{"bang": "bang_1", "noi_dung": "Phòng 9 đông nhất."}],
               "ket_luan": [{"tieu_de": "Quy mô", "noi_dung": "45 người."}] * 3}
    plan = sd.build_plan(data, PARAMS, "Đơn vị thử", sd.build_datasets(data, PARAMS), summary, "09/10/2026",
                         "momentum")
    return sd.chuyen_template(plan, "momentum")


def test_momentum_dung_layout_cua_template_va_gop_diem_noi_bat_vao_bieu_do():
    plan = _momentum_plan()
    assert {p.layout for p in plan} <= set(MOMENTUM["layouts"])
    assert "highlights" not in [p.role for p in plan]
    chart = next(p for p in plan if p.role == "chart")
    notes = [x["note_description"] for x in chart.lists["section_grid_lists"]]
    assert notes[1] == "Cao nhất: Phòng 9 với 9 người, chiếm 20%."
    tables = [p for p in plan if p.role == "table"]
    assert [len(p.table[1]) for p in tables] == [5, 4]                       # 9 dòng, 8/trang -> chia đều 5 + 4
    assert all(p.table[0] == ["Phòng ban", "Số nhân sự (người)"] for p in tables)   # bỏ cột mã
    assert plan[0].texts["cover_title"] == "Báo cáo\n9/2026"


def test_momentum_dung_ra_ui_vua_o_khong_ten_truong_co_so_trang():
    plan = _momentum_plan()
    built = [sd.build_slide(p, i, "pid", MOMENTUM) for i, p in enumerate(plan)]
    check = sd.verify_deck({"slides": [s for s, _ in built]}, plan, [r for _, r in built])
    assert check["ok"], check["issues"]
    assert check["field_like"] == []

    def texts(e, out):
        if e.get("type") == "text" and e.get("name") and not e.get("decorative"):
            out.append((e["name"], e.get("max_length") or 0, sd._run_text(e)))
        for k in e.get("children") or []:
            texts(k, out)
        if e.get("child"):
            texts(e["child"], out)
    for slide, _ in built:
        out = []
        for c in slide["ui"]["components"]:
            for e in c["elements"]:
                texts(e, out)
        for name, limit, text in out:
            assert not limit or len(text) <= limit, (slide["layout"], name, text)
            assert "…" not in text and "·" not in text, (name, text)
        marker = [t for n, _, t in out if sd._O_TRANG.search(n)]
        assert marker == [f"{slide['index'] + 1:02d}"] * len(marker)
    # Ảnh trang trí của template giữ nguyên, không bị thay bằng ảnh giữ chỗ.
    cover = json.dumps(built[0][0]["ui"])
    assert "/app_data/templates/momentum/static/" in cover and sd.PLACEHOLDER_IMAGE not in cover


def test_bang_nhieu_cot_bo_cot_ma_roi_chia_nhom_cot():
    cols = ["Phòng ban", "Mã phòng ban", "A", "B", "C", "D"]
    rows = [["P1", "M1", "1", "2", "3", "4"]]
    parts = sd._chia_cot(cols, rows, 4)
    assert [c for c, _ in parts] == [["Phòng ban", "A", "B", "C"], ["Phòng ban", "D"]]


def test_theme_cho_bieu_do_da_sac():
    theme = sd.theme_cho("momentum", MOMENTUM["theme"])
    graph = [theme["colors"][f"graph_{i}"] for i in range(4)]
    assert len(set(graph)) == 4 and graph[:2] == sd.PALETTE[:2]
    assert MOMENTUM["theme"]["colors"]["graph_1"] == "#213EBB"            # không sửa theme gốc
