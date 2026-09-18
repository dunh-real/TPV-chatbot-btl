"""Tham chiếu nguồn: đánh số, lọc theo marker, và gỡ marker trước khi vào file."""

from __future__ import annotations

from app.agents import references as R
from app.documents.parser import Block


def test_danh_so_lien_tuc_tu_mot():
    refs = R.number([R.make("block", f"P0{i}", f"nội dung {i}") for i in range(3)])

    assert [r["id"] for r in refs] == [1, 2, 3]


def test_gop_nhieu_nhom_thi_danh_so_lien_tuc():
    a = R.from_data({"quan_so": {"tong": 113}})
    b = R.from_regulations([{"doc_title": "NĐ 30", "section": "Điều 8", "text": "..."}])

    gop = R.merge(a, b)

    assert [r["id"] for r in gop] == [1, 2]
    assert gop[-1]["kind"] == "quy_dinh"


def test_render_dung_dang_model_doc_duoc():
    refs = R.number([R.make("block", "P07", "Nhân sự chính thức: 24 người")])

    assert R.render(refs) == "[1] P07\nNhân sự chính thức: 24 người"
    assert R.render([]) == "(không có)"


def test_chi_giu_nguon_thuc_su_duoc_trich():
    refs = R.number([R.make("block", f"P{i}", f"đoạn {i}") for i in range(3)])

    giu = R.used("Theo báo cáo [1] và [3] thì...", refs)

    assert [r["id"] for r in giu] == [1, 3]


def test_quen_danh_so_thi_tra_ve_tat_ca():
    """Thà thừa nguồn còn hơn câu trả lời trông như không có căn cứ nào."""
    refs = R.number([R.make("block", "P00", "đoạn")])

    assert R.used("Câu trả lời không có marker nào.", refs) == refs


def test_go_marker_truoc_khi_do_vao_file():
    """Văn bản hành chính không được phép có "[1]" nằm giữa câu."""
    assert R.strip_markers("Quân số 113 người [1], tăng 3 [2][3].") == \
        "Quân số 113 người, tăng 3."


def test_go_marker_khong_dung_den_so_that():
    assert R.strip_markers("Tháng 8/2026 có 113 người") == "Tháng 8/2026 có 113 người"


def test_snippet_bi_cat_nhung_khong_cat_giua_tu():
    dai = " ".join(["từ"] * 400)
    ref = R.make("block", "P00", dai)

    assert len(ref["snippet"]) <= R.SNIPPET_LIMIT + 1
    assert ref["snippet"].endswith("…")


def test_tu_khoi_van_ban_bo_khoi_rong():
    blocks = [Block(id="P00", text="có chữ"), Block(id="P01", text="   ")]

    refs = R.from_blocks(blocks)

    assert len(refs) == 1 and refs[0]["locator"] == {"block_id": "P00"}


def test_tu_tool_log_giu_dung_so_da_phat():
    """Model đã nhìn thấy [2] trong hội thoại; đánh số lại là lệch hết trích dẫn."""
    log = [{"ref_id": 2, "tool": "get_personnel", "arguments": {"ky": "2026-08"},
            "preview": "{'tong': 113}", "ok": True}]

    refs = R.from_tool_log(log)

    assert refs[0]["id"] == 2
    assert refs[0]["locator"]["tool"] == "get_personnel"
    assert "ky" in refs[0]["label"]


def test_tu_du_lieu_bo_nhanh_rong_va_co_dau_nhat_quan():
    refs = R.from_data({"personnel": {"tong": 113}, "equipment": {},
                        "personnel_consistent": True})

    assert [r["label"] for r in refs] == ["personnel"]
    assert refs[0]["locator"] == {"key": "personnel"}


def test_danh_so_lai_theo_thu_tu_xuat_hien():
    """Số gốc là địa chỉ nguồn nên nhảy cóc; đọc dạng chữ thì trông như hỏng."""
    refs = R.number([R.make("block", f"P{i:02d}", f"đoạn {i}", {"block_id": f"P{i:02d}"})
                     for i in range(25)])

    texts, dung = R.renumber(["Tóm tắt [5][16][1].", "Nhiệm vụ [16][22]."], refs)

    assert texts == ["Tóm tắt [1][2][3].", "Nhiệm vụ [2][4]."]
    assert [r["id"] for r in dung] == [1, 2, 3, 4]


def test_danh_so_lai_dung_chung_mot_day():
    """[2] ở đoạn này và [2] ở đoạn kia phải trỏ cùng một nguồn."""
    refs = R.number([R.make("block", f"P{i:02d}", "x", {"block_id": f"P{i:02d}"})
                     for i in range(25)])

    _, dung = R.renumber(["a [16]", "b [16]"], refs)

    assert len(dung) == 1 and dung[0]["locator"]["block_id"] == "P15"


def test_danh_so_lai_giu_dia_chi_that_trong_locator():
    refs = R.number([R.make("block", "P20", "x", {"block_id": "P20"})] * 1, start=21)

    texts, dung = R.renumber(["chỉ có [21]"], refs)

    assert texts == ["chỉ có [1]"]
    assert dung[0]["id"] == 1 and dung[0]["locator"]["block_id"] == "P20"


def test_marker_tro_nguon_khong_ton_tai_bi_go():
    refs = R.number([R.make("block", "P00", "x", {"block_id": "P00"})])

    texts, dung = R.renumber(["thật [1] và bịa [9]"], refs)

    assert texts == ["thật [1] và bịa "]
    assert len(dung) == 1


def test_khong_co_marker_thi_khong_doi_gi():
    refs = R.number([R.make("block", "P00", "x", {"block_id": "P00"})])

    texts, dung = R.renumber(["không trích dẫn gì"], refs)

    assert texts == ["không trích dẫn gì"] and dung == []
