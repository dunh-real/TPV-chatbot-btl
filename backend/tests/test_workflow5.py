"""Workflow 5: tạo slide - LLM sinh JSON trung gian, code dựng file."""

from __future__ import annotations

from datetime import date
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


def test_bang_qua_dai_bi_cat_va_noi_ro(tmp_path):
    rows = [[f"Đơn vị {i}", str(i)] for i in range(20)]
    deck = DeckSpec(title="x", slides=[
        SlideSpec(kind="table", title="Bảng dài",
                  table=SlideTable(columns=["Đơn vị", "Quân số"], rows=rows))])

    output = build_pptx(deck, tmp_path / "deck.pptx")
    slide = Presentation(str(output)).slides[0]
    table = next(shape.table for shape in slide.shapes if shape.has_table)

    assert len(table.rows) <= 9                   # 8 dòng + tiêu đề
    texts = " ".join(shape.text_frame.text for shape in slide.shapes if shape.has_text_frame)
    assert "còn 12 dòng" in texts


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
    kinds = [s["kind"] for s in result["outline"]["slides"]]

    assert kinds == ["title", "chart"]
    assert all(k in SLIDE_KINDS for k in kinds)


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
async def ppt_env(tmp_path, monkeypatch):
    """CSDL tạm + thư mục output tạm cho cả graph."""
    from contextlib import asynccontextmanager

    from app.db.models import Base, DonVi, KiemKeTrangBi, KyKiemKe

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all([DonVi(ma_don_vi="DV01", ten_don_vi="Đơn vị 1", quan_so=48),
                         DonVi(ma_don_vi="DV02", ten_don_vi="Đơn vị 2", quan_so=65)])
        await session.flush()
        for code, ky, quan_so, co_mat, vang in [
            ("DV01", "2026-07", 46, 43, 3), ("DV01", "2026-08", 48, 44, 4),
            ("DV02", "2026-07", 64, 59, 5), ("DV02", "2026-08", 65, 60, 5),
        ]:
            kiem_ke = KyKiemKe(ma_don_vi=code, ky=ky, quan_so=quan_so, co_mat=co_mat,
                               vang=vang, di_hoc=vang - 1, nghi_phep=1,
                               ngay_kiem_ke=date(2026, int(ky[5:]), 28))
            session.add(kiem_ke)
            await session.flush()
            session.add(KiemKeTrangBi(kiem_ke_id=kiem_ke.id, ten_trang_bi="Máy chủ",
                                      so_luong=6, tinh_trang="Tốt"))
        await session.commit()

    session = factory()

    @asynccontextmanager
    async def _scope():
        yield session

    class _Settings:
        output_dir = str(tmp_path)
        utility_model = "test"

    monkeypatch.setattr(rp, "session_scope", _scope)
    monkeypatch.setattr(rp, "get_settings", lambda: _Settings())
    monkeypatch.setattr(pr, "get_settings", lambda: _Settings())
    yield tmp_path
    await session.close()
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

    llm = DeckLLM(["Tổng quân số 113 người", "Tăng 3 người so với kỳ trước"])
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

    llm = DeckLLM(["Tổng quân số 113 người", "Đề nghị bổ sung 47 biên chế"])
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
    assert "113" in texts


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
