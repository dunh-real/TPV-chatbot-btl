"""Tầng tool + định tuyến ý định + agent tổng, chạy không cần GPU/Qdrant/vLLM.

Trọng tâm là các ràng buộc mà tầng tool sinh ra để tồn tại: tên tool và tham số
phải có thật, file chỉ đọc được trong vùng cho phép, và không workflow nào chạy khi
thiếu đầu vào bắt buộc.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import date

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import graph as graph_mod
from app.agents import router as router_mod
from app.services import storage
from app.services.cache import CacheService
from app.services.conversation import ConversationMemory, ConversationStore
from app.services.llm import LLMError
from app.tools import document as document_tools
from app.tools import presentation as presentation_tools
from app.tools import registry
from app.tools import templates as template_tools
from app.tools.base import ToolError


class _Settings:
    """Chỉ những trường mà tầng lưu trữ đọc tới."""

    def __init__(self, base) -> None:
        self.upload_dir = str(base / "uploads")
        self.output_dir = str(base / "output")


@pytest.fixture
def tmp_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "get_settings", lambda: _Settings(tmp_path))
    return tmp_path


@pytest.fixture
async def session():
    from app.db.models import Base, DonVi, KiemKeTrangBi, KyKiemKe, TemplateBaoCao

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
            DonVi(ma_don_vi="DV01", ten_don_vi="Đơn vị 1", quan_so=48),
            DonVi(ma_don_vi="DV02", ten_don_vi="Đơn vị 2", quan_so=65),
            TemplateBaoCao(
                ma_template="BC_TAINGUYEN",
                ten_bao_cao="Báo cáo tài nguyên đơn vị",
                loai_bao_cao="bao_cao_tai_nguyen",
                mo_ta="Quân số và trang thiết bị của một đơn vị",
                file_path="",
                truong_du_lieu=json.dumps({
                    "meta": {"nguoi_ky": {"source": "input"},
                             "chuc_vu_ky": {"source": "input"},
                             "ngay_bao_cao": {"source": "today"}},
                    "sections": [{"id": "quan_so", "title": "I. QUÂN SỐ", "type": "data"}],
                }, ensure_ascii=False),
            ),
            TemplateBaoCao(
                ma_template="BC_TONGHOP",
                ten_bao_cao="Báo cáo tổng hợp",
                loai_bao_cao="bao_cao_tong_hop",
                mo_ta="Tổng hợp nhiều đơn vị",
                file_path="",
                truong_du_lieu="{}",
            ),
        ])
        await session.flush()
        rows = [
            ("DV01", "2026-07", 46, 43, 3, 2, 1, [("Máy in", 8, "Tốt")]),
            ("DV01", "2026-08", 48, 44, 4, 3, 1, [("Máy in", 8, "Cần bảo dưỡng")]),
            ("DV02", "2026-08", 65, 60, 5, 3, 2, [("Máy chủ", 6, "Tốt")]),
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
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def db(session, monkeypatch):
    """Trỏ mọi tool đang mở phiên CSDL về phiên sqlite trong bộ nhớ."""

    @asynccontextmanager
    async def _scope():
        yield session

    for module in (registry, template_tools):
        monkeypatch.setattr(module, "session_scope", _scope)
    return session


class FakeLLM:
    def __init__(self, json_response=None, fail=False):
        self.json_response = json_response or {}
        self.fail = fail
        self.calls: list[list[dict]] = []

    async def chat_json(self, messages, **kwargs):
        self.calls.append(messages)
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        return self.json_response


# --------------------------------------------------------------------------- #
# Lưu trữ: ranh giới chặn đọc file ngoài vùng cho phép
# --------------------------------------------------------------------------- #
def test_resolve_chan_duong_dan_vuot_ra_ngoai(tmp_storage):
    with pytest.raises(storage.StorageError):
        storage.resolve("../../../etc/passwd")

    outsider = tmp_storage / "bi_mat.docx"
    outsider.write_text("x")
    with pytest.raises(storage.StorageError, match="ngoài thư mục"):
        storage.resolve(str(outsider))


def test_resolve_nhan_ca_ba_dang_dinh_danh(tmp_storage):
    ref = storage.save_upload(b"noi dung", "ban_thao.docx")
    assert ref.file_id == "upload:ban_thao.docx"

    for dinh_danh in ("upload:ban_thao.docx", "ban_thao.docx", str(ref.path)):
        assert storage.resolve(dinh_danh).path == ref.path


def test_save_upload_lam_sach_ten_file(tmp_storage):
    ref = storage.save_upload(b"x", "../../công văn khẩn.docx")
    assert "/" not in ref.name and ".." not in ref.name
    assert ref.path.parent == storage.root("upload")


def test_resolve_bao_thieu_khi_khong_co_file(tmp_storage):
    with pytest.raises(storage.StorageError, match="Không tìm thấy"):
        storage.resolve("khong_ton_tai.docx")


# --------------------------------------------------------------------------- #
# Registry: tên tool và tham số phải có thật
# --------------------------------------------------------------------------- #
async def test_tool_khong_ton_tai_bi_chan():
    with pytest.raises(ToolError, match="Không có công cụ"):
        await registry.call_tool("drop_table", ky="2026-08")


async def test_tham_so_la_bi_chan():
    with pytest.raises(ToolError, match="tham số không hợp lệ"):
        await registry.call_tool("search_documents", query="x", sql="SELECT 1")


async def test_bi_danh_khoang_thoi_gian_quy_ve_ky(db):
    """Người dùng nói "từ tháng 7 đến tháng 8", tool vẫn nhận đúng kỳ và kỳ đối chiếu."""
    theo_bi_danh = await registry.call_tool(
        "get_personnel_statistics", start_date="2026-07", end_date="2026-08", unit="DV01"
    )
    theo_ten_that = await registry.call_tool(
        "get_personnel_statistics", ky="2026-08", compare_to="2026-07", ma_don_vi="DV01"
    )
    assert theo_bi_danh["period"] == "2026-08"
    assert theo_bi_danh["compare_to"] == "2026-07"
    assert theo_bi_danh["scope"]["units_requested"] == 1
    assert theo_bi_danh["metrics"] == theo_ten_that["metrics"]


async def test_ket_qua_la_du_lieu_thuan(db):
    """Registry trả dict để mọi con số nhúng vào prompt đều đối chiếu ngược được."""
    result = await registry.call_tool("get_personnel_statistics", ky="2026-08")
    assert isinstance(result, dict)
    assert result["metrics"]["total_personnel"]["value"] == 113


async def test_ten_that_thang_bi_danh(db):
    result = await registry.call_tool(
        "get_personnel_statistics", ky="2026-08", end_date="2026-07"
    )
    assert result["period"] == "2026-08"


# --------------------------------------------------------------------------- #
# Tra mẫu báo cáo
# --------------------------------------------------------------------------- #
async def test_get_template_tra_duoc_theo_ma_loai_va_ten(db):
    for needle in ("BC_TAINGUYEN", "bao_cao_tai_nguyen", "Báo cáo tài nguyên đơn vị",
                   "trang thiết bị"):
        result = await template_tools.get_template(needle)
        assert result["found"], needle
        assert result["ma_template"] == "BC_TAINGUYEN"


async def test_get_template_hut_thi_tra_ve_ung_vien(db):
    result = await template_tools.get_template("báo cáo tài chính quý")
    assert result["found"] is False
    assert {t["ma_template"] for t in result["candidates"]} == {"BC_TAINGUYEN", "BC_TONGHOP"}


async def test_required_inputs_liet_ke_truong_phai_hoi_nguoi_dung(db):
    """Trường `source: input` là thứ không suy ra được từ đâu - agent phải hỏi."""
    result = await template_tools.get_template("BC_TAINGUYEN")
    assert result["required_inputs"] == ["chuc_vu_ky", "nguoi_ky"]


# --------------------------------------------------------------------------- #
# Sinh file: code dựng, nội dung là thứ được đưa
# --------------------------------------------------------------------------- #
async def test_generate_docx_sinh_file_that(tmp_storage, db):
    result = await document_tools.generate_docx(
        template_id="BC_TAINGUYEN",
        content={
            "meta": {"noi_gui": "ĐƠN VỊ 1", "so_ky_hieu": "01/BC-DV01"},
            "sections": [
                {"id": "quan_so", "title": "I. QUÂN SỐ", "paragraphs": ["Quân số: 48."]},
                {"id": "trang_bi", "title": "II. TRANG BỊ",
                 "table": {"columns": ["Tên", "Số lượng"], "rows": [["Máy in", "8"]]}},
            ],
        },
    )
    assert result["section_count"] == 2
    path = storage.resolve(result["file_id"]).path
    assert path.is_file() and path.stat().st_size > 0


async def test_generate_docx_dien_ngay_nhung_khong_bia_nguoi_ky(tmp_storage, db):
    """Ngày tháng là cơ học nên điền hộ; người ký thì không có nguồn nào để suy."""
    from app.documents.docx_builder import DocumentPayload

    captured: dict = {}

    def _fake_build(payload: DocumentPayload, output_path, template_path=None):
        captured["meta"] = payload.meta
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"docx")
        return output_path

    import app.tools.document as mod

    original = mod.build_docx
    mod.build_docx = _fake_build
    try:
        await document_tools.generate_docx(
            template_id="", content={"sections": [{"title": "I", "paragraphs": ["x"]}]}
        )
    finally:
        mod.build_docx = original

    assert captured["meta"]["ngay_bao_cao"].startswith("ngày")
    assert "nguoi_ky" not in captured["meta"]


async def test_generate_docx_chan_noi_dung_hong(tmp_storage, db):
    with pytest.raises(ToolError, match="sections"):
        await document_tools.generate_docx(template_id="", content={"meta": {}})

    with pytest.raises(ToolError, match="columns/rows"):
        await document_tools.generate_docx(
            template_id="",
            content={"sections": [{"id": "s1", "table": {"cot": ["a"]}}]},
        )


async def test_generate_docx_tu_choi_mau_khong_co(tmp_storage, db):
    with pytest.raises(ToolError, match="BC_TAINGUYEN"):
        await document_tools.generate_docx(
            template_id="BC_KHONG_TON_TAI",
            content={"sections": [{"title": "I", "paragraphs": ["x"]}]},
        )


async def test_generate_presentation_sinh_file_that(tmp_storage, db):
    result = await presentation_tools.generate_presentation(
        data={
            "title": "Báo cáo tháng 8",
            "slides": [
                {"kind": "title", "title": "Báo cáo tháng 8"},
                {"kind": "bullet", "title": "Đánh giá", "bullets": ["Quân số ổn định"]},
            ],
        },
    )
    assert result["slide_count"] == 2
    assert storage.resolve(result["file_id"]).path.is_file()


async def test_slide_bieu_do_thieu_anh_thi_bi_chan(tmp_storage, db):
    """Slide chart không có PNG chỉ còn cái tiêu đề - phải báo lỗi, không dựng."""
    with pytest.raises(ToolError, match="cần biểu đồ"):
        await presentation_tools.generate_presentation(
            data={"slides": [{"kind": "chart", "title": "Quân số", "chart_key": "personnel"}]},
        )


async def test_slide_kieu_la_bi_chan(tmp_storage, db):
    with pytest.raises(ToolError, match="không hợp lệ"):
        await presentation_tools.generate_presentation(
            data={"slides": [{"kind": "video", "title": "x"}]},
        )


# --------------------------------------------------------------------------- #
# Định tuyến ý định
# --------------------------------------------------------------------------- #
async def test_khong_co_file_thi_khong_bao_gio_di_nhanh_document(monkeypatch):
    """Ràng buộc cứng: workflow 2 cần file có thật, prompt nói không đủ."""
    monkeypatch.setattr(router_mod, "get_llm",
                        lambda: FakeLLM({"intent": "document", "confidence": 0.95}))
    result = await router_mod.classify_intent("kiểm tra thể thức văn bản này", has_file=False)
    assert result.intent != "document"


async def test_co_file_thi_ton_trong_y_dinh_document(monkeypatch):
    monkeypatch.setattr(router_mod, "get_llm",
                        lambda: FakeLLM({"intent": "document", "confidence": 0.95}))
    result = await router_mod.classify_intent("kiểm tra thể thức văn bản này", has_file=True)
    assert result.intent == "document" and result.source == "llm"


async def test_llm_hong_thi_van_dinh_tuyen_duoc(monkeypatch):
    monkeypatch.setattr(router_mod, "get_llm", lambda: FakeLLM(fail=True))
    result = await router_mod.classify_intent("làm slide báo cáo tháng 8 cho giao ban")
    assert result.intent == "presentation" and result.source == "keyword"


async def test_y_dinh_la_thi_lui_ve_tu_khoa(monkeypatch):
    monkeypatch.setattr(router_mod, "get_llm",
                        lambda: FakeLLM({"intent": "xoa_du_lieu", "confidence": 0.99}))
    result = await router_mod.classify_intent("tổng hợp quân số toàn cơ quan tháng 8")
    assert result.intent == "report" and result.source == "keyword"


async def test_llm_tu_tin_thap_thi_thua_tu_khoa(monkeypatch):
    monkeypatch.setattr(router_mod, "get_llm",
                        lambda: FakeLLM({"intent": "qa", "confidence": 0.2}))
    result = await router_mod.classify_intent("làm slide trình chiếu báo cáo tháng 8")
    assert result.intent == "presentation"


def test_tu_khoa_phan_biet_slide_soan_va_tong_hop():
    assert router_mod.classify_by_keywords("soạn báo cáo cho DV01").intent == "draft"
    assert router_mod.classify_by_keywords("tổng hợp quân số các đơn vị").intent == "report"
    assert router_mod.classify_by_keywords("làm slide giao ban").intent == "presentation"
    assert router_mod.classify_by_keywords("quy định nghỉ phép thế nào").intent == "qa"


# --------------------------------------------------------------------------- #
# Agent tổng
# --------------------------------------------------------------------------- #
@pytest.fixture
def agent_env(monkeypatch):
    """Cache trong tiến trình + Qdrant nhúng: đủ để kiểm tra cả hai tầng bộ nhớ."""
    from qdrant_client import AsyncQdrantClient

    from app.core.config import Settings
    from app.rag import embedding as embedding_mod

    cfg = Settings()

    def _fake_embed(texts):
        """Vector giả nhưng đúng chiều - test không được đụng GPU."""
        from app.rag.embedding import HybridEmbedding, SparseEmbedding

        return [HybridEmbedding(dense=[float(len(t) % 7) + 1.0] * cfg.dense_vector_size,
                                lexical=SparseEmbedding(indices=[], values=[]),
                                bm25=SparseEmbedding(indices=[], values=[]))
                for t in texts]

    monkeypatch.setattr(embedding_mod, "embed_documents", _fake_embed)

    cache = CacheService()
    cache._degraded = True
    store = ConversationStore(cfg, client=AsyncQdrantClient(":memory:"))
    memory = ConversationMemory(cache=cache, store=store)
    monkeypatch.setattr(graph_mod, "get_memory", lambda: memory)
    return memory


def _route(intent: str, confidence: float = 0.9, **extra):
    async def _classify(request, has_file=False, history=None):
        return router_mod.IntentResult(intent=intent, confidence=confidence, **extra)

    return _classify


async def test_agent_giao_viec_cho_dung_workflow_va_gom_file(agent_env, monkeypatch, tmp_path):
    monkeypatch.setattr(graph_mod, "classify_intent", _route("report"))

    output = tmp_path / "BC_TONGHOP_2026-08.docx"
    output.write_bytes(b"docx")

    async def _aggregate(request, inputs=None, history=None):
        return {"params": {"ky": "2026-08"}, "output_path": str(output),
                "validation": {"status": "passed"}, "assumptions": [],
                "data": {"reporting": {"units_total": 2, "units_reported": 1,
                                       "missing": [{"ten_don_vi": "Đơn vị 1"}]}}}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8")

    assert result["intent"] == "report"
    assert "2026-08" in result["answer"]
    assert "Đơn vị 1" in result["answer"]           # nêu đơn vị chưa gửi
    assert result["artifacts"][0]["file_name"] == output.name
    assert result["artifacts"][0]["kind"] == "docx"

    history = await agent_env.get_history(result["conversation_id"])
    assert [turn["role"] for turn in history] == ["user", "assistant"]


async def test_agent_hoi_lai_khi_thieu_don_vi(agent_env, monkeypatch):
    monkeypatch.setattr(graph_mod, "classify_intent", _route("draft"))

    async def _draft(request, ma_don_vi=None, inputs=None, history=None):
        return {"missing_input": ["ma_don_vi"], "output_path": "", "params": {}}

    monkeypatch.setattr(graph_mod, "run_draft_workflow", _draft)

    result = await graph_mod.run_agent("soạn báo cáo tài nguyên tháng 8")
    assert result["missing_input"] == ["ma_don_vi"]
    assert "đơn vị nào" in result["answer"]
    assert result["artifacts"] == []


async def test_agent_mo_ho_thi_hoi_lai_chu_khong_chay_workflow(agent_env, monkeypatch):
    monkeypatch.setattr(graph_mod, "classify_intent",
                        _route("report", confidence=0.2, clarify="Bạn muốn tổng hợp kỳ nào?"))

    async def _khong_duoc_goi(*args, **kwargs):
        raise AssertionError("Yêu cầu mơ hồ mà vẫn chạy workflow")

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _khong_duoc_goi)

    result = await graph_mod.run_agent("báo cáo")
    assert result["answer"] == "Bạn muốn tổng hợp kỳ nào?"
    assert result["artifacts"] == []


async def test_model_da_chac_y_dinh_thi_lam_viec_du_co_kem_cau_hoi(agent_env, monkeypatch):
    """Model hay kèm sẵn một câu hỏi lịch sự - cứ thấy là dừng thì agent không làm gì."""
    monkeypatch.setattr(graph_mod, "classify_intent",
                        _route("report", confidence=0.95, clarify="Bạn cần thêm gì không?"))

    goi = []

    async def _aggregate(request, inputs=None, history=None):
        goi.append(request)
        return {"params": {"ky": "2026-08"}, "output_path": "", "data": {}}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8")
    assert goi, "Model đã chắc ý định mà workflow vẫn không chạy"
    assert result["intent"] == "report"


async def test_agent_bao_loi_khi_file_khong_doc_duoc(agent_env, monkeypatch, tmp_storage):
    monkeypatch.setattr(graph_mod, "classify_intent", _route("document"))

    result = await graph_mod.run_agent("soát văn bản này", file_id="khong_co.docx")
    assert result["error"]
    assert "Không xử lý được văn bản" in result["answer"]


# ------------------------------------------------- lịch sử hội thoại ------- #
@pytest.mark.parametrize(
    "intent, ten_workflow",
    [("draft", "run_draft_workflow"),
     ("report", "run_aggregate_workflow"),
     ("presentation", "run_presentation_workflow")],
)
async def test_ba_nhanh_con_lai_cung_nhan_duoc_lich_su(
    agent_env, monkeypatch, intent, ten_workflow
):
    """Trước đây chỉ qa/agent/router đọc lịch sử; ba nhánh này nhận đúng chuỗi request."""
    monkeypatch.setattr(graph_mod, "classify_intent", _route(intent))
    await agent_env.append_turns("hoi-thoai-1", [
        ("user", "soạn báo cáo tài nguyên DV01 tháng 8"),
        ("assistant", "Đã soạn báo cáo tài nguyên DV01.")])

    nhan_duoc: dict[str, object] = {}

    async def _workflow(request, *args, history=None, **kwargs):
        nhan_duoc["history"] = history
        return {"params": {}, "output_path": "", "data": {}, "slide_count": 3}

    monkeypatch.setattr(graph_mod, ten_workflow, _workflow)

    await graph_mod.run_agent("làm luôn slide từ số liệu đó", conversation_id="hoi-thoai-1")

    turns = nhan_duoc["history"]
    assert turns, f"{ten_workflow} không nhận được lịch sử"
    assert turns[0]["content"] == "soạn báo cáo tài nguyên DV01 tháng 8"
