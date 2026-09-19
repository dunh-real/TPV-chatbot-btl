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
from app.documents.structure import detect_components
from app.documents.verify import check_numbers, collect_known_numbers

DATA = {
    "ma_don_vi": "DV02", "ten_don_vi": "Đơn vị 2", "ky": "2026-08",
    "quan_so": 65, "quan_so_kiem_ke": "2026-08-30",
    "tong_so_trang_bi": 80, "so_loai_trang_bi": 4, "so_loai_can_bao_duong": 2,
    "trang_bi": [
        {"ten_trang_bi": "Máy chủ", "so_luong": 6, "tinh_trang": "Tốt",
         "cap_nhat_cuoi": "2026-08-02"},
        {"ten_trang_bi": "Xe công vụ", "so_luong": 2, "tinh_trang": "Cần bảo dưỡng",
         "cap_nhat_cuoi": "2026-01-25"},
    ],
}


# ------------------------------------------------- đối chiếu số liệu ------ #
def test_so_lieu_dung_thi_qua():
    known = collect_known_numbers(DATA)
    check = check_numbers("Quân số là 65 người, kiểm kê ngày 30/8/2026.", known)
    assert check.ok


def test_so_bia_bi_bat():
    known = collect_known_numbers(DATA)
    assert check_numbers("Quân số là 70 người.", known).unverified == ["70"]
    assert check_numbers("Đề nghị cấp 15.000.000 đồng.", known).unverified == ["15.000.000"]


def test_khoang_thoi_gian_va_so_thu_tu_khong_bi_bat():
    """Văn phong hành chính có "quá hạn 12 tháng", "nêu tại mục 2" - không phải số liệu."""
    known = collect_known_numbers(DATA)
    assert check_numbers("Thiết bị quá hạn bảo dưỡng hơn 12 tháng, nêu tại mục 3.", known).ok


def test_so_lieu_ghi_khac_dinh_dang_van_truy_duoc():
    known = collect_known_numbers({"so_tien": 5000000})
    assert check_numbers("Kinh phí 5.000.000 đồng.", known).ok      # có dấu chấm phân cách


# ----------------------------------------------------- dựng mục bằng code - #
def test_muc_data_va_table_khong_goi_llm():
    facts = dr._facts_paragraph({"fields": ["quan_so", "quan_so_kiem_ke"]}, DATA)
    assert "65" in facts
    assert "30/8/2026" in facts        # ngày ISO được chuyển sang cách viết tiếng Việt

    table = dr._build_table({"query": "trang_bi",
                             "columns": ["ten_trang_bi", "so_luong", "tinh_trang"]}, DATA)
    assert table.columns == ["Tên trang bị", "Số lượng", "Tình trạng"]
    assert table.rows[0] == ["Máy chủ", "6", "Tốt"]


def test_bang_rong_thi_khong_dung_bang():
    assert dr._build_table({"query": "trang_bi", "columns": ["x"]}, {"trang_bi": []}) is None


# ------------------------------------------------------------ xuất DOCX --- #
@pytest.fixture
def payload() -> DocumentPayload:
    return DocumentPayload(
        meta={"noi_gui": "ĐƠN VỊ 2", "so_ky_hieu": "42/BC-DV02", "dia_danh": "Hà Nội",
              "ngay_bao_cao": "ngày 30 tháng 9 năm 2026",
              "trich_yeu": "V/v báo cáo quân số và trang thiết bị tháng 8/2026",
              "noi_nhan": "Ban Giám đốc", "chuc_vu_ky": "TRƯỞNG ĐƠN VỊ",
              "nguoi_ky": "Trần Văn B"},
        sections=[
            RenderedSection(id="quan_so", title="I. TÌNH HÌNH QUÂN SỐ",
                            paragraphs=["Quân số 65 người."]),
            RenderedSection(id="trang_bi", title="II. TRANG THIẾT BỊ",
                            paragraphs=["Tổng 80 đầu trang bị."],
                            table=RenderedTable(columns=["Tên", "Số lượng"],
                                                rows=[["Máy chủ", "6"], ["Xe công vụ", "2"]])),
        ],
    )


def test_bao_cao_sinh_ra_dat_the_thuc_nd30(payload, tmp_path):
    """Văn bản do hệ thống sinh phải qua được chính rule engine của workflow 2."""
    output = build_docx(payload, tmp_path / "bc.docx",
                        template_path="data/templates/bao_cao_tai_nguyen.docx")
    structure = parse_document(output)
    result = RuleEngine().check(structure, detect_components(structure))

    assert result.status == "done"
    assert result.error_count == 0 and result.warning_count == 0
    assert "format.font" in result.passed and "format.margin_mm" in result.passed


def test_khong_co_mau_thi_dung_bang_code(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc2.docx", template_path=None)
    structure = parse_document(output)
    result = RuleEngine().check(structure, detect_components(structure))

    assert output.exists()
    assert result.error_count == 0          # dựng bằng code vẫn phải đúng thể thức


def test_mau_khong_ton_tai_thi_lui_ve_dung_code(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc3.docx", template_path="khong/co/mau.docx")
    assert output.exists()


def test_noi_dung_va_bang_vao_dung_cho(payload, tmp_path):
    output = build_docx(payload, tmp_path / "bc4.docx",
                        template_path="data/templates/bao_cao_tai_nguyen.docx")
    structure = parse_document(output)
    texts = [b.text for b in structure.blocks]

    assert any("I. TÌNH HÌNH QUÂN SỐ" in t for t in texts)
    assert any("Quân số 65 người." in t for t in texts)
    assert any(b.kind == "table" and "Máy chủ" in b.text for b in structure.blocks)
    # Mục I phải đứng trước mục II
    assert texts.index("I. TÌNH HÌNH QUÂN SỐ") < texts.index("II. TRANG THIẾT BỊ")


# ------------------------------------------------ dữ liệu theo kỳ --------- #
async def test_moi_ky_ra_so_lieu_khac_nhau(erp_session):
    """Cùng một đơn vị, hai kỳ khác nhau phải ra hai con số khác nhau."""
    from app.db.erp_repository import ErpTaiNguyenRepository

    repo = ErpTaiNguyenRepository(erp_session)
    thang_7 = await repo.get_tai_nguyen("00002", "2026-07")
    thang_8 = await repo.get_tai_nguyen("00002", "2026-08")

    # Tháng 8 có thêm một người vào làm và một chiếc xe công vụ mới mua.
    assert (thang_7.quan_so, thang_7.tong_trang_bi) == (2, 6)
    assert (thang_8.quan_so, thang_8.tong_trang_bi) == (3, 8)


async def test_ky_truoc_khi_co_du_lieu_ra_so_khong(erp_session):
    """ERP suy số theo mốc thời gian nên kỳ quá khứ xa ra 0, không phải lỗi."""
    from app.db.erp_repository import ErpTaiNguyenRepository

    tai_nguyen = await ErpTaiNguyenRepository(erp_session).get_tai_nguyen("00002", "2025-01")
    assert tai_nguyen.quan_so == 0
    assert tai_nguyen.tong_trang_bi == 0


async def test_ky_tuong_lai_lay_hien_trang(erp_session):
    """Không còn cơ chế "lùi về kỳ gần nhất": mốc sau hiện tại chỉ là hiện trạng."""
    from app.db.erp_repository import ErpTaiNguyenRepository

    repo = ErpTaiNguyenRepository(erp_session)
    assert (await repo.get_tai_nguyen("00002", "2027-12")).tong_trang_bi == 8


# -------------------------------------------------- toàn bộ workflow 3 ---- #
@pytest.fixture
async def draft_env(tmp_path, monkeypatch, erp_session):
    """Hai nguồn tách rời: số liệu đọc từ ERP, mẫu báo cáo ghi ở CSDL app."""
    import json
    from contextlib import asynccontextmanager

    from app.db.models import Base, TemplateBaoCao

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    fields = {
        "meta": {"noi_gui": {"source": "unit.ten_don_vi"},
                 "noi_nhan": {"source": "literal", "value": "Ban Giám đốc"},
                 "nguoi_ky": {"source": "input", "label": "Người ký"}},
        "sections": [
            {"id": "quan_so", "title": "I. TÌNH HÌNH QUÂN SỐ", "type": "data",
             "query": "don_vi", "fields": ["quan_so", "quan_so_kiem_ke"],
             "narrative": "Nhận xét về quân số."},
            {"id": "trang_bi", "title": "II. TRANG THIẾT BỊ", "type": "table",
             "query": "trang_bi", "columns": ["ten_trang_bi", "so_luong", "tinh_trang"]},
        ],
    }

    async with factory() as session:
        session.add(TemplateBaoCao(
            ma_template="BC_TAINGUYEN",
            ten_bao_cao="Báo cáo quân số và trang thiết bị",
            loai_bao_cao="bao_cao_dinh_ky", mo_ta="Dùng khi báo cáo trang bị",
            file_path="data/templates/bao_cao_tai_nguyen.docx",
            truong_du_lieu=json.dumps(fields, ensure_ascii=False)))
        await session.commit()

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            yield session
            await session.commit()

    @asynccontextmanager
    async def _erp_scope():
        yield erp_session

    monkeypatch.setattr(dr, "session_scope", _scope)
    monkeypatch.setattr(dr, "erp_session_scope", _erp_scope)
    monkeypatch.setattr("app.core.config.settings.output_dir", str(tmp_path), raising=False)
    monkeypatch.setattr(dr, "get_settings", lambda: _Settings(str(tmp_path)))
    yield tmp_path
    await engine.dispose()


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
            return {"loai_bao_cao": "báo cáo trang bị", "thang": thang, "nam": nam,
                    "ma_don_vi": None}
        if "chọn mẫu báo cáo" in system:
            return {"ma_template": "BC_TAINGUYEN", "confidence": 0.9, "reason": "phù hợp"}
        self.section_calls += 1
        return {"paragraphs": [self.section_text]}


async def test_soan_bao_cao_hoan_chinh(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    llm = ScriptedLLM("Quân số đơn vị là 3 người, kiểm kê ngày 31/8/2026.")
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    result = await run_draft_workflow("Soạn báo cáo tình hình trang bị tháng 8",
                                      ma_don_vi="00002", inputs={"nguoi_ky": "Trần Văn B"})

    assert result["params"]["ky"] == "2026-08"
    assert result["assumptions"] == ["Không nêu năm, hiểu là năm 2026"]
    assert result["validation"]["status"] == "passed"
    assert Path(result["output_path"]).exists()
    assert result["registered_as"]


async def test_thieu_don_vi_thi_hoi_lai_chu_khong_doan(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    monkeypatch.setattr(dr, "get_llm", lambda: ScriptedLLM("x"))
    result = await run_draft_workflow("Soạn báo cáo tình hình trang bị tháng 8")

    assert result["missing_input"] == ["ma_don_vi"]
    assert result["output_path"] == ""


async def test_so_bia_thi_viet_lai_roi_tu_choi_xuat_file(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    llm = ScriptedLLM("Quân số đơn vị là 70 người. Đề nghị cấp 15.000.000 đồng.")
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    result = await run_draft_workflow("Báo cáo trang bị tháng 8/2026", ma_don_vi="00002")

    assert result["validation"]["status"] == "failed"
    assert result["retry_count"] == 2                 # đã cho viết lại
    assert result["output_path"] == ""                # nhưng vẫn không xuất file
    numbers = {n for issue in result["validation"]["issues"] for n in issue["numbers"]}
    assert numbers == {"70", "15.000.000"}


async def test_so_lieu_theo_ky_phai_kem_canh_bao_suy_nguoc(draft_env, monkeypatch):
    """ERP không chốt số theo kỳ nên số kỳ cũ là hiện trạng suy ngược.

    Người ký cần thấy cảnh báo này trong văn bản, vì cùng một báo cáo đọc lại sau
    vài tháng có thể ra con số khác mà không ai sửa gì.
    """
    from app.agents.graph import run_draft_workflow

    monkeypatch.setattr(dr, "get_llm", lambda: ScriptedLLM("Quân số đơn vị là 3 người."))
    result = await run_draft_workflow("Báo cáo trang bị tháng 8/2026", ma_don_vi="00002")

    assert any("suy ngược" in note or "không phải số đã chốt" in note
               for note in result["data_notes"])


async def test_file_sinh_ra_qua_duoc_rule_engine(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    llm = ScriptedLLM("Quân số đơn vị là 3 người, kiểm kê ngày 31/8/2026.")
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    result = await run_draft_workflow("Báo cáo trang bị tháng 8/2026", ma_don_vi="00002",
                                      inputs={"nguoi_ky": "Trần Văn B",
                                              "chuc_vu_ky": "TRƯỞNG ĐƠN VỊ",
                                              "so_ky_hieu": "42/BC-DV02"})
    structure = parse_document(result["output_path"])
    check = RuleEngine().check(structure, detect_components(structure))

    assert check.error_count == 0


# ------------------------------------------------- lịch sử hội thoại ------- #
class LichSuLLM(ScriptedLLM):
    """Như ScriptedLLM nhưng giữ lại prompt trích tham số để soi."""

    def __init__(self) -> None:
        super().__init__("Quân số đơn vị là 3 người, kiểm kê ngày 31/8/2026.")
        self.params_prompt = ""

    async def chat_json(self, messages, **kwargs):
        if "trích tham số" in messages[0]["content"]:
            self.params_prompt = messages[1]["content"]
        return await super().chat_json(messages, **kwargs)


async def test_trich_tham_so_nhin_thay_luot_truoc(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    llm = LichSuLLM()
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    await run_draft_workflow(
        "soạn tiếp báo cáo trang bị, vẫn đơn vị đó",
        ma_don_vi="00002",
        history=[{"role": "user", "content": "tổng hợp quân số DV02 tháng 8/2026"},
                 {"role": "assistant", "content": "Đã tổng hợp báo cáo kỳ 2026-08."}],
    )

    assert "tổng hợp quân số DV02 tháng 8/2026" in llm.params_prompt
    assert "vẫn đơn vị đó" in llm.params_prompt


async def test_khong_truyen_lich_su_thi_prompt_bao_chua_co(draft_env, monkeypatch):
    from app.agents.graph import run_draft_workflow

    llm = LichSuLLM()
    monkeypatch.setattr(dr, "get_llm", lambda: llm)

    await run_draft_workflow("Soạn báo cáo trang bị tháng 8/2026", ma_don_vi="00002")

    assert "(chưa có)" in llm.params_prompt
