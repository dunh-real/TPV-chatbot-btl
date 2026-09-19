"""Workflow 4: tổng hợp báo cáo - số liệu do data tool tính, LLM không được tính."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents.nodes import report as rp


def date_to_dt(y, m, d):
    from datetime import datetime

    return datetime(y, m, d)
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
async def app_session():
    """CSDL app: chỉ còn sổ văn bản (dùng để biết đơn vị nào đã gửi báo cáo)."""
    from app.db.models import Base, VanBan

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(VanBan(ma_van_ban="10/BC-DV02", ten_van_ban="Báo cáo Đơn vị 2",
                           loai_van_ban="bao_cao_di", noi_gui="Đơn vị 2",
                           noi_nhan="Ban Giám đốc", ngay_van_ban=date(2026, 8, 20),
                           file_path="", dang_file="docx"))
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
async def session(erp_session, app_session, monkeypatch):
    """Phiên ERP để đọc số liệu, kèm sổ văn bản tạm cho `get_reporting_status`.

    Tool đọc sổ văn bản tự mở phiên CSDL app của nó, nên phải trỏ phiên đó về
    CSDL tạm của test thay vì file thật.
    """
    from contextlib import asynccontextmanager

    import app.db.session as db_session

    @asynccontextmanager
    async def _app_scope():
        yield app_session

    monkeypatch.setattr(db_session, "session_scope", _app_scope)
    return erp_session


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

    # Tháng 8: đơn vị 1 còn 2 người (một người nghỉ 15/8), đơn vị 2 có 3.
    assert metrics["total_personnel"]["value"] == 2 + 3
    assert metrics["total_personnel"]["prev"] == 3 + 2
    assert metrics["total_personnel"]["delta"] == 0
    # Một người vào (10/8), một người nghỉ (15/8) -> tổng đứng yên nhưng có biến động.
    assert metrics["new_hires"]["value"] == 1
    assert metrics["resignations"]["value"] == 1
    assert result.is_consistent
    assert len(result.breakdown) == 2


async def test_gioi_han_pham_vi_don_vi(session):
    result = await call_tool(session, "get_personnel_statistics", ky="2026-08",
                             ma_don_vi=["00002"])
    assert result.as_dict()["metrics"]["total_personnel"]["value"] == 3
    assert result.scope["units_requested"] == 1


async def test_bat_quan_so_lech_voi_bien_dong(session, erp_session):
    """Chênh lệch quân số phải giải thích được bằng tuyển mới - nghỉ việc.

    Cho một người nghỉ việc mà không ghi ngày nghỉ: quân số tụt đi một nhưng
    không có biến động nào giải thích, và điều đó phải được báo lên chứ không
    để LLM viết trơn tru qua.
    """
    from app.db.erp_models import EmployeeProfile

    nhan_vien = await erp_session.get(EmployeeProfile, 1)
    nhan_vien.is_deleted = True
    nhan_vien.deletion_time = date_to_dt(2026, 8, 20)
    await erp_session.commit()

    result = await call_tool(session, "get_personnel_statistics", ky="2026-08")
    assert not result.is_consistent
    assert "Chênh lệch" in result.consistency[0].message


async def test_thong_ke_trang_bi(session):
    result = await call_tool(session, "get_equipment_statistics", ky="2026-08")
    metrics = result.as_dict()["metrics"]

    # Máy in 8 + máy chủ 6 + xe công vụ 2 = 16 (máy chiếu đã thanh lý đầu tháng 8).
    assert metrics["total_equipment"]["value"] == 16
    # Chưa khai báo mã trạng thái "tốt" thì hai chỉ tiêu này phải VẮNG MẶT,
    # không được mặc định coi tất cả là tốt.
    assert "good" not in metrics
    assert "needs_attention" not in metrics


async def test_biet_don_vi_nao_chua_gui_bao_cao(session):
    status = await call_tool(session, "get_reporting_status", ky="2026-08")

    assert status["units_reported"] == 1
    assert [m["ma_don_vi"] for m in status["missing"]] == ["00001"]


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
async def agg_env(tmp_path, monkeypatch, session, app_session):
    """Trỏ cả hai nguồn dữ liệu của graph về CSDL tạm: ERP đọc, app ghi."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _erp_scope():
        yield session

    @asynccontextmanager
    async def _app_scope():
        yield app_session

    monkeypatch.setattr(rp, "erp_session_scope", _erp_scope)
    monkeypatch.setattr(rp, "session_scope", _app_scope)
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
        "Tổng quân số toàn cơ quan là 5 người."))

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
    """Quân số thật là 5; model viết "tăng 47 người" -> không truy được về số liệu."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 5 người, tăng 47 người so với kỳ trước."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")

    assert result["validation"]["status"] == "failed"
    assert result["output_path"] == ""              # không xuất file khi số không truy được
    numbers = {n for issue in result["validation"]["issues"] for n in issue["numbers"]}
    assert "47" in numbers


async def test_gioi_han_da_biet_so_nho_trung_ngau_nhien_van_lot(agg_env, monkeypatch):
    """Ghi nhận giới hạn của cách đối chiếu theo tập số.

    Quân số không đổi giữa hai kỳ, model viết "tăng 1 người" - sai, nhưng số 1
    lọt vì trùng với số tuyển mới cũng bằng 1. Van này bắt số bịa lạ (47) chứ
    không bắt được số nhỏ trùng ngẫu nhiên, nên vẫn cần người duyệt trước khi
    phát hành văn bản.
    """
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 5 người, tăng 1 người so với kỳ trước."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")
    assert result["validation"]["status"] == "passed"    # lọt - đây là giới hạn đã biết


async def test_ty_le_da_tinh_san_thi_llm_dung_lai_duoc(agg_env, monkeypatch):
    """Tỷ lệ phần trăm do data tool tính sẵn nên LLM dùng lại được, không bị coi là bịa."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số 5 người, trong kỳ tuyển mới 1 và nghỉ việc 1."))

    result = await run_aggregate_workflow("Tổng hợp quân số tháng 8/2026")
    assert result["validation"]["status"] == "passed"


async def test_so_lieu_cua_muc_khac_cung_bi_coi_la_bia(agg_env, monkeypatch):
    """Nhận xét quân số đặt ở mục trang thiết bị -> số không thuộc phạm vi mục đó."""
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM(
        "Tổng quân số là 5 người.",
        equipment_narrative="Tổng quân số là 5 người, giảm 1 người.",
    ))

    result = await run_aggregate_workflow("Tổng hợp tháng 8/2026")
    sections_with_issue = {i["section"] for i in result["validation"]["issues"]}
    assert "trang_bi" in sections_with_issue


async def test_don_vi_chua_nop_duoc_neu_ten(agg_env, monkeypatch):
    from app.agents.graph import run_aggregate_workflow

    monkeypatch.setattr(rp, "get_llm", lambda: AggLLM("Tổng quân số là 5 người."))
    result = await run_aggregate_workflow("Tổng hợp tháng 8/2026")

    section = next(s for s in result["sections"] if "GỬI BÁO CÁO" in s["title"])
    assert "Đơn vị 1" in section["paragraphs"][0]      # đơn vị 1 chưa gửi
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


# --------------------------------------------------------------------------- #
# Yêu cầu hỏi mảng nào thì báo cáo đúng mảng đó
# --------------------------------------------------------------------------- #
import pytest

from app.agents.nodes.report import scope_from_request

CA_HAI = ["quan_so", "trang_bi"]


@pytest.mark.parametrize("yeu_cau, mong_doi", [
    # LLM trích tham số từng trả về CẢ HAI cho câu này - bộ slide xin về nhân sự
    # mọc thêm biểu đồ trang bị.
    ("Tạo slide báo cáo thông tin nhân viên", ["quan_so"]),
    ("Báo cáo quân số tháng 8/2026", ["quan_so"]),
    ("Thống kê cán bộ, biên chế", ["quan_so"]),
    ("Làm slide báo cáo trang thiết bị", ["trang_bi"]),
    ("Tình hình tài sản, vật tư", ["trang_bi"]),
])
def test_yeu_cau_neu_ro_mot_mang_thi_de_len_lua_chon_cua_llm(yeu_cau, mong_doi):
    assert scope_from_request(yeu_cau, CA_HAI) == mong_doi


@pytest.mark.parametrize("yeu_cau", [
    "Báo cáo tổng hợp tháng 8/2026",       # không nhắc mảng nào
    "Báo cáo quân số và trang thiết bị",   # nhắc cả hai
    "",
])
def test_yeu_cau_khong_khoanh_vung_thi_giu_nguyen_lua_chon_cua_llm(yeu_cau):
    assert scope_from_request(yeu_cau, CA_HAI) == CA_HAI
    assert scope_from_request(yeu_cau, ["trang_bi"]) == ["trang_bi"]


# --------------------------------------------------------------------------- #
# Kỳ đối chiếu
# --------------------------------------------------------------------------- #
from app.agents.nodes.report import _compare_period


def _goi(ky, nam, **params):
    gia_dinh: list[str] = []
    return _compare_period(ky, nam, params, gia_dinh), gia_dinh


def test_khong_neu_gi_thi_lay_ky_lien_truoc():
    assert _goi("2026-08", 2026)[0] == "2026-07"
    assert _goi("2026-01", 2026)[0] == "2025-12"


def test_doi_chieu_khac_nam_khong_con_bi_ep_ve_nam_bao_cao():
    """"tháng 8/2026 so với tháng 8 năm 2025" từng ra 2026-08 - so kỳ với chính nó."""
    compare_to, gia_dinh = _goi("2026-08", 2026, so_sanh_thang=8, so_sanh_nam=2025)

    assert compare_to == "2025-08"
    assert any("2025-08" in g for g in gia_dinh)


def test_neu_thang_khong_neu_nam_thi_hieu_la_cung_nam():
    assert _goi("2026-08", 2026, so_sanh_thang=3)[0] == "2026-03"


def test_ky_doi_chieu_trung_ky_bao_cao_bi_thay_bang_ky_lien_truoc():
    """So với chính mình thì mọi biến động bằng 0 - vô nghĩa mà trông như có nghĩa."""
    compare_to, gia_dinh = _goi("2026-08", 2026, so_sanh_thang=8)

    assert compare_to == "2026-07"
    assert any("trùng kỳ báo cáo" in g for g in gia_dinh)


def test_ky_doi_chieu_nam_sau_ky_bao_cao_bi_tu_choi():
    compare_to, gia_dinh = _goi("2026-08", 2026, so_sanh_thang=11)

    assert compare_to == "2026-07"
    assert any("nằm sau kỳ báo cáo" in g for g in gia_dinh)


@pytest.mark.parametrize("xau", [0, 13, "abc", "", -1])
def test_thang_doi_chieu_rac_thi_lui_ve_ky_lien_truoc(xau):
    compare_to, gia_dinh = _goi("2026-08", 2026, so_sanh_thang=xau)
    assert compare_to == "2026-07"
    if xau:
        assert gia_dinh                      # phải nói ra, không im lặng


# --------------------------------------------------------------------------- #
# Đánh số đầu mục văn bản
# --------------------------------------------------------------------------- #
from app.agents.nodes.report import _period_numbers, so_la_ma


def test_danh_so_dau_muc_la_so_la_ma_that():
    """Trước đây dùng "I" * n: đúng ba mục đầu, từ mục thứ tư thành "IIII"."""
    assert [so_la_ma(i) for i in range(1, 8)] == [
        "I", "II", "III", "IV", "V", "VI", "VII"]


def test_danh_so_ngoai_khoang_thi_tra_so_thuong():
    assert so_la_ma(0) == "0"
    assert so_la_ma(40) == "40"


# --------------------------------------------------------------------------- #
# Van chắn số: hai ca từng chặn oan báo cáo đúng
# --------------------------------------------------------------------------- #
def test_ty_le_tron_so_khong_bi_coi_la_so_bia():
    """delta_pct=75.0 -> "750" theo quy tắc dấu chấm ngăn nghìn, còn văn bản viết
    "75%". Báo cáo đúng bị `validation=failed` và không xuất được file."""
    from app.documents.verify import check_numbers, collect_known_numbers

    known = collect_known_numbers(
        {"metrics": {"m": {"value": 28, "delta": 12, "delta_pct": 75.0}}})

    assert check_numbers("Tăng 12 người, tương đương 75%.", known).ok
    assert check_numbers("Tăng 75,0% so với kỳ trước.", known).ok
    # Vẫn phải chặn số thật sự không có trong dữ liệu.
    assert not check_numbers("Tăng 80%.", known).ok


def test_ky_doi_chieu_nam_trong_tap_so_hop_le():
    """Không có thì câu "so với tháng 8/2025" bị loại - đúng câu người đọc cần nhất."""
    numbers = _period_numbers({"thang": 8, "nam": 2026, "compare_to": "2025-08"})

    assert {"8", "2026", "2025"} <= numbers


def test_van_chan_so_van_chan_ky_doi_chieu_sai():
    """Nới cho kỳ đối chiếu ĐÚNG, không nới cho mọi tháng."""
    from app.documents.verify import check_numbers, collect_known_numbers

    params = {"thang": 8, "nam": 2026, "compare_to": "2025-08"}
    known = collect_known_numbers({"m": {"value": 28, "delta": 12}}) | _period_numbers(params)

    assert check_numbers("Tháng 8/2026 đạt 28, so với tháng 8/2025 tăng 12.", known).ok
    assert not check_numbers("So với tháng 7/2026 tăng 12.", known).ok


def test_muc_trang_thiet_bi_co_bang_chi_tiet():
    """Trước đây `"table": None` cứng: báo cáo ghi "tổng 194 trang bị" mà không
    dòng nào nói 194 đó nằm ở đơn vị nào. Mục quân số thì luôn có bảng."""
    import asyncio

    from app.agents.nodes import report as rp

    data = {
        "equipment": {
            "metrics": {"total_equipment": {"value": 194, "prev": None, "delta": None,
                                            "delta_pct": None, "share_pct": None}},
            "scope": {"nguon": "Asm_Assets"},
            "breakdown": [
                {"ten_don_vi": "Phòng IT", "ten_trang_bi": "Máy in",
                 "so_luong": 5, "tinh_trang": "Đang dùng"},
                {"ten_don_vi": "Tạp vụ", "ten_trang_bi": "Tủ kính hồ sơ",
                 "so_luong": 4, "tinh_trang": "Đang dùng"},
            ],
        },
    }
    params = {"ky": "2026-08", "thang": 8, "nam": 2026, "compare_to": "2026-07"}

    async def khong_goi_llm(*args, **kwargs):
        return [], [], []

    goc = rp._narrative
    rp._narrative = khong_goi_llm  # type: ignore[assignment]
    try:
        ket_qua = asyncio.run(rp.render_node({"data": data, "params": params, "charts": {}}))
    finally:
        rp._narrative = goc  # type: ignore[assignment]

    muc = next(s for s in ket_qua["sections"] if s["id"] == "trang_bi")
    assert muc["table"] is not None, "mục trang thiết bị không có bảng chi tiết"
    assert muc["table"]["columns"] == ["Đơn vị", "Trang bị", "Số lượng", "Tình trạng"]
    assert len(muc["table"]["rows"]) == 2
    assert muc["table"]["rows"][0][:2] == ["Phòng IT", "Máy in"]
