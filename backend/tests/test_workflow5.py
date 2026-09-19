"""Workflow 5: tạo slide - LLM sinh JSON trung gian, code dựng file."""

from __future__ import annotations

from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Emu
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.nodes import presentation as pr
from app.agents.nodes import report as rp
from app.documents.charts import compare_bar_chart
from app.documents.pptx_builder import (
    SLIDE_KINDS,
    DeckSpec,
    MetricBox,
    SlideSpec,
    SlideTable,
    build_pptx,
)


# --------------------------------------------------------------- renderer -- #
@pytest.fixture
def deck() -> DeckSpec:
    chart = compare_bar_chart("Quân số", ["Tổng", "Có mặt"], [144, 133], [140, 130])
    return DeckSpec(
        title="Báo cáo quân số tháng 8/2026", subtitle="Phòng Hành chính quản trị",
        slides=[
            SlideSpec(kind="title", title="Báo cáo quân số tháng 8/2026",
                      subtitle="Phòng Hành chính quản trị"),
            SlideSpec(kind="summary", title="Tổng quan",
                      metrics=[MetricBox("Tổng quân số", "144", "+4 (2.9%)"),
                               MetricBox("Có mặt", "133", "92.4%")],
                      bullets=["3/3 đơn vị đã gửi báo cáo"]),
            SlideSpec(kind="chart", title="Biến động quân số", chart=chart,
                      caption="Nguồn: kiểm kê tháng 8/2026"),
            SlideSpec(kind="table", title="Chi tiết theo đơn vị",
                      table=SlideTable(columns=["Đơn vị", "Quân số"],
                                       rows=[["Đơn vị 1", "48"], ["Đơn vị 2", "65"]])),
            SlideSpec(kind="bullet", title="Đánh giá",
                      bullets=["Quân số tăng 4 người", "Tỷ lệ có mặt 92.4%"],
                      notes="Nhấn mạnh trang bị cần bảo dưỡng"),
        ],
    )


def test_dung_du_slide_va_dung_kieu(deck, tmp_path):
    output = build_pptx(deck, tmp_path / "deck.pptx")
    presentation = Presentation(str(output))

    assert len(presentation.slides) == 5
    assert Emu(presentation.slide_width).inches == pytest.approx(13.333, abs=0.01)  # 16:9


def test_bieu_do_va_bang_duoc_nhung_that(deck, tmp_path):
    output = build_pptx(deck, tmp_path / "deck.pptx")
    presentation = Presentation(str(output))

    chart_slide = presentation.slides[2]
    assert any(shape.shape_type == 13 for shape in chart_slide.shapes)   # 13 = picture

    table_slide = presentation.slides[3]
    table = next(shape.table for shape in table_slide.shapes if shape.has_table)
    assert table.cell(0, 0).text_frame.text == "Đơn vị"
    assert table.cell(1, 1).text_frame.text == "48"


def test_khong_co_shape_nao_tran_ra_ngoai(deck, tmp_path):
    """Slide tràn lề là lỗi hay gặp nhất khi dựng pptx bằng code."""
    output = build_pptx(deck, tmp_path / "deck.pptx")
    presentation = Presentation(str(output))
    width, height = presentation.slide_width, presentation.slide_height

    for index, slide in enumerate(presentation.slides, start=1):
        for shape in slide.shapes:
            if shape.left is None:
                continue
            assert shape.left >= 0 and shape.top >= 0, f"slide {index}"
            assert shape.left + (shape.width or 0) <= width, f"slide {index}"
            assert shape.top + (shape.height or 0) <= height, f"slide {index}"


def test_ghi_chu_nguoi_trinh_bay_duoc_luu(deck, tmp_path):
    output = build_pptx(deck, tmp_path / "deck.pptx")
    presentation = Presentation(str(output))
    assert "bảo dưỡng" in presentation.slides[4].notes_slide.notes_text_frame.text


def test_bang_qua_dai_tach_slide_chu_khong_cat_bo(tmp_path):
    """Bảng số liệu phải ĐỦ: thiếu một dòng là người đọc cộng ra số khác với tổng.

    Trước đây dôi ra bao nhiêu dòng thì bỏ bấy nhiêu, kèm câu "xem chi tiết trong
    báo cáo" - mà bộ slide tạo riêng lẻ thì không có bản báo cáo nào để xem.
    """
    rows = [[f"Đơn vị {i}", str(i)] for i in range(20)]
    deck = DeckSpec(title="x", slides=[
        SlideSpec(kind="table", title="Bảng dài",
                  table=SlideTable(columns=["Đơn vị", "Quân số"], rows=rows))])

    slides = Presentation(str(build_pptx(deck, tmp_path / "deck.pptx"))).slides
    assert len(slides) == 2                       # 20 dòng / 11 mỗi slide

    du_lieu = []
    for slide in slides:
        table = next(shape.table for shape in slide.shapes if shape.has_table)
        # Bỏ dòng tiêu đề cột, dòng này lặp lại ở mọi trang.
        du_lieu += [[c.text for c in row.cells] for row in list(table.rows)[1:]]

    assert du_lieu == rows                        # đủ cả 20 dòng, đúng thứ tự
    assert len({row.cells[0].text for slide in slides
                for row in [list(next(sh.table for sh in slide.shapes
                                      if sh.has_table).rows)[0]]}) == 1  # mọi trang đều có tiêu đề cột


def test_bang_nhieu_trang_co_danh_so_de_biet_dang_o_dau(tmp_path):
    rows = [[f"Đơn vị {i}", str(i)] for i in range(20)]
    deck = DeckSpec(title="x", slides=[
        SlideSpec(kind="table", title="Chi tiết",
                  table=SlideTable(columns=["Đơn vị", "Quân số"], rows=rows))])

    slides = Presentation(str(build_pptx(deck, tmp_path / "deck.pptx"))).slides
    chu = [" ".join(sh.text_frame.text for sh in slide.shapes if sh.has_text_frame)
           for slide in slides]

    assert "Chi tiết (1/2)" in chu[0] and "Chi tiết (2/2)" in chu[1]
    assert "dòng 1-11/20" in chu[0] and "dòng 12-20/20" in chu[1]


def test_bang_ngan_khong_bi_danh_so_thua(tmp_path):
    deck = DeckSpec(title="x", slides=[
        SlideSpec(kind="table", title="Chi tiết",
                  table=SlideTable(columns=["Đơn vị"], rows=[["A"], ["B"]]))])

    slides = Presentation(str(build_pptx(deck, tmp_path / "deck.pptx"))).slides
    assert len(slides) == 1
    chu = " ".join(sh.text_frame.text for sh in slides[0].shapes if sh.has_text_frame)
    assert "Chi tiết" in chu and "(1/1)" not in chu and "dòng" not in chu


def test_slide_kieu_la_bi_bo_qua(tmp_path):
    deck = DeckSpec(title="x", slides=[
        SlideSpec(kind="title", title="Bìa"),
        SlideSpec(kind="kieu_khong_ton_tai", title="Lạ"),
    ])
    assert len(Presentation(str(build_pptx(deck, tmp_path / "d.pptx"))).slides) == 1


# ------------------------------------------------------- lọc dàn ý LLM ---- #
DATA = {
    "personnel": {
        "metrics": {"total_personnel": {"value": 113, "prev": 110, "delta": 3,
                                        "delta_pct": 2.7, "share_pct": None},
                    "present": {"value": 104, "prev": 102, "delta": 2,
                                "delta_pct": 2.0, "share_pct": 92.0}},
        "scope": {"units_requested": 2, "units_with_data": 2, "units_missing": []},
        "breakdown": [{"ma_don_vi": "DV01", "ten_don_vi": "Đơn vị 1", "quan_so": 48,
                       "co_mat": 44, "vang": 4, "di_hoc": 3, "nghi_phep": 1}],
    },
    "reporting": {"units_total": 2, "units_reported": 1,
                  "missing": [{"ma_don_vi": "DV01", "ten_don_vi": "Đơn vị 1"}],
                  "reported": []},
}
PARAMS = {"ky": "2026-08", "thang": 8, "nam": 2026, "compare_to": "2026-07"}


class OutlineLLM:
    def __init__(self, outline: dict | None = None, bullets: list[str] | None = None) -> None:
        self.outline = outline
        self.bullets = bullets or ["Quân số tăng 3 người so với kỳ trước"]

    async def chat_json(self, messages, **kwargs):
        if "lập dàn ý" in messages[0]["content"]:
            if self.outline is None:
                raise RuntimeError("LLM hỏng")
            return self.outline
        return {"bullets": self.bullets, "notes": ""}


async def test_loai_slide_kieu_la_va_chart_khong_co_du_lieu(monkeypatch):
    monkeypatch.setattr(pr, "get_llm", lambda: OutlineLLM({
        "title": "Báo cáo", "slides": [
            {"kind": "title", "title": "Bìa"},
            {"kind": "chart", "title": "Có dữ liệu", "chart_key": "personnel"},
            {"kind": "chart", "title": "Không có dữ liệu", "chart_key": "khong_co"},
            {"kind": "table", "title": "Bảng lạ", "data_key": "bang_khong_ton_tai"},
            {"kind": "kieu_tu_nghi", "title": "Lạ"},
        ]}))

    result = await pr.outline_node({"request": "x", "data": DATA, "params": PARAMS})
    slides = result["outline"]["slides"]
    kinds = [s["kind"] for s in slides]

    # Chart không có dữ liệu, bảng không tồn tại và kiểu tự nghĩ đều bị loại.
    assert "khong_co" not in [s.get("chart_key") for s in slides]
    assert "bang_khong_ton_tai" not in [s.get("data_key") for s in slides]
    assert all(k in SLIDE_KINDS for k in kinds)

    # Còn lại là slide bìa, biểu đồ quân số, kèm bảng chi tiết quân số mà
    # `_ensure_detail_tables` thêm vào vì dàn ý có biểu đồ quân số mà thiếu bảng.
    assert kinds == ["title", "chart", "table"]
    assert slides[2]["data_key"] == "personnel_breakdown"


async def test_llm_hong_thi_dung_dan_y_mac_dinh(monkeypatch):
    monkeypatch.setattr(pr, "get_llm", lambda: OutlineLLM(outline=None))

    result = await pr.outline_node({"request": "x", "data": DATA, "params": PARAMS})
    slides = result["outline"]["slides"]

    assert slides[0]["kind"] == "title"
    assert any(s["kind"] == "chart" for s in slides)
    assert slides[-1]["kind"] == "bullet"


async def test_luon_co_slide_bia(monkeypatch):
    monkeypatch.setattr(pr, "get_llm", lambda: OutlineLLM({
        "title": "Báo cáo", "slides": [{"kind": "bullet", "title": "Đánh giá"}]}))

    result = await pr.outline_node({"request": "x", "data": DATA, "params": PARAMS})
    assert result["outline"]["slides"][0]["kind"] == "title"


def test_o_chi_tieu_do_code_dung_tu_so_lieu():
    boxes = pr._metric_boxes(DATA, "quan_so")
    assert boxes[0].label == "Tổng quân số" and boxes[0].value == "113"
    assert boxes[0].note == "+3 (2.7%)"          # lấy thẳng delta đã tính sẵn


def test_moi_slide_chi_nhan_so_lieu_cua_muc_do():
    payload = pr._slide_payload("quan_so", DATA)
    assert "quan_so" in payload and "trang_bi" not in payload


# ------------------------------------------------ toàn bộ workflow 5 ------ #
@pytest.fixture
async def ppt_env(tmp_path, monkeypatch, erp_session):
    """Số liệu đọc từ ERP tạm, sổ văn bản ở CSDL app tạm, output vào thư mục tạm."""
    from contextlib import asynccontextmanager

    from app.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app_session = factory()

    @asynccontextmanager
    async def _scope():
        yield app_session

    @asynccontextmanager
    async def _erp_scope():
        yield erp_session

    class _Settings:
        output_dir = str(tmp_path)
        utility_model = "test"

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "session_scope", _scope)
    monkeypatch.setattr(rp, "session_scope", _scope)
    monkeypatch.setattr(rp, "erp_session_scope", _erp_scope)
    monkeypatch.setattr(rp, "get_settings", lambda: _Settings())
    monkeypatch.setattr(pr, "get_settings", lambda: _Settings())
    yield tmp_path
    await app_session.close()
    await engine.dispose()


class DeckLLM:
    """LLM giả cho cả ba bước: tham số, dàn ý, nội dung slide."""

    def __init__(self, bullets: list[str]) -> None:
        self.bullets = bullets

    async def chat_json(self, messages, **kwargs):
        system = messages[0]["content"]
        if "trích tham số" in system:
            return {"thang": 8, "nam": 2026, "ma_don_vi": [], "so_sanh_thang": None,
                    "noi_dung": ["quan_so", "trang_bi"]}
        if "lập dàn ý" in system:
            return {"title": "Báo cáo quân số tháng 8/2026", "subtitle": "Toàn cơ quan",
                    "slides": [
                        {"kind": "title", "title": "Báo cáo quân số tháng 8/2026"},
                        {"kind": "summary", "title": "Tổng quan", "focus": "quan_so"},
                        {"kind": "chart", "title": "Biến động quân số",
                         "chart_key": "personnel", "focus": "quan_so"},
                        {"kind": "table", "title": "Chi tiết theo đơn vị",
                         "data_key": "personnel_breakdown"},
                        {"kind": "bullet", "title": "Đánh giá", "focus": "quan_so"},
                    ]}
        return {"bullets": self.bullets, "notes": "ghi chú"}


async def test_tao_bo_slide_hoan_chinh(ppt_env, monkeypatch):
    from app.agents.graph import run_presentation_workflow

    llm = DeckLLM(["Tổng quân số 5 người", "Trong kỳ tuyển mới 1 người"])
    monkeypatch.setattr(rp, "get_llm", lambda: llm)
    monkeypatch.setattr(pr, "get_llm", lambda: llm)

    result = await run_presentation_workflow("Tạo slide báo cáo quân số tháng 8")

    assert result["validation"]["status"] == "passed"
    assert result["slide_count"] == 5
    assert Path(result["output_path"]).exists()

    presentation = Presentation(result["output_path"])
    assert len(presentation.slides) == 5
    assert any(shape.shape_type == 13 for shape in presentation.slides[2].shapes)


async def test_bullet_co_so_bia_bi_loai_nhung_van_ra_file(ppt_env, monkeypatch):
    """Slide là tài liệu nội bộ: bỏ đúng bullet sai, không chặn cả bộ slide."""
    from app.agents.graph import run_presentation_workflow

    llm = DeckLLM(["Tổng quân số 5 người", "Đề nghị bổ sung 47 biên chế"])
    monkeypatch.setattr(rp, "get_llm", lambda: llm)
    monkeypatch.setattr(pr, "get_llm", lambda: llm)

    result = await run_presentation_workflow("Tạo slide báo cáo quân số tháng 8")

    assert result["validation"]["status"] == "failed"
    assert result["removed_bullets"] > 0
    assert Path(result["output_path"]).exists()        # vẫn có file để người dùng sửa

    texts = " ".join(
        shape.text_frame.text
        for slide in Presentation(result["output_path"]).slides
        for shape in slide.shapes if shape.has_text_frame
    )
    assert "47" not in texts                           # bullet bịa không lọt vào file
    assert "Tổng quân số 5 người" in texts


async def test_o_chi_tieu_khong_phu_thuoc_llm(ppt_env, monkeypatch):
    """LLM không viết được chữ thì slide tổng quan vẫn có số liệu."""
    from app.agents.graph import run_presentation_workflow

    class BrokenContentLLM(DeckLLM):
        async def chat_json(self, messages, **kwargs):
            if "viết nội dung cho một slide" in messages[0]["content"]:
                raise RuntimeError("LLM hỏng")
            return await super().chat_json(messages, **kwargs)

    llm = BrokenContentLLM([])
    monkeypatch.setattr(rp, "get_llm", lambda: llm)
    monkeypatch.setattr(pr, "get_llm", lambda: llm)

    result = await run_presentation_workflow("Tạo slide quân số tháng 8")
    summary = next(s for s in result["slides"] if s["kind"] == "summary")

    assert summary["metrics"]          # ô chỉ tiêu do code dựng, vẫn còn
    assert summary["bullets"] == []    # chỉ mất phần chữ
    assert Path(result["output_path"]).exists()


# ------------------------------------------------- lịch sử hội thoại ------- #
class GhiPromptLLM:
    """Ghi lại prompt để kiểm tra dàn ý có nhìn thấy lượt trước hay không."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def chat_json(self, messages, **kwargs):
        self.messages = messages
        return {"title": "Báo cáo", "slides": [{"kind": "title", "title": "Bìa"}]}


async def test_dan_y_nhin_thay_luot_truoc(monkeypatch):
    """"làm luôn slide từ số liệu đó" chỉ hiểu được nếu lượt trước đi vào prompt."""
    llm = GhiPromptLLM()
    monkeypatch.setattr(pr, "get_llm", lambda: llm)

    await pr.outline_node({
        "request": "làm luôn slide từ số liệu đó",
        "data": DATA, "params": PARAMS,
        "history": [{"role": "user", "content": "soạn báo cáo tài nguyên DV01 tháng 8"},
                    {"role": "assistant", "content": "Đã soạn báo cáo tài nguyên DV01."}],
    })

    user = llm.messages[1]["content"]
    assert "soạn báo cáo tài nguyên DV01 tháng 8" in user
    assert "làm luôn slide từ số liệu đó" in user


async def test_khong_co_lich_su_van_chay_binh_thuong(monkeypatch):
    llm = GhiPromptLLM()
    monkeypatch.setattr(pr, "get_llm", lambda: llm)

    result = await pr.outline_node({"request": "tạo slide", "data": DATA, "params": PARAMS})

    assert "(chưa có)" in llm.messages[1]["content"]
    assert result["outline"]["slides"][0]["kind"] == "title"


# --------------------------------------------------------------------------- #
# `focus` là enum, không phải chú thích tự do
# --------------------------------------------------------------------------- #
def test_focus_van_xuoi_bi_ep_ve_gia_tri_dung_duoc():
    """Model từng trả về cả câu; khi đó không nhánh dữ liệu nào khớp."""
    from app.agents.nodes.presentation import _normalize_focus

    assert _normalize_focus("Tổng quan nhanh về quân số và trang bị", None, None) == "tong_hop"
    assert _normalize_focus("quan_so", None, None) == "quan_so"
    assert _normalize_focus("  TRANG_BI  ", None, None) == "trang_bi"


def test_focus_suy_tu_khoa_du_lieu_khi_model_viet_lung_tung():
    """Slide biểu đồ đã tự khai nói về cái gì - tin khoá dữ liệu hơn tin chữ."""
    from app.agents.nodes.presentation import _normalize_focus

    assert _normalize_focus("Tình hình thiết bị kỹ thuật", "equipment", None) == "trang_bi"
    assert _normalize_focus("", None, "personnel_breakdown") == "quan_so"


def test_o_chi_tieu_bam_theo_focus():
    """Slide nói về trang bị không được hiện ô quân số."""
    from app.agents.nodes.presentation import _metric_boxes

    data = {
        "personnel": {"metrics": {"total_personnel": {"value": 28, "delta": None,
                                                     "delta_pct": None, "share_pct": None}}},
        "equipment": {"metrics": {"total_equipment": {"value": 194, "delta": None,
                                                     "delta_pct": None, "share_pct": None}}},
    }
    assert [b.value for b in _metric_boxes(data, "trang_bi")] == ["194"]
    assert [b.value for b in _metric_boxes(data, "quan_so")] == ["28"]
    # Slide "chỉ tiêu chính" của bộ slide hỗn hợp phải thấy cả hai, không rỗng.
    assert [b.value for b in _metric_boxes(data, "tong_hop")] == ["28", "194"]


def test_bo_slide_chi_co_trang_bi_khong_ra_slide_chi_tieu_rong():
    """Đúng cái slide trắng trong bộ slide đang dùng: deck trang bị, ô lấy quân số."""
    from app.agents.nodes.presentation import _metric_boxes

    chi_trang_bi = {"equipment": {"metrics": {"total_equipment": {
        "value": 194, "delta": None, "delta_pct": None, "share_pct": None}}}}
    assert _metric_boxes(chi_trang_bi, "tong_hop") != []


def test_slide_count_dem_ca_slide_sinh_them_khi_phan_trang():
    """Nhãn "N slide" trên giao diện phải khớp số slide trong file.

    `len(specs)` đếm trước bước phân trang, nên một bảng 33 dòng làm giao diện ghi
    "5 slide" trên một file 7 slide - người dùng tin cái nhãn chứ không mở ra đếm.
    """
    from app.documents.pptx_builder import count_slides

    specs = [
        SlideSpec(kind="title", title="Bìa"),
        SlideSpec(kind="table", title="Chi tiết",
                  table=SlideTable(columns=["A"], rows=[[str(i)] for i in range(33)])),
    ]
    assert count_slides(specs) == 1 + 3          # 33 dòng / 11 = 3 slide bảng


def test_slide_count_khop_voi_file_dung_ra(tmp_path):
    from app.documents.pptx_builder import count_slides

    specs = [
        SlideSpec(kind="title", title="Bìa"),
        SlideSpec(kind="table", title="Chi tiết",
                  table=SlideTable(columns=["A"], rows=[[str(i)] for i in range(33)])),
        SlideSpec(kind="kieu_la", title="Bị bỏ qua"),
    ]
    output = build_pptx(DeckSpec(title="x", slides=specs), tmp_path / "d.pptx")
    assert count_slides(specs) == len(Presentation(str(output)).slides)


# --------------------------------------------------------------------------- #
# Bảng chi tiết không được phụ thuộc vào ý thích của model
# --------------------------------------------------------------------------- #
def test_dan_y_quen_bang_thi_code_tu_them():
    """Ba lần chạy thật với "báo cáo thông tin nhân viên" đều ra dàn ý không bảng."""
    from app.agents.nodes.presentation import _ensure_detail_tables

    slides = [
        {"kind": "title", "title": "Bìa", "focus": "tong_hop",
         "chart_key": None, "data_key": None},
        {"kind": "chart", "title": "Biến động quân số", "focus": "quan_so",
         "chart_key": "personnel", "data_key": None},
        {"kind": "bullet", "title": "Đánh giá", "focus": "tong_hop",
         "chart_key": None, "data_key": None},
    ]
    ket_qua = _ensure_detail_tables(slides, ["personnel_breakdown"])

    assert [s["kind"] for s in ket_qua] == ["title", "chart", "table", "bullet"]
    assert ket_qua[2]["data_key"] == "personnel_breakdown"
    assert ket_qua[2]["focus"] == "quan_so"


def test_khong_them_bang_cua_mang_khong_nhac_toi():
    """Bộ slide chỉ nói về quân số thì không tự nhiên mọc ra bảng trang bị."""
    from app.agents.nodes.presentation import _ensure_detail_tables

    slides = [
        {"kind": "chart", "title": "Quân số", "focus": "quan_so",
         "chart_key": "personnel", "data_key": None},
    ]
    ket_qua = _ensure_detail_tables(slides, ["personnel_breakdown", "equipment_breakdown"])

    assert [s.get("data_key") for s in ket_qua if s["kind"] == "table"] == ["personnel_breakdown"]


def test_bang_da_co_thi_khong_them_lan_hai():
    from app.agents.nodes.presentation import _ensure_detail_tables

    slides = [
        {"kind": "chart", "title": "Quân số", "focus": "quan_so",
         "chart_key": "personnel", "data_key": None},
        {"kind": "table", "title": "Chi tiết", "focus": "quan_so",
         "chart_key": None, "data_key": "personnel_breakdown"},
    ]
    assert _ensure_detail_tables(slides, ["personnel_breakdown"]) == slides


def test_dan_y_toan_tong_hop_thi_them_du_ca_hai_bang():
    from app.agents.nodes.presentation import _ensure_detail_tables

    slides = [{"kind": "summary", "title": "Chỉ tiêu", "focus": "tong_hop",
               "chart_key": None, "data_key": None}]
    ket_qua = _ensure_detail_tables(slides, ["personnel_breakdown", "equipment_breakdown"])

    assert [s.get("data_key") for s in ket_qua if s["kind"] == "table"] == [
        "personnel_breakdown", "equipment_breakdown"]


def test_bang_chen_truoc_slide_nhan_xet():
    """Số liệu phải đến trước kết luận, không phải sau."""
    from app.agents.nodes.presentation import _ensure_detail_tables

    slides = [
        {"kind": "chart", "title": "Quân số", "focus": "quan_so",
         "chart_key": "personnel", "data_key": None},
        {"kind": "bullet", "title": "Đánh giá", "focus": "tong_hop",
         "chart_key": None, "data_key": None},
        {"kind": "bullet", "title": "Kiến nghị", "focus": "tong_hop",
         "chart_key": None, "data_key": None},
    ]
    assert [s["kind"] for s in _ensure_detail_tables(slides, ["personnel_breakdown"])] == [
        "chart", "table", "bullet", "bullet"]


def test_bang_rong_khong_duoc_chao_len():
    """Kỳ 7/2026 trên ERP thật: chưa trang bị nào, breakdown rỗng.

    Chào bảng rỗng thì bộ slide mọc một slide chỉ có mỗi dòng tiêu đề cột. Chỉ
    tiêu "0 trang bị" vẫn giữ - đó là kết luận thật về kỳ đó, khác với một bảng
    trống không nói gì.
    """
    data = {"equipment": {"metrics": {"total_equipment": {"value": 0, "delta": None,
                                                          "delta_pct": None, "share_pct": None}},
                          "breakdown": []}}
    _, charts, tables = pr._available_data(data)

    assert charts == ["equipment"]          # chỉ tiêu 0 vẫn được nói tới
    assert tables == []                     # nhưng không có bảng rỗng


def test_bang_co_dong_thi_van_duoc_chao():
    data = {"equipment": {"metrics": {"total_equipment": {"value": 194, "delta": None,
                                                          "delta_pct": None, "share_pct": None}},
                          "breakdown": [{"ten_don_vi": "Phòng IT"}]}}
    _, _, tables = pr._available_data(data)
    assert tables == ["equipment_breakdown"]
