"""Tầng tool + định tuyến ý định + agent tổng, chạy không cần GPU/Qdrant/vLLM.

Trọng tâm là các ràng buộc mà tầng tool sinh ra để tồn tại: tên tool và tham số
phải có thật, file chỉ đọc được trong vùng cho phép, và không workflow nào chạy khi
thiếu đầu vào bắt buộc.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agents import graph as graph_mod
from app.agents import planner as planner_mod
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
    """CSDL app: chỉ còn mẫu báo cáo (số liệu nghiệp vụ đã chuyển sang ERP)."""
    from app.db.models import Base, TemplateBaoCao

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all([
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
        await session.commit()
        yield session
    await engine.dispose()


@pytest.fixture
def db(session, erp_session, monkeypatch):
    """Trỏ hai nguồn của tool về CSDL tạm: mẫu báo cáo ở app, số liệu ở ERP."""

    @asynccontextmanager
    async def _scope():
        yield session

    @asynccontextmanager
    async def _erp_scope():
        yield erp_session

    for module in (registry, template_tools):
        monkeypatch.setattr(module, "session_scope", _scope, raising=False)
    monkeypatch.setattr(registry, "erp_session_scope", _erp_scope)
    import app.db.session as db_session

    monkeypatch.setattr(db_session, "session_scope", _scope)
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
        "get_personnel_statistics", start_date="2026-07", end_date="2026-08", unit="00001"
    )
    theo_ten_that = await registry.call_tool(
        "get_personnel_statistics", ky="2026-08", compare_to="2026-07", ma_don_vi="00001"
    )
    assert theo_bi_danh["period"] == "2026-08"
    assert theo_bi_danh["compare_to"] == "2026-07"
    assert theo_bi_danh["scope"]["units_requested"] == 1
    assert theo_bi_danh["metrics"] == theo_ten_that["metrics"]


async def test_ket_qua_la_du_lieu_thuan(db):
    """Registry trả dict để mọi con số nhúng vào prompt đều đối chiếu ngược được."""
    result = await registry.call_tool("get_personnel_statistics", ky="2026-08")
    assert isinstance(result, dict)
    assert result["metrics"]["total_personnel"]["value"] == 5


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


def _plan(*intents: str, confidence: float = 0.9, clarify: str = "",
          depends: dict[str, list[str]] | None = None, requests: dict[str, str] | None = None):
    """Kế hoạch dựng sẵn - thay cho việc gọi LLM ở tầng lập kế hoạch."""

    async def _make(request, has_file=False, history=None):
        steps = [
            planner_mod.PlanStep(
                id=f"s{i}", intent=intent,
                request=(requests or {}).get(f"s{i}", request),
                depends_on=list((depends or {}).get(f"s{i}", [])),
            )
            for i, intent in enumerate(intents, start=1)
        ]
        return planner_mod.Plan(steps=steps, confidence=confidence, clarify=clarify,
                                source="llm")

    return _make


async def test_agent_giao_viec_cho_dung_workflow_va_gom_file(agent_env, monkeypatch, tmp_path):
    monkeypatch.setattr(graph_mod, "make_plan", _plan("report"))

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
    monkeypatch.setattr(graph_mod, "make_plan", _plan("draft"))

    async def _draft(request, ma_don_vi=None, inputs=None, history=None):
        return {"missing_input": ["ma_don_vi"], "output_path": "", "params": {}}

    monkeypatch.setattr(graph_mod, "run_draft_workflow", _draft)

    result = await graph_mod.run_agent("soạn báo cáo tài nguyên tháng 8")
    assert result["missing_input"] == ["ma_don_vi"]
    assert "đơn vị nào" in result["answer"]
    assert result["artifacts"] == []


async def test_agent_mo_ho_thi_hoi_lai_chu_khong_chay_workflow(agent_env, monkeypatch):
    monkeypatch.setattr(graph_mod, "make_plan",
                        _plan("report", confidence=0.2, clarify="Bạn muốn tổng hợp kỳ nào?"))

    async def _khong_duoc_goi(*args, **kwargs):
        raise AssertionError("Yêu cầu mơ hồ mà vẫn chạy workflow")

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _khong_duoc_goi)

    result = await graph_mod.run_agent("báo cáo")
    assert result["answer"] == "Bạn muốn tổng hợp kỳ nào?"
    assert result["artifacts"] == []


async def test_model_da_chac_y_dinh_thi_lam_viec_du_co_kem_cau_hoi(agent_env, monkeypatch):
    """Model hay kèm sẵn một câu hỏi lịch sự - cứ thấy là dừng thì agent không làm gì."""
    monkeypatch.setattr(graph_mod, "make_plan",
                        _plan("report", confidence=0.95, clarify="Bạn cần thêm gì không?"))

    goi = []

    async def _aggregate(request, inputs=None, history=None):
        goi.append(request)
        return {"params": {"ky": "2026-08"}, "output_path": "", "data": {}}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8")
    assert goi, "Model đã chắc ý định mà workflow vẫn không chạy"
    assert result["intent"] == "report"


async def test_agent_bao_loi_khi_file_khong_doc_duoc(agent_env, monkeypatch, tmp_storage):
    monkeypatch.setattr(graph_mod, "make_plan", _plan("document"))

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
    monkeypatch.setattr(graph_mod, "make_plan", _plan(intent))
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


# --------------------------------------------------------------------------- #
# Lập kế hoạch: phân rã yêu cầu thành nhiều bước
# --------------------------------------------------------------------------- #
class FakePlannerLLM:
    """LLM chỉ dùng cho tầng lập kế hoạch - trả về đúng JSON kế hoạch."""

    def __init__(self, payload, fail=False) -> None:
        self.payload = payload
        self.fail = fail

    async def chat_json(self, messages, **kwargs):
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        return self.payload


async def test_ke_hoach_tach_duoc_hai_san_pham(monkeypatch):
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({
        "steps": [
            {"intent": "report", "request": "tổng hợp quân số toàn cơ quan tháng 8/2026"},
            {"intent": "presentation", "request": "làm slide từ báo cáo tháng 8/2026",
             "depends_on": ["s1"]},
        ],
        "confidence": 0.9,
    }))

    plan = await planner_mod.make_plan("tổng hợp quân số tháng 8 rồi làm slide")

    assert [s.intent for s in plan.steps] == ["report", "presentation"]
    assert plan.steps[1].depends_on == ["s1"]
    # Phụ thuộc -> hai đợt, không chạy song song được.
    assert [[s.id for s in wave] for wave in plan.waves()] == [["s1"], ["s2"]]


async def test_hai_buoc_doc_lap_nam_chung_mot_dot(monkeypatch):
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({
        "steps": [
            {"intent": "agent", "request": "quân số DV01 tháng 8/2026"},
            {"intent": "qa", "request": "quy định về thời hạn gửi báo cáo"},
        ],
        "confidence": 0.9,
    }))

    plan = await planner_mod.make_plan("quân số DV01 tháng 8 và thời hạn gửi báo cáo")
    assert [[s.id for s in wave] for wave in plan.waves()] == [["s1", "s2"]]


async def test_ke_hoach_bo_buoc_document_khi_khong_co_file(monkeypatch):
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({
        "steps": [{"intent": "document", "request": "soát văn bản này"},
                  {"intent": "qa", "request": "quy định thể thức gồm những gì"}],
        "confidence": 0.9,
    }))

    plan = await planner_mod.make_plan("soát văn bản rồi cho biết quy định", has_file=False)
    assert [s.intent for s in plan.steps] == ["qa"]


async def test_ke_hoach_cat_theo_tran_va_bo_phu_thuoc_tien(monkeypatch):
    """Phụ thuộc trỏ tới bước CHƯA có thì bị bỏ - nếu không thì `waves` treo."""
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({
        "steps": [{"intent": "qa", "request": "a", "depends_on": ["s2"]},
                  {"intent": "agent", "request": "b"},
                  {"intent": "qa", "request": "c"},
                  {"intent": "agent", "request": "d"}],
        "confidence": 0.9,
    }))

    plan = await planner_mod.make_plan("một yêu cầu dài")
    assert len(plan.steps) == planner_mod.MAX_STEPS
    assert plan.steps[0].depends_on == []
    assert [[s.id for s in w] for w in plan.waves()] == [["s1", "s2", "s3"]]


async def test_ke_hoach_hong_thi_lui_ve_dinh_tuyen(monkeypatch):
    """LLM chết ở tầng lập kế hoạch không được làm agent đứng hình."""
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({}, fail=True))
    monkeypatch.setattr(router_mod, "get_llm", lambda: FakeLLM(fail=True))

    plan = await planner_mod.make_plan("làm slide báo cáo tháng 8 cho giao ban")
    assert len(plan.steps) == 1
    assert plan.steps[0].intent == "presentation"     # lớp từ khoá vẫn nhận ra
    assert plan.source == "keyword"


async def test_ke_hoach_khong_sinh_hai_file_cung_loai(monkeypatch):
    monkeypatch.setattr(planner_mod, "get_llm", lambda: FakePlannerLLM({
        "steps": [{"intent": "report", "request": "tổng hợp quân số tháng 8"},
                  {"intent": "report", "request": "tổng hợp trang bị tháng 8"}],
        "confidence": 0.9,
    }))

    plan = await planner_mod.make_plan("tổng hợp quân số và trang bị tháng 8")
    assert [s.intent for s in plan.steps] == ["report"]


# --------------------------------------------------------------------------- #
# Chạy kế hoạch: song song, phụ thuộc, thử lại
# --------------------------------------------------------------------------- #
async def test_hai_buoc_doc_lap_chay_that_su_song_song(agent_env, monkeypatch):
    """Không chỉ gọi đủ hai bước - hai bước phải CHỒNG LẤN về thời gian."""
    import anyio

    monkeypatch.setattr(graph_mod, "make_plan", _plan("report", "presentation"))
    dang_chay = 0
    chong_lan = False

    async def _cham(request, inputs=None, history=None):
        nonlocal dang_chay, chong_lan
        dang_chay += 1
        await anyio.sleep(0.05)
        chong_lan = chong_lan or dang_chay > 1
        dang_chay -= 1
        return {"params": {"ky": "2026-08"}, "output_path": "", "data": {}, "slide_count": 2}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _cham)
    monkeypatch.setattr(graph_mod, "run_presentation_workflow", _cham)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8 và làm slide")

    assert chong_lan, "Hai bước độc lập vẫn chạy nối đuôi nhau"
    assert [s["intent"] for s in result["steps"]] == ["report", "presentation"]


async def test_buoc_phu_thuoc_nhan_duoc_boi_canh_cua_buoc_truoc(agent_env, monkeypatch):
    """"Làm slide từ số liệu đó" phải biết "đó" là kỳ nào - lấy từ kết quả thật."""
    monkeypatch.setattr(graph_mod, "make_plan", _plan(
        "report", "presentation",
        depends={"s2": ["s1"]},
        requests={"s1": "tổng hợp quân số tháng 8/2026", "s2": "làm slide từ số liệu đó"}))

    async def _aggregate(request, inputs=None, history=None):
        return {"params": {"ky": "2026-08", "ma_don_vi": ["DV01"]}, "output_path": "",
                "data": {}}

    nhan_duoc: dict[str, str] = {}

    async def _presentation(request, inputs=None, history=None):
        nhan_duoc["request"] = request
        return {"params": {}, "output_path": "", "slide_count": 3}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)
    monkeypatch.setattr(graph_mod, "run_presentation_workflow", _presentation)

    await graph_mod.run_agent("tổng hợp quân số tháng 8 rồi làm slide")

    assert "2026-08" in nhan_duoc["request"]
    assert "DV01" in nhan_duoc["request"]


async def test_gom_file_cua_moi_buoc_chu_khong_chi_buoc_cuoi(agent_env, monkeypatch, tmp_path):
    monkeypatch.setattr(graph_mod, "make_plan", _plan("report", "presentation"))
    docx = tmp_path / "BC_TONGHOP_2026-08.docx"
    pptx = tmp_path / "SLIDE_2026-08.pptx"
    docx.write_bytes(b"docx")
    pptx.write_bytes(b"pptx")

    async def _aggregate(request, inputs=None, history=None):
        return {"params": {"ky": "2026-08"}, "output_path": str(docx), "data": {}}

    async def _presentation(request, inputs=None, history=None):
        return {"params": {}, "output_path": str(pptx), "slide_count": 3}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)
    monkeypatch.setattr(graph_mod, "run_presentation_workflow", _presentation)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8 rồi làm slide")

    assert {a["kind"] for a in result["artifacts"]} == {"docx", "pptx"}
    # Câu trả lời ghép phải nêu cả hai việc, không chỉ việc cuối.
    assert "Tổng hợp báo cáo" in result["answer"] and "Tạo slide" in result["answer"]


async def test_qa_khong_ra_gi_thi_thu_lai_bang_tra_so_lieu(agent_env, monkeypatch):
    """Kho tài liệu im lặng -> đổi NGUỒN, không hỏi lại cùng một nguồn."""
    monkeypatch.setattr(graph_mod, "make_plan", _plan("qa"))

    async def _qa_rong(state, request):
        return {"answer": "Tôi không tìm thấy thông tin này.", "citations": [],
                "result": {"chunk_count": 0}, "error": ""}

    async def _tra_so_lieu(state, request):
        return {"answer": "Quân số DV01 tháng 8/2026 là 120 người.",
                "result": {"stop_reason": "hoàn thành",
                           "tool_log": [{"tool": "get_personnel_statistics",
                                         "ok": True, "empty": False}]},
                "refs": [], "error": ""}

    monkeypatch.setitem(graph_mod.BRANCHES, "qa", _qa_rong)
    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _tra_so_lieu)

    result = await graph_mod.run_agent("quân số DV01 tháng 8 là bao nhiêu")

    step = result["steps"][0]
    assert step["planned_intent"] == "qa" and step["intent"] == "agent"
    assert step["attempts"] == 2 and step["retried_as"] == "agent"
    assert "120 người" in result["answer"]


async def test_ca_hai_nguon_deu_rong_thi_noi_that_da_tra_o_dau(agent_env, monkeypatch):
    monkeypatch.setattr(graph_mod, "make_plan", _plan("qa"))

    async def _qa_rong(state, request):
        return {"answer": "Tôi không tìm thấy thông tin này.", "citations": [],
                "result": {"chunk_count": 0}, "error": ""}

    async def _so_lieu_rong(state, request):
        """Gọi được tool nhưng CSDL không có dữ liệu cho kỳ đó."""
        return {"answer": "Chưa có dữ liệu cho kỳ này.",
                "result": {"stop_reason": "hoàn thành",
                           "tool_log": [{"tool": "get_personnel_statistics",
                                         "ok": True, "empty": True}]},
                "refs": [], "error": ""}

    monkeypatch.setitem(graph_mod.BRANCHES, "qa", _qa_rong)
    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _so_lieu_rong)

    result = await graph_mod.run_agent("giá vàng hôm nay")

    assert result["steps"][0]["empty"] is True
    assert "tra số liệu" in result["answer"].lower()


async def test_thieu_dau_vao_thi_hoi_nguoi_dung_chu_khong_thu_lai(agent_env, monkeypatch):
    """Thiếu mã đơn vị thì chạy lại bằng nghiệp vụ nào cũng vẫn thiếu."""
    monkeypatch.setattr(graph_mod, "make_plan", _plan("draft"))

    async def _draft(request, ma_don_vi=None, inputs=None, history=None):
        return {"missing_input": ["ma_don_vi"], "output_path": "", "params": {}}

    async def _khong_duoc_goi(state, request):
        raise AssertionError("Thiếu đầu vào mà vẫn đi thử lại")

    monkeypatch.setattr(graph_mod, "run_draft_workflow", _draft)
    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _khong_duoc_goi)

    result = await graph_mod.run_agent("soạn báo cáo tài nguyên tháng 8")
    assert result["steps"][0]["attempts"] == 1
    assert result["missing_input"] == ["ma_don_vi"]


async def test_nguon_cua_hai_buoc_khong_dam_so_nhau(agent_env, monkeypatch):
    """Hai bước đều đánh nguồn từ [1]; ghép lại phải thành một dãy 1..n."""
    monkeypatch.setattr(graph_mod, "make_plan", _plan("agent", "agent"))

    async def _co_nguon(state, request):
        return {"answer": "Quân số là 120 người [1].",
                "result": {"stop_reason": "hoàn thành",
                           "tool_log": [{"tool": "get_personnel_statistics",
                                         "ok": True, "empty": False}]},
                "refs": [{"id": 1, "kind": "cong_cu", "label": "quân số",
                          "snippet": "…", "locator": {}}],
                "error": ""}

    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _co_nguon)

    result = await graph_mod.run_agent("quân số DV01 và DV02 tháng 8")

    assert [ref["id"] for ref in result["refs"]] == [1, 2]
    assert "[1]" in result["answer"] and "[2]" in result["answer"]


# --------------------------------------------------------------------------- #
# Kênh tiến trình: hiện agent đang làm gì, ngay lúc nó làm
# --------------------------------------------------------------------------- #
def _thu_su_kien():
    """Bắt sự kiện kèm thứ tự để kiểm cả nội dung lẫn trình tự."""
    ghi: list[tuple[str, dict]] = []
    return ghi, lambda event, data: ghi.append((event, data))


async def test_khong_ai_nghe_thi_phat_tien_trinh_la_lenh_rong(agent_env, monkeypatch):
    """Đường `/chat` cũ không được trả giá gì cho tính năng của `/chat/stream`."""
    from app.agents import progress

    monkeypatch.setattr(graph_mod, "make_plan", _plan("report"))

    async def _aggregate(request, inputs=None, history=None):
        return {"params": {"ky": "2026-08"}, "output_path": "", "data": {}}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)

    assert progress._emitter.get() is None
    result = await graph_mod.run_agent("tổng hợp quân số tháng 8")
    assert result["intent"] == "report"
    assert progress._emitter.get() is None, "kênh phát rò ra ngoài phạm vi"


async def test_phat_du_ke_hoach_va_vong_doi_tung_buoc(agent_env, monkeypatch):
    from app.agents import progress

    monkeypatch.setattr(graph_mod, "make_plan", _plan("report", "presentation",
                                                      depends={"s2": ["s1"]}))

    async def _aggregate(request, inputs=None, history=None):
        return {"params": {"ky": "2026-08"}, "output_path": "", "data": {}}

    async def _presentation(request, inputs=None, history=None):
        return {"params": {}, "output_path": "", "slide_count": 4}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _aggregate)
    monkeypatch.setattr(graph_mod, "run_presentation_workflow", _presentation)

    ghi, emitter = _thu_su_kien()
    with progress.collecting(emitter):
        await graph_mod.run_agent("tổng hợp quân số tháng 8 rồi làm slide")

    ten = [e for e, _ in ghi]
    assert ten.count("plan") == 1
    assert ten.count("step_start") == 2 and ten.count("step_done") == 2
    # Kế hoạch phải tới trước mọi bước - giao diện dựng khung rồi mới tô từng ô.
    assert ten.index("plan") < ten.index("step_start")

    plan = dict(ghi[[e for e, _ in ghi].index("plan")][1])
    assert [s["intent"] for s in plan["steps"]] == ["report", "presentation"]

    xong = [d for e, d in ghi if e == "step_done"]
    assert {d["id"] for d in xong} == {"s1", "s2"}
    assert all(d["attempts"] == 1 and not d["empty"] for d in xong)


async def test_su_kien_tien_trinh_khong_mang_ket_qua_cong_cu(agent_env, monkeypatch):
    """Số liệu thô chưa qua van đối chiếu thì không được lên màn hình."""
    from app.agents import progress

    monkeypatch.setattr(graph_mod, "make_plan", _plan("agent"))

    async def _tra_so_lieu(state, request):
        return {"answer": "Quân số là 120 người.",
                "result": {"stop_reason": "hoàn thành",
                           "tool_log": [{"tool": "get_personnel_statistics", "ok": True,
                                         "empty": False, "preview": '{"quan_so": 120}'}]},
                "refs": [], "error": ""}

    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _tra_so_lieu)

    ghi, emitter = _thu_su_kien()
    with progress.collecting(emitter):
        await graph_mod.run_agent("quân số DV01 tháng 8")

    assert all(e in progress.EVENTS for e, _ in ghi), "phát sự kiện ngoài danh mục"
    assert not any(e in ("tool_call", "tool_result") for e, _ in ghi)
    for _, data in ghi:
        phang = json.dumps(data, ensure_ascii=False)
        assert "tool_log" not in phang and "quan_so" not in phang, phang[:120]


async def test_phat_su_kien_thu_lai_kem_nghiep_vu_thay_the(agent_env, monkeypatch):
    from app.agents import progress

    monkeypatch.setattr(graph_mod, "make_plan", _plan("qa"))

    async def _qa_rong(state, request):
        return {"answer": "Không tìm thấy.", "citations": [],
                "result": {"chunk_count": 0}, "error": ""}

    async def _tra_so_lieu(state, request):
        return {"answer": "Quân số là 120 người.",
                "result": {"stop_reason": "hoàn thành",
                           "tool_log": [{"tool": "x", "ok": True, "empty": False}]},
                "refs": [], "error": ""}

    monkeypatch.setitem(graph_mod.BRANCHES, "qa", _qa_rong)
    monkeypatch.setitem(graph_mod.BRANCHES, "agent", _tra_so_lieu)

    ghi, emitter = _thu_su_kien()
    with progress.collecting(emitter):
        await graph_mod.run_agent("quân số DV01 tháng 8")

    retry = [d for e, d in ghi if e == "step_retry"]
    assert len(retry) == 1
    assert retry[0]["from"] == "qa" and retry[0]["to"] == "agent"

    xong = [d for e, d in ghi if e == "step_done"][0]
    assert xong["planned_intent"] == "qa" and xong["intent"] == "agent"
    assert xong["attempts"] == 2


async def test_hang_doi_day_thi_bo_su_kien_chu_khong_treo(agent_env):
    """Giao diện đọc chậm không được làm agent đứng lại."""
    from app.agents import progress

    channel = progress.Channel(maxsize=3)
    for i in range(10):
        channel.put("thinking", {"text": f"bước {i}"})

    assert channel.queue.qsize() == 3
    assert channel.dropped == 7


# --------------------------------------------------------------------------- #
# Dịch lỗi: người dùng nhận một câu, log nhận chi tiết
# --------------------------------------------------------------------------- #
def test_loi_csdl_khong_lo_cau_sql_ra_ngoai():
    """Nguyên văn ngoại lệ SQL mang tên bảng, tên schema và cả câu truy vấn."""
    from sqlalchemy.exc import ProgrammingError

    from app.services import errors

    tho = ProgrammingError(
        "SELECT [Dms_WorkDepartment].[Id] FROM [Dms_WorkDepartment] WHERE ...",
        {},
        Exception("[42000] The SELECT permission was denied on the object "
                  "'Dms_WorkDepartment', database 'bqp-private-ai-assistant', schema 'dbo'"),
    )
    cau = errors.friendly(tho)

    assert "Dms_WorkDepartment" not in cau
    assert "SELECT" not in cau and "dbo" not in cau
    assert "cơ sở dữ liệu" in cau.lower()


def test_loi_cua_chinh_minh_thi_giu_nguyen():
    """`ToolError` đã viết bằng tiếng người và giúp người dùng sửa đầu vào."""
    from app.services import errors

    assert errors.friendly(ToolError('Kỳ phải có dạng "YYYY-MM"')) == 'Kỳ phải có dạng "YYYY-MM"'


def test_loi_llm_va_loi_la_deu_co_cau_rieng():
    from app.services import errors
    from app.services.llm import LLMError

    assert errors.friendly(LLMError("connection reset")) == errors.LLM
    assert errors.friendly(ValueError("gì đó rất lạ")) == errors.DEFAULT


async def test_mot_buoc_hong_khong_giet_ca_ke_hoach(agent_env, monkeypatch):
    """CSDL sập ở bước 1 thì bước 2 vẫn phải chạy và người dùng vẫn nhận được gì đó."""
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(graph_mod, "make_plan", _plan("report", "qa"))

    async def _sap(request, inputs=None, history=None):
        raise OperationalError("SELECT [Dms_Assets].[Id] FROM [Dms_Assets]", {},
                               Exception("login failed"))

    async def _qa(state, request):
        return {"answer": "Theo Nghị định 30/2020…", "citations": [{"id": 1}],
                "result": {"chunk_count": 3}, "error": ""}

    monkeypatch.setattr(graph_mod, "run_aggregate_workflow", _sap)
    monkeypatch.setitem(graph_mod.BRANCHES, "qa", _qa)

    result = await graph_mod.run_agent("tổng hợp quân số tháng 8 và cho biết quy định")

    assert len(result["steps"]) == 2, "bước sau không chạy khi bước trước sập"
    assert "Nghị định 30/2020" in result["answer"], "mất luôn kết quả của bước còn lại"
    assert "Dms_Assets" not in result["answer"] and "SELECT" not in result["answer"]
    assert "Dms_Assets" not in result["error"]
