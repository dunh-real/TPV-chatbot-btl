"""Nhãn tiếng Việt + vai trò cột cho kết quả Text-to-SQL: Qwen đặt dựa trên dấu hiệu dữ liệu, code kiểm.

Không từ điển, không sửa Text-to-SQL: chỉ đọc `QueryResult` có sẵn.
"""

from __future__ import annotations

import pytest

from app.documents import nhan_cot as nc
from app.documents import slide_deck as sd


def _result(cols, rows, question="Số nhân sự theo phòng ban"):
    return {"question": question, "rows": rows,
            "columns": [{"name": n, "data_type": t, "label": lb, "unit": None} for n, t, lb in cols]}


def _c(ten, nhan, vai_tro, don_vi="", loai_so=None):
    if vai_tro == "so" and loai_so is None:
        loai_so = "phat_sinh"
    return {"ten": ten, "nhan": nhan, "don_vi": don_vi, "vai_tro": vai_tro, "loai_so": loai_so}


class FakeLLM:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    async def chat_json(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("vLLM tắt")
        return self.answer


class SeqLLM:
    """Trả lần lượt từng câu trả lời; ghi lại prompt để kiểm phản hồi lỗi ở lần hỏi lại."""

    def __init__(self, *answers):
        self.answers, self.prompts = list(answers), []

    async def chat_json(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return self.answers.pop(0)


@pytest.fixture
def llm(monkeypatch):
    def use(fake):
        import app.services.llm as llm_module
        monkeypatch.setattr(llm_module, "get_llm", lambda: fake)
        return fake
    return use


PHONG = ([("tenphongban", "nvarchar", None), ("maphongban", "nvarchar", None), ("soluongnhansu", "int", None)],
         [["Phòng A", "PA", 3], ["Phòng B", "PB", 5]])


@pytest.mark.parametrize(("text", "expected"), [
    ("Tổng soluongnhansu: 27", True), ("ten_phong_ban", True), ("employeeCount", True),
    ("Phòng Kinh doanh có 12 người.", False), ("KIDO", False), ("44,4%", False),
])
def test_nhan_dien_ten_truong(text, expected):
    assert nc.la_ten_truong(text) is expected


# ---------------------------------------------------------- dấu hiệu dữ liệu -- #
def test_dau_hieu_cong_don_ky_ty_le_am():
    cols = [{"name": n} for n in ("thang", "tuyen_moi", "cong_don", "ty_le", "con_lai")]
    rows = [["T1", 10, 10, 12.5, 3], ["T2", 1, 11, 40.0, -1], ["T3", 11, 22, 7.25, 2]]
    th = nc.tin_hieu_du_lieu(cols, rows)
    assert any("kỳ thời gian" in t for t in th[0])
    assert any("cộng dồn" in t and "tuyen_moi" in t for t in th[2]) and not any("cộng dồn" in t for t in th[1])
    assert any("tỷ lệ" in t for t in th[3]) and any("âm" in t for t in th[4])


# --------------------------------------------------------- Qwen + kiểm tra -- #
async def test_qwen_dat_nhan_va_vai_tro_hop_le_thi_dung(llm):
    fake = llm(FakeLLM({"cot": [_c("tenphongban", "Phòng ban", "nhom"), _c("maphongban", "Mã phòng ban", "nhom"),
                                _c("soluongnhansu", "Số nhân sự", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result(*PHONG))
    assert [(c["label"], c["unit"], c["role"]) for c in out["columns"]] == \
        [("Phòng ban", "", "nhom"), ("Mã phòng ban", "", "nhom"), ("Số nhân sự", "người", "so")]
    assert out["columns"][2]["cong_duoc"] is True and fake.calls == 1


async def test_dau_hieu_cong_don_duoc_dua_vao_prompt_va_thang_cong_duoc(llm):
    cols = [("thang", "nvarchar", None), ("tuyen_moi", "int", None), ("cong_don", "int", None)]
    rows = [["T1", 10, 10], ["T2", 1, 11], ["T3", 11, 22]]
    fake = llm(SeqLLM({"cot": [_c("thang", "Tháng", "ky"), _c("tuyen_moi", "Tuyển mới", "so", "người"),
                               _c("cong_don", "Cộng dồn tuyển mới", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result(cols, rows, "Tuyển mới theo tháng"))
    # Dấu hiệu nằm trong JSON của prompt (dấu nháy được escape).
    assert 'bằng tổng cộng dồn theo dòng của cột \\"tuyen_moi\\"' in fake.prompts[0]
    assert out["columns"][2]["cong_duoc"] is False      # dữ liệu nói là cộng dồn: không cộng thành tổng
    assert out["columns"][1]["cong_duoc"] is True


async def test_qwen_tra_ten_truong_thi_hoi_lai_kem_ly_do(llm):
    cols, rows = [("soluongnhansu", "int", None)], [[3], [5]]
    fake = llm(SeqLLM({"cot": [_c("soluongnhansu", "soluong_nhansu", "so")]},
                      {"cot": [_c("soluongnhansu", "Số nhân sự", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result(cols, rows))
    assert out["columns"][0]["label"] == "Số nhân sự" and out["columns"][0]["unit"] == "người"
    assert len(fake.prompts) == 2 and "soluong_nhansu" in fake.prompts[1]   # lần 2 biết lần 1 sai ở đâu


async def test_vai_tro_trai_du_lieu_thi_hoi_lai(llm):
    """Cột kỳ (T1, T2) mà Qwen gọi là "nhom", cột chữ mà gọi là "so": code biết chắc nên trả lại."""
    cols, rows = [("thang", "nvarchar", None), ("phong", "nvarchar", None)], [["T1", "A"], ["T2", "B"]]
    fake = llm(SeqLLM({"cot": [_c("thang", "Tháng", "nhom"), _c("phong", "Phòng ban", "so")]},
                      {"cot": [_c("thang", "Tháng", "ky"), _c("phong", "Phòng ban", "nhom")]}))
    out = await nc.gan_nhan_cot(_result(cols, rows))
    assert [c["role"] for c in out["columns"]] == ["ky", "nhom"] and len(fake.prompts) == 2
    assert 'giá trị là kỳ thời gian, vai_tro phải là "ky"' in fake.prompts[1]
    assert 'vai_tro "so" nhưng giá trị không phải số' in fake.prompts[1]


async def test_qwen_dat_trung_nhan_thi_hoi_lai(llm):
    cols, rows = [("cuoi_thang_8", "int", None), ("cuoi_thang_9", "int", None)], [[16, 16], [15, 15]]
    fake = llm(SeqLLM({"cot": [_c("cuoi_thang_8", "Nhân sự", "so", "người"), _c("cuoi_thang_9", "Nhân sự", "so", "người")]},
                      {"cot": [_c("cuoi_thang_9", "Nhân sự cuối tháng 9", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result(cols, rows))
    assert [c["label"] for c in out["columns"]] == ["Nhân sự", "Nhân sự cuối tháng 9"]
    assert len(fake.prompts) == 2


async def test_khong_co_qwen_thi_nhan_trung_tinh_khong_anh_xa(llm):
    """Không có từ điển: Qwen hỏng thì "Cột n" - không đoán "soluong" thành "Số lượng"."""
    llm(FakeLLM(fail=True))
    out = await nc.gan_nhan_cot(_result([("tenphongban", "nvarchar", None), ("soluong", "int", "soluong")],
                                        [["A", 1]]))
    assert [c["label"] for c in out["columns"]] == ["Cột 1", "Cột 2"]
    assert not hasattr(nc, "nhan_tu_ten") and not hasattr(nc, "_AM_TIET")


async def test_cot_da_du_nhan_va_vai_tro_thi_khong_goi_qwen(llm):
    fake = llm(FakeLLM(fail=True))
    r = _result([("department", "nvarchar", "Phòng ban"), ("cnt", "int", "Số nhân sự")], [["A", 1]])
    r["columns"][0]["role"], r["columns"][1]["role"] = "nhom", "so"
    out = await nc.gan_nhan_cot(r)
    assert fake.calls == 0 and [c["label"] for c in out["columns"]] == ["Phòng ban", "Số nhân sự"]


async def test_nhan_theo_noi_dung_rieng_cua_bang_khong_theo_yeu_cau_chung(llm):
    """Yêu cầu chung nhắc "số nhân sự", nhưng bảng này là thiết bị: prompt phải lấy bảng làm căn cứ."""
    fake = llm(SeqLLM({"cot": [_c("so_luong", "Số lượng thiết bị", "so", "thiết bị")]}))
    r = _result([("so_luong", "int", None)], [[3]], "Phân bổ trang thiết bị theo phòng ban")
    await nc.gan_nhan_cot(r, "Phân bổ thiết bị theo phòng ban, đặt cạnh số nhân sự của từng phòng")
    p = fake.prompts[0]
    assert p.index("Nội dung của bảng này: Phân bổ trang thiết bị theo phòng ban") < p.index("Yêu cầu chung")


# --------------------------------------------- slide dùng vai trò đã khai -- #
def test_slide_dung_vai_tro_da_khai_thay_regex():
    """Cột mã số nguyên ("ma_gioi_tinh") regex sẽ coi là số; vai trò khai "nhom" thì là trục nhóm.
    Trục kỳ đặt tên lạ ("dot") vẫn giữ thứ tự thời gian nhờ vai trò "ky"."""
    q = {"question": "Nhân sự theo đợt", "source_tables": [], "warnings": [],
         "columns": [{"name": "dot", "data_type": "nvarchar", "label": "Đợt báo cáo", "unit": "", "role": "ky"},
                     {"name": "ma_gioi_tinh", "data_type": "int", "label": "Mã giới tính", "unit": "", "role": "nhom"},
                     {"name": "so_nhan_su", "data_type": "int", "label": "Số nhân sự", "unit": "người",
                      "role": "so", "cong_duoc": True}],
         "rows": [["Đợt 1", 0, 5], ["Đợt 2", 1, 9], ["Đợt 3", 0, 7]]}
    ds, = sd.query_parts({"queries": [q]}).datasets
    assert [n for n, _ in ds.series] == ["Số nhân sự"]          # mã giới tính không thành chuỗi số
    assert ds.time_axis and ds.categories == ["Đợt 1", "Đợt 2", "Đợt 3"]


@pytest.mark.parametrize(("kind", "co_truc_ky", "expected"), [
    ("phat_sinh", False, True), ("phat_sinh", True, True),
    ("ton", False, True),      # nhân sự cuối tháng 8 theo phòng ban: cộng ra tổng của đơn vị
    ("ton", True, False),      # nhân sự cuối tháng qua các tháng: cộng vô nghĩa
    ("ty_le", False, False), ("trung_binh", False, False), ("cong_don", True, False),
    ("chi_tieu_khac_loai", False, False),
])
def test_cong_duoc_do_code_quyet_theo_loai_so_va_truc_ky(kind, co_truc_ky, expected):
    assert nc.cong_duoc(kind, [], co_truc_ky) is expected


def test_dau_hieu_du_lieu_thang_phan_loai_cua_model():
    assert nc.cong_duoc("phat_sinh", ['bằng tổng cộng dồn theo dòng của cột "x"'], False) is False


async def test_so_ton_theo_ky_khong_cong_theo_phong_ban_thi_cong(llm):
    ky = _result([("thang", "nvarchar", None), ("nhan_su", "int", None)], [["T1", 26], ["T2", 27]])
    llm(FakeLLM({"cot": [_c("thang", "Tháng", "ky"), _c("nhan_su", "Nhân sự cuối tháng", "so", "người", "ton")]}))
    assert (await nc.gan_nhan_cot(ky))["columns"][1]["cong_duoc"] is False
    pb = _result([("phong_ban", "nvarchar", None), ("cuoi_thang_8", "int", None)], [["A", 16], ["B", 15]])
    llm(FakeLLM({"cot": [_c("phong_ban", "Phòng ban", "nhom"),
                         _c("cuoi_thang_8", "Nhân sự cuối tháng 8", "so", "người", "ton")]}))
    assert (await nc.gan_nhan_cot(pb))["columns"][1]["cong_duoc"] is True


async def test_cot_so_thieu_loai_so_thi_hoi_lai(llm):
    fake = llm(SeqLLM({"cot": [{"ten": "so_luong", "nhan": "Số lượng", "don_vi": "", "vai_tro": "so", "loai_so": "abc"}]},
                      {"cot": [_c("so_luong", "Số lượng", "so", "thiết bị", "phat_sinh")]}))
    out = await nc.gan_nhan_cot(_result([("so_luong", "int", None)], [[3]]))
    assert out["columns"][0]["cong_duoc"] is True and len(fake.prompts) == 2 and "loai_so" in fake.prompts[1]


def test_alias_da_la_tieng_viet_thi_duoc_dung_lai_lam_nhan():
    """Text-to-SQL có thể đặt alias "[Số nhân sự]": trùng tên cột là đúng, không bắt đổi thành "Cột n"."""
    assert nc.nhan_hop_le("Số nhân sự", "Số nhân sự") and nc.nhan_hop_le("Tình trạng", "Tình trạng")
    assert not nc.nhan_hop_le("soluong", "soluong") and not nc.nhan_hop_le("so_luong", "so_luong")


async def test_alias_tieng_viet_qua_qwen_giu_nguyen(llm):
    llm(FakeLLM({"cot": [_c("Tình trạng", "Tình trạng", "nhom"), _c("Số nhân sự", "Số nhân sự", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result([("Tình trạng", "nvarchar", None), ("Số nhân sự", "int", None)], [["A", 3]]))
    assert [(c["label"], c["role"]) for c in out["columns"]] == [("Tình trạng", "nhom"), ("Số nhân sự", "so")]


def test_dau_hieu_phan_tram_va_ngay_cu_the():
    cols = [{"name": n} for n in ("ty_le", "ngay", "thang")]
    rows = [["12.5%", "2026-09-15T00:00:00", "2026-09"], ["40%", "2026-08-01", "2026-08"]]
    th = nc.tin_hieu_du_lieu(cols, rows)
    assert any("%" in t for t in th[0]) and nc._la_so(["12.5%", "40%"])
    assert any("ngày/thời điểm cụ thể" in t for t in th[1]) and not any("kỳ thời gian" in t for t in th[1])
    assert any("kỳ thời gian" in t for t in th[2])
    assert nc.cong_duoc("phat_sinh", th[0], False) is False      # tỷ lệ % không cộng thành tổng


async def test_ngay_cu_the_khi_da_nhom_theo_cot_khac_khong_lam_truc_ky(llm):
    cols = [("phong_ban", "nvarchar", None), ("ngay_vao_gan_nhat", "datetime", None), ("so_nhan_su", "int", None)]
    rows = [["A", "2026-09-15T00:00:00", 3], ["B", "2026-08-01T00:00:00", 5]]
    fake = llm(SeqLLM({"cot": [_c("phong_ban", "Phòng ban", "nhom"), _c("ngay_vao_gan_nhat", "Ngày vào gần nhất", "ky"),
                               _c("so_nhan_su", "Số nhân sự", "so", "người", "ton")]},
                      {"cot": [_c("ngay_vao_gan_nhat", "Ngày vào gần nhất", "nhom")]}))
    out = await nc.gan_nhan_cot(_result(cols, rows))
    assert [c["role"] for c in out["columns"]] == ["nhom", "nhom", "so"] and len(fake.prompts) == 2
    assert out["columns"][2]["cong_duoc"] is True                 # không có trục kỳ: số tồn theo phòng ban cộng được


async def test_bo_chu_ten_o_dau_nhan_cot_nhom(llm):
    llm(FakeLLM({"cot": [_c("tenant_name", "Tên đơn vị thuê bao", "nhom"), _c("n", "Số nhân sự", "so", "người")]}))
    out = await nc.gan_nhan_cot(_result([("tenant_name", "nvarchar", None), ("n", "int", None)], [["TPV", 3]]))
    assert out["columns"][0]["label"] == "Đơn vị thuê bao"


def test_nam_va_thang_ghep_thanh_mot_truc_ky():
    """Hai cột kỳ (năm, tháng): trục là "T11/2025", không phải "2025, 2025, 2026" - nhận xét không gán số
    của một tháng cho cả năm."""
    q = {"question": "Tuyển mới theo năm và tháng", "source_tables": [], "warnings": [],
         "columns": [{"name": "nam", "data_type": "int", "label": "Năm", "unit": "", "role": "ky"},
                     {"name": "thang", "data_type": "int", "label": "Tháng", "unit": "", "role": "ky"},
                     {"name": "tuyen_moi", "data_type": "int", "label": "Số tuyển mới", "unit": "người",
                      "role": "so", "cong_duoc": True}],
         "rows": [[2025, 11, 14], [2025, 12, 1], [2026, 1, 10]]}
    ds, = sd.query_parts({"queries": [q]}).datasets
    assert ds.categories == ["T11/2025", "T12/2025", "T1/2026"] and ds.time_axis
    assert "theo tháng" in ds.title and not any("Năm 2025" in f for f in ds.facts)


def test_ngay_iso_hien_kieu_viet_nam():
    assert sd.gia_tri_chu("2025-11-01T00:00:00") == "01/11/2025"
    assert sd.gia_tri_chu("2025-11-01T08:30:00") == "01/11/2025 08:30"
    assert sd.gia_tri_chu("Phòng A") == "Phòng A"
    from app.documents.query_result import cell
    assert cell("2026-04-02T00:00:00") == "02/04/2026"
