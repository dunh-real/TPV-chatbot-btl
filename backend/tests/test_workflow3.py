"""Workflow 3: soạn văn bản theo mẫu - số liệu từ CSDL, LLM chỉ viết văn."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.nodes import drafting as dr
from app.documents.docx_builder import (
    DocumentPayload,
    RenderedSection,
    RenderedTable,
    build_docx,
)
from app.documents.parser import parse_document
from app.documents.rules import RuleEngine
from app.documents.verify import check_numbers, collect_known_numbers

DATA = {
    "ma_don_vi": "DV02", "ten_don_vi": "Đơn vị 2", "ky": "2026-08",
    "nhan_su": 65, "nhan_su_kiem_ke": "2026-08-30",
    "tong_so_thiet_bi": 80, "so_loai_thiet_bi": 4, "so_loai_can_bao_duong": 2,
    "thiet_bi": [
        {"ten_thiet_bi": "Máy chủ", "so_luong": 6, "tinh_trang": "Tốt",
         "cap_nhat_cuoi": "2026-08-02"},
        {"ten_thiet_bi": "Xe công vụ", "so_luong": 2, "tinh_trang": "Cần bảo dưỡng",
         "cap_nhat_cuoi": "2026-01-25"},
    ],
}


# ------------------------------------------------- đối chiếu số liệu ------ #
def test_so_lieu_dung_thi_qua():
    known = collect_known_numbers(DATA)
    check = check_numbers("Nhân sự là 65 người, kiểm kê ngày 30/8/2026.", known)
    assert check.ok


def test_so_bia_bi_bat():
    known = collect_known_numbers(DATA)
    assert check_numbers("Nhân sự là 70 người.", known).unverified == ["70"]
    assert check_numbers("Đề nghị cấp 15.000.000 đồng.", known).unverified == ["15.000.000"]


def test_khoang_thoi_gian_va_so_thu_tu_khong_bi_bat():
    """Văn phong hành chính có "quá hạn 12 tháng", "nêu tại mục 2" - không phải số liệu."""
    known = collect_known_numbers(DATA)
    assert check_numbers("Thiết bị quá hạn bảo dưỡng hơn 12 tháng, nêu tại mục 3.", known).ok


def test_so_lieu_ghi_khac_dinh_dang_van_truy_duoc():
    known = collect_known_numbers({"so_tien": 5000000})
    assert check_numbers("Kinh phí 5.000.000 đồng.", known).ok      # có dấu chấm phân cách


# ----------------------------------------------------- dựng mục bằng code - #




# ------------------------------------------------------------ xuất DOCX --- #
@pytest.fixture
def payload() -> DocumentPayload:
    return DocumentPayload(
        meta={"noi_gui": "ĐƠN VỊ 2", "so_ky_hieu": "42/BC-DV02", "dia_danh": "Hà Nội",
              "ngay_bao_cao": "ngày 30 tháng 9 năm 2026",
              "trich_yeu": "V/v báo cáo nhân sự và trang thiết bị tháng 8/2026",
              "noi_nhan": "Ban Giám đốc", "chuc_vu_ky": "TRƯỞNG ĐƠN VỊ",
              "nguoi_ky": "Trần Văn B"},
        sections=[
            RenderedSection(id="nhan_su", title="I. TÌNH HÌNH NHÂN SỰ",
                            paragraphs=["Nhân sự 65 người."]),
            RenderedSection(id="thiet_bi", title="II. TRANG THIẾT BỊ",
                            paragraphs=["Tổng 80 đầu thiết bị."],
                            table=RenderedTable(columns=["Tên", "Số lượng"],
                                                rows=[["Máy chủ", "6"], ["Xe công vụ", "2"]])),
        ],
    )


def test_bao_cao_sinh_ra_qua_duoc_rule_engine(payload, tmp_path):
    """Văn bản do hệ thống sinh phải qua được chính rule engine của workflow 2."""
    output = build_docx(payload, tmp_path / "bc.docx",
                        template_path="data/templates/bao_cao_tai_nguyen.docx")
    structure = parse_document(output)
    result = RuleEngine().check(structure)

    assert result.error_count == 0 and result.warning_count == 0
    assert "consistency.font" in result.passed and "page.margin_mm" in result.passed


def test_khong_co_mau_thi_dung_bang_code(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc2.docx", template_path=None)
    structure = parse_document(output)
    result = RuleEngine().check(structure)

    assert output.exists()
    assert result.error_count == 0          # dựng bằng code vẫn phải qua được rule engine


def test_mau_khong_ton_tai_thi_lui_ve_dung_code(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc3.docx", template_path="khong/co/mau.docx")
    assert output.exists()


def test_noi_dung_va_bang_vao_dung_cho(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc4.docx",
                        template_path="data/templates/bao_cao_tai_nguyen.docx")
    structure = parse_document(output)
    texts = [b.text for b in structure.blocks]

    assert any("I. TÌNH HÌNH NHÂN SỰ" in t for t in texts)
    assert any("Nhân sự 65 người." in t for t in texts)
    assert any(b.kind == "table" and "Máy chủ" in b.text for b in structure.blocks)
    # Mục I phải đứng trước mục II
    assert texts.index("I. TÌNH HÌNH NHÂN SỰ") < texts.index("II. TRANG THIẾT BỊ")


# ------------------------------------------------ dữ liệu theo kỳ --------- #






# -------------------------------------------------- toàn bộ workflow 3 ---- #
@pytest.fixture
async def draft_env(tmp_path, monkeypatch, erp_session):
    """Kiểm tra node trích tham số, không mở DB nghiệp vụ hoặc SQLite."""
    monkeypatch.setattr(dr, "get_settings", lambda: _Settings(str(tmp_path)))
    yield tmp_path


class _Settings:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self.utility_model = "test"


class ScriptedLLM:
    """LLM giả: trích tháng/năm ngay từ câu yêu cầu để test đúng kỳ được hỏi."""

    def __init__(self, section_text: str) -> None:
        self.section_text = section_text
        self.section_calls = 0

    async def chat_json(self, messages, **kwargs):
        import re as _re

        system = messages[0]["content"]
        if "trích tham số" in system:
            request = messages[1]["content"]
            thang = int(m.group(1)) if (m := _re.search(r"tháng (\d{1,2})", request)) else None
            nam = int(m.group(1)) if (m := _re.search(r"/(\d{4})", request)) else None
            return {"loai_bao_cao": "báo cáo thiết bị", "thang": thang, "nam": nam,
                    "ma_don_vi": None}
        if "chọn mẫu báo cáo" in system:
            return {"ma_template": "BC_TAINGUYEN", "confidence": 0.9, "reason": "phù hợp"}
        self.section_calls += 1
        return {"paragraphs": [self.section_text]}












# ------------------------------------------------- lịch sử hội thoại ------- #
class LichSuLLM(ScriptedLLM):
    """Như ScriptedLLM nhưng giữ lại prompt trích tham số để soi."""

    def __init__(self) -> None:
        super().__init__("Nhân sự đơn vị là 3 người, kiểm kê ngày 31/8/2026.")
        self.params_prompt = ""

    async def chat_json(self, messages, **kwargs):
        if "trích tham số" in messages[0]["content"]:
            self.params_prompt = messages[1]["content"]
        return await super().chat_json(messages, **kwargs)


async def test_trich_tham_so_nhin_thay_luot_truoc(draft_env, monkeypatch):

    llm = LichSuLLM()
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    await dr.extract_params_node({"request": "soạn tiếp báo cáo thiết bị, vẫn đơn vị đó",
        "history": [{"role": "user", "content": "tổng hợp nhân sự DV02 tháng 8/2026"},
                 {"role": "assistant", "content": "Đã tổng hợp báo cáo kỳ 2026-08."}]}
    )

    assert "tổng hợp nhân sự DV02 tháng 8/2026" in llm.params_prompt
    assert "vẫn đơn vị đó" in llm.params_prompt


async def test_khong_truyen_lich_su_thi_prompt_bao_chua_co(draft_env, monkeypatch):

    llm = LichSuLLM()
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    await dr.extract_params_node({"request":"Soạn báo cáo thiết bị tháng 8/2026"})

    assert "(chưa có)" in llm.params_prompt
