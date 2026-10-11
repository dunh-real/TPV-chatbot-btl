"""Nhãn tiếng Việt cho cột kết quả Text-to-SQL: không để tên trường CSDL lên Word/slide."""

from __future__ import annotations

import pytest

from app.documents import nhan_cot as nc


def _result(*cols):
    return {"question": "Số nhân sự theo phòng ban", "rows": [["Phòng A", "PA", 3], ["Phòng B", "PB", 5]],
            "columns": [{"name": n, "data_type": t, "label": l, "unit": None} for n, t, l in cols]}


class FakeLLM:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, 0

    async def chat_json(self, messages, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("vLLM tắt")
        return self.answer


@pytest.fixture
def llm(monkeypatch):
    def use(fake):
        import app.services.llm as llm_module
        monkeypatch.setattr(llm_module, "get_llm", lambda: fake)
        return fake
    return use


@pytest.mark.parametrize(("text", "expected"), [
    ("Tổng soluongnhansu: 27", True), ("ten_phong_ban", True), ("employeeCount", True),
    ("Phòng Kinh doanh có 12 người.", False), ("KIDO", False), ("44,4%", False),
])
def test_nhan_dien_ten_truong(text, expected):
    assert nc.la_ten_truong(text) is expected


@pytest.mark.parametrize(("name", "expected"), [
    ("soluongnhansu", "Số lượng nhân sự"), ("maphongban", "Mã phòng ban"),
    ("employee_count", "Số lượng nhân viên"), ("SoLuongTaiSan", "Số lượng tài sản"), ("xq7zz", ""),
])
def test_nhan_tu_ten_cot(name, expected):
    assert nc.nhan_tu_ten(name) == expected


async def test_qwen_dat_nhan_hop_le_thi_dung(llm):
    fake = llm(FakeLLM({"cot": [{"ten": "tenphongban", "nhan": "Phòng ban", "don_vi": ""},
                                {"ten": "maphongban", "nhan": "Mã phòng ban", "don_vi": ""},
                                {"ten": "soluongnhansu", "nhan": "Số nhân sự", "don_vi": "người"}]}))
    out = await nc.gan_nhan_cot(_result(("tenphongban", "nvarchar", None), ("maphongban", "nvarchar", None),
                                        ("soluongnhansu", "int", None)))
    assert [(c["label"], c["unit"]) for c in out["columns"]] == \
        [("Phòng ban", ""), ("Mã phòng ban", ""), ("Số nhân sự", "người")]
    assert fake.calls == 1


async def test_qwen_tra_ten_truong_hoac_loi_thi_lui_ve_tu_dien(llm):
    llm(FakeLLM({"cot": [{"ten": "soluongnhansu", "nhan": "soluong_nhansu", "don_vi": ""}]}))
    out = await nc.gan_nhan_cot(_result(("tenphongban", "nvarchar", None), ("soluongnhansu", "int", None),
                                        ("x9q", "int", None)))
    assert [c["label"] for c in out["columns"]] == ["Phòng ban", "Số lượng nhân sự", "Cột 3"]
    assert out["columns"][1]["unit"] == "người"

    llm(FakeLLM(fail=True))
    out = await nc.gan_nhan_cot(_result(("maphongban", "nvarchar", None)))
    assert out["columns"][0]["label"] == "Mã phòng ban"


async def test_cot_da_co_nhan_tot_thi_khong_goi_qwen(llm):
    fake = llm(FakeLLM(fail=True))
    out = await nc.gan_nhan_cot(_result(("department", "nvarchar", "Phòng ban"), ("cnt", "int", "Số nhân sự")))
    assert fake.calls == 0 and [c["label"] for c in out["columns"]] == ["Phòng ban", "Số nhân sự"]
