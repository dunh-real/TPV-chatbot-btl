"""Workflow 4: tổng hợp báo cáo - số liệu do data tool tính, LLM không được tính."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.nodes import report as rp
from app.documents.charts import compare_bar_chart, status_bar_chart
from app.documents.extract_figures import extract_figures, reconcile_file
from app.tools.data import (
    Metric,
    ToolError,
    call_tool,
    describe_tools,
    previous_ky,
    validate_ky,
)


@pytest.fixture
async def session():
    from app.db.models import Base, DonVi, KiemKeTrangBi, KyKiemKe, VanBan

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            DonVi(ma_don_vi="DV01", ten_don_vi="Đơn vị 1", quan_so=48),
            DonVi(ma_don_vi="DV02", ten_don_vi="Đơn vị 2", quan_so=65),
        ])
        await session.flush()
        # (đơn vị, kỳ, quân số, có mặt, vắng, đi học, nghỉ phép, [(trang bị, sl, tình trạng)])
        rows = [
            ("DV01", "2026-07", 46, 43, 3, 2, 1, [("Máy in", 8, "Tốt")]),
            ("DV01", "2026-08", 48, 44, 4, 3, 1, [("Máy in", 8, "Cần bảo dưỡng")]),
            ("DV02", "2026-07", 64, 59, 5, 3, 2, [("Máy chủ", 6, "Tốt")]),
            ("DV02", "2026-08", 65, 60, 5, 3, 2, [("Máy chủ", 6, "Tốt"),
                                                  ("Xe công vụ", 2, "Hỏng")]),
        ]
        for code, ky, quan_so, co_mat, vang, di_hoc, nghi_phep, equipment in rows:
            kiem_ke = KyKiemKe(ma_don_vi=code, ky=ky, quan_so=quan_so, co_mat=co_mat,
                               vang=vang, di_hoc=di_hoc, nghi_phep=nghi_phep,
                               ngay_kiem_ke=date(2026, int(ky[5:]), 28))
            session.add(kiem_ke)
            await session.flush()
            for ten, so_luong, tinh_trang in equipment:
                session.add(KiemKeTrangBi(kiem_ke_id=kiem_ke.id, ten_trang_bi=ten,
                                          so_luong=so_luong, tinh_trang=tinh_trang))
        session.add(VanBan(ma_van_ban="10/BC-DV02", ten_van_ban="Báo cáo DV02",
                           loai_van_ban="bao_cao_di", noi_gui="Đơn vị 2",
                           noi_nhan="Ban Giám đốc", ngay_van_ban=date(2026, 8, 20),
                           file_path="", dang_file="docx"))
        await session.commit()
        yield session
    await engine.dispose()


# ------------------------------------------------------------ tiện ích ---- #
def test_ky_hop_le():
    assert validate_ky("2026-08") == "2026-08"
    for bad in ["tháng 8", "2026-13", "2026/08", "202608"]:
        with pytest.raises(ToolError):
            validate_ky(bad)


def test_ky_truoc_vat_qua_nam():
    assert previous_ky("2026-08") == "2026-07"
    assert previous_ky("2026-01") == "2025-12"


def test_metric_tinh_san_moi_con_so():
    """LLM không phải tính gì: delta, phần trăm, tỷ trọng đều có sẵn."""
    metric = Metric.build(value=133, prev=130, total=144)
    assert metric.delta == 3
    assert metric.delta_pct == 2.3
    assert metric.share_pct == 92.4


def test_metric_khong_co_ky_truoc():
    metric = Metric.build(value=100)
    assert metric.delta is None and metric.delta_pct is None


# ---------------------------------------------------------- data tool ----- #
async def test_tong_hop_quan_so_nhieu_don_vi(session):
    result = await call_tool(session, "get_personnel_statistics", ky="2026-08")
    metrics = result.as_dict()["metrics"]

    assert metrics["total_personnel"]["value"] == 48 + 65
    assert metrics["total_personnel"]["prev"] == 46 + 64
    assert metrics["total_personnel"]["delta"] == 3
    assert metrics["present"]["share_pct"] == round(104 / 113 * 100, 1)
    assert result.is_consistent
    assert len(result.breakdown) == 2


async def test_gioi_han_pham_vi_don_vi(session):
    result = await call_tool(session, "get_personnel_statistics", ky="2026-08",
                             ma_don_vi=["DV02"])
    assert result.as_dict()["metrics"]["total_personnel"]["value"] == 65
    assert result.scope["units_requested"] == 1


async def test_bat_so_lieu_khong_nhat_quan(session):
    """co_mat + vang != quan_so thì phải báo, không để LLM làm mượt."""
    from sqlalchemy import select

    from app.db.models import KyKiemKe

    row = (await session.execute(
        select(KyKiemKe).where(KyKiemKe.ma_don_vi == "DV01", KyKiemKe.ky == "2026-08")
    )).scalar_one()
    row.co_mat = 40                     # 40 + 4 != 48
    await session.commit()

    result = await call_tool(session, "get_personnel_statistics", ky="2026-08")
    assert not result.is_consistent
    assert "≠ quân số 48" in result.consistency[0].message


async def test_thong_ke_trang_bi(session):
    result = await call_tool(session, "get_equipment_statistics", ky="2026-08")
    metrics = result.as_dict()["metrics"]

    assert metrics["total_equipment"]["value"] == 8 + 6 + 2
    assert metrics["good"]["value"] == 6            # chỉ máy chủ "Tốt"
    assert metrics["needs_attention"]["value"] == 10
    assert metrics["good"]["share_pct"] == 37.5


async def test_biet_don_vi_nao_chua_gui_bao_cao(session):
    status = await call_tool(session, "get_reporting_status", ky="2026-08")

    assert status["units_reported"] == 1
    assert [m["ma_don_vi"] for m in status["missing"]] == ["DV01"]


async def test_chan_tham_so_va_ten_tool_la(session):
    with pytest.raises(ToolError, match="YYYY-MM"):
        await call_tool(session, "get_personnel_statistics", ky="tháng 8")
    with pytest.raises(ToolError, match="không tồn tại"):
        await call_tool(session, "get_personnel_statistics", ky="2026-08", ma_don_vi="DVXX")
    with pytest.raises(ToolError, match="Không có công cụ"):
        await call_tool(session, "drop_table", ky="2026-08")
    with pytest.raises(ToolError, match="tham số không hợp lệ"):
        await call_tool(session, "get_personnel_statistics", ky="2026-08", sql="SELECT 1")


def test_mo_ta_tool_cho_prompt():
    described = describe_tools()
    assert "get_personnel_statistics" in described
    assert "YYYY-MM" in described


# ------------------------------------------------------------ biểu đồ ----- #
def test_ve_bieu_do_so_sanh():
    png = compare_bar_chart("Quân số tháng 8", ["Tổng", "Có mặt", "Vắng"],
                            [144, 133, 11], [140, 130, 10])
    assert png.startswith(b"\x89PNG") and len(png) > 5000


def test_ve_bieu_do_tinh_trang():
    png = status_bar_chart("Trang bị", ["Tốt", "Cần bảo dưỡng", "Hỏng"], [121, 22, 6])
    assert png.startswith(b"\x89PNG")


# ------------------------------------------------- đối chiếu file báo cáo - #
def test_trich_so_tu_bao_cao():
    text = ("I. QUÂN SỐ\nQuân số: 65; Ngày kiểm kê: 30/8/2026.\n"
            "Trong đó có mặt 60, vắng 5.\nII. Đơn vị quản lý 80 đầu trang bị.")
    figures = extract_figures(text)

    assert figures["quan_so"] == 65
    assert figures["co_mat"] == 60 and figures["vang"] == 5
    assert figures["tong_so_trang_bi"] == 80


def test_phat_hien_bao_cao_lech_so_lieu(tmp_path):
    path = tmp_path / "bc.md"
    path.write_text("Quân số: 65 người, có mặt 60.", encoding="utf-8")

    result = reconcile_file("DV02", path, {"quan_so": 66, "co_mat": 60})

    assert result.status == "mismatched"
    assert len(result.discrepancies) == 1
    assert "báo cáo ghi 65" in result.discrepancies[0].message


def test_bao_cao_khop_thi_khong_bao_lech(tmp_path):
    path = tmp_path / "bc.md"
    path.write_text("Quân số: 65 người.", encoding="utf-8")
    assert reconcile_file("DV02", path, {"quan_so": 65}).status == "matched"


def test_khong_co_file_thi_bo_qua():
    assert reconcile_file("DV03", None, {"quan_so": 30}).status == "no_file"
    assert reconcile_file("DV03", "/khong/ton/tai.docx", {}).status == "no_file"


# ------------------------------------------------ toàn bộ workflow 4 ------ #
@pytest.fixture
async def agg_env(tmp_path, monkeypatch, session):
    """Dùng lại CSDL tạm của fixture `session` cho toàn bộ graph."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(rp, "session_scope", _scope)
    monkeypatch.setattr(rp, "get_settings", lambda: _Settings(str(tmp_path)))
    return tmp_path


class _Settings:
    def __init__(self, output_dir: str) -> None:
        self.output_dir = output_dir
        self.utility_model = "test"


class AggLLM:
    """LLM giả; mỗi mục một câu riêng - đúng như model thật chỉ thấy số liệu của mục đó."""

    def __init__(self, narrative: str, equipment_narrative: str | None = None) -> None:
        self.narrative = narrative
        self.equipment_narrative = equipment_narrative or (
            "Toàn cơ quan quản lý 16 đầu trang thiết bị."
        )
        self.narrative_calls = 0

    async def chat_json(self, messages, **kwargs):
        if "trích tham số" in messages[0]["content"]:
            return {"thang": 8, "nam": 2026, "ma_don_vi": [], "so_sanh_thang": None,
                    "noi_dung": ["quan_so", "trang_bi"]}
        self.narrative_calls += 1
        user = messages[1]["content"]
        if "TRANG THIẾT BỊ" in user:
            return {"paragraphs": [self.equipment_narrative]}
        return {"paragraphs": [self.narrative]}


async def test_bao_cao_tong_hop_hoan_chinh(agg_env, monkeypatch):
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số toàn cơ quan là 113 người, tăng 3 người so với kỳ trước."))

    result = await run_aggregate_workflow("Tổng hợp quân số và trang bị tháng 8/2026")

    assert result["params"]["ky"] == "2026-08"
    assert result["params"]["compare_to"] == "2026-07"
    assert result["validation"]["status"] == "passed"
    assert Path(result["output_path"]).exists()

    titles = [s["title"] for s in result["sections"]]
    assert any("GỬI BÁO CÁO" in t for t in titles)     # ai nộp, ai chưa
    assert any("QUÂN SỐ" in t for t in titles)
    assert any("TRANG THIẾT BỊ" in t for t in titles)
    assert any(s["has_chart"] for s in result["sections"])


async def test_llm_tu_tinh_sai_thi_bi_chan(agg_env, monkeypatch):
    """delta thật là 3; model viết "tăng 47 người" -> không truy được về số liệu."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 113 người, tăng 47 người so với kỳ trước."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")

    assert result["validation"]["status"] == "failed"
    assert result["output_path"] == ""              # không xuất file khi số không truy được
    numbers = {n for issue in result["validation"]["issues"] for n in issue["numbers"]}
    assert "47" in numbers


async def test_gioi_han_da_biet_so_nho_trung_ngau_nhien_van_lot(agg_env, monkeypatch):
    """Ghi nhận giới hạn của cách đối chiếu theo tập số.

    delta thật là 3, model viết "tăng 5" - sai, nhưng số 5 lọt vì trùng với
    `training.prev` cũng bằng 5. Van này bắt được số bịa lạ (47, 15.000.000) chứ
    không bắt được số nhỏ trùng ngẫu nhiên, nên vẫn cần người duyệt trước khi
    phát hành văn bản.
    """
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 113 người, tăng 5 người so với kỳ trước."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")
    assert result["validation"]["status"] == "passed"    # lọt - đây là giới hạn đã biết


async def test_ty_le_da_tinh_san_thi_llm_dung_lai_duoc(agg_env, monkeypatch):
    """Tỷ lệ phần trăm do data tool tính sẵn nên LLM dùng lại được, không bị coi là bịa."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Quân số có mặt đạt 104 người, chiếm 92.0% tổng quân số."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")
    assert result["validation"]["status"] == "passed"


async def test_so_lieu_cua_muc_khac_cung_bi_coi_la_bia(agg_env, monkeypatch):
    """Nhận xét quân số đặt ở mục trang thiết bị -> số không thuộc phạm vi mục đó."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 113 người.",
        equipment_narrative="Tổng quân số là 113 người, tăng 3 người.",
    ))

    result = await run_aggregate_workflow("Tổng hợp tháng 8/2026")
    sections_with_issue = {i["section"] for i in result["validation"]["issues"]}
    assert "trang_bi" in sections_with_issue


async def test_don_vi_chua_nop_duoc_neu_ten(agg_env, monkeypatch):
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM("Tổng quân số là 113 người."))
    result = await run_aggregate_workflow("Tổng hợp tháng 8/2026")

    section = next(s for s in result["sections"] if "GỬI BÁO CÁO" in s["title"])
    assert "Đơn vị 1" in section["paragraphs"][0]      # DV01 chưa gửi
    assert "1 đơn vị đã gửi" in section["paragraphs"][0]


async def test_khong_neu_ky_thi_lay_ky_gan_nhat_da_khep(agg_env, monkeypatch):
    from app.agents.graph import run_aggregate_workflow

    class NoPeriodLLM(AggLLM):
        async def chat_json(self, messages, **kwargs):
            if "trích tham số" in messages[0]["content"]:
                return {"thang": None, "nam": None, "ma_don_vi": [], "noi_dung": []}
            return {"paragraphs": [self.narrative]}

    monkeypatch.setattr(rp, "get_llm", lambda: NoPeriodLLM("Không có số liệu."))
    result = await run_aggregate_workflow("Tổng hợp báo cáo")

    assert any("kỳ gần nhất đã khép" in a for a in result["assumptions"])


# ------------------------------------------------- lịch sử hội thoại ------- #
class LichSuLLM(AggLLM):
    """Như AggLLM nhưng giữ lại prompt trích tham số để soi."""

    def __init__(self) -> None:
        super().__init__("Tổng quân số toàn cơ quan là 113 người.")
        self.params_prompt = ""

    async def chat_json(self, messages, **kwargs):
        if "trích tham số" in messages[0]["content"]:
            self.params_prompt = messages[1]["content"]
        return await super().chat_json(messages, **kwargs)


async def test_trich_tham_so_nhin_thay_luot_truoc(agg_env, monkeypatch):
    """"vẫn kỳ đó" chỉ giải được nếu lượt trước đi vào prompt trích tham số."""
    from app.agents.graph import run_aggregate_workflow

    llm = LichSuLLM()
    monkeypatch.setattr(rp, "get_llm", lambda: llm)

    await run_aggregate_workflow(
        "tổng hợp thêm trang bị, vẫn kỳ đó",
        history=[{"role": "user", "content": "tổng hợp quân số tháng 8/2026"},
                 {"role": "assistant", "content": "Đã tổng hợp báo cáo kỳ 2026-08."}],
    )

    assert "tổng hợp quân số tháng 8/2026" in llm.params_prompt
    assert "vẫn kỳ đó" in llm.params_prompt


async def test_khong_truyen_lich_su_thi_prompt_bao_chua_co(agg_env, monkeypatch):
    from app.agents.graph import run_aggregate_workflow

    llm = LichSuLLM()
    monkeypatch.setattr(rp, "get_llm", lambda: llm)

    await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")

    assert "(chưa có)" in llm.params_prompt
