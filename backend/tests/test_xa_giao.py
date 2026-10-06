"""Lời chào không được đem đi tra cứu, và câu hỏi thật không được nhận nhầm.

Hai phía lệch giá nhau: bỏ sót một lời chào chỉ tốn một lượt truy hồi vô ích,
còn nhận nhầm một câu hỏi thật thành lời chào thì người dùng bị từ chối tra cứu
mà không hiểu vì sao. Nửa dưới của bài test này là nửa quan trọng.
"""

from __future__ import annotations

import pytest

from app.agents.nodes.qa import retrieve_node
from app.agents.xa_giao import la_xa_giao

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("cau", [
    "Hi", "hi", "hello", "Hello!", "hey", "alo",
    "chào", "Xin chào", "chào bạn", "chào em", "Chào anh!",
    "ok", "oke", "OK bạn", "vâng", "dạ", "uk",
    "cảm ơn", "Cảm ơn bạn!", "cám ơn nhé", "thanks", "thank you", "tks",
    "bye", "tạm biệt", "good morning", "chào buổi sáng",
    "bạn là ai", "Bạn là ai?", "bạn làm được gì",
    "Bạn có thể giúp gì?", "giới thiệu về bạn",
    "test", "   ", "😀",
])
def test_cau_xa_giao_duoc_nhan(cau):
    assert la_xa_giao(cau)


@pytest.mark.parametrize("cau", [
    "Điều 7 quy định mức phụ cấp bao nhiêu?",
    "cho tôi hỏi về quy định nghỉ phép",
    "chào bạn, cho tôi hỏi thủ tục xin nghỉ",
    "Hi, tóm tắt giúp tôi tài liệu này",
    "FedEABoost là gì",
    "entropy trọng số nhãn là gì",
    "đơn vị nào chưa nộp báo cáo",
    "soạn báo cáo tháng 9 cho DV01",
    "ai ký công văn kiểm kê",
    "tài liệu nói gì về bước 2",
    "bạn đọc giúp tôi file vừa gửi",
])
def test_cau_co_noi_dung_khong_bi_nhan_nham(cau):
    assert not la_xa_giao(cau)


async def test_model_noi_khong_tra_cuu_thi_khong_cham_toi_qdrant(monkeypatch):
    """Quyết định phải chặn TRƯỚC khi gọi truy hồi, không phải lọc kết quả sau."""
    def khong_duoc_goi():  # pragma: no cover - gọi tới là hỏng
        raise AssertionError("lượt không cần tra cứu thì không được đem đi truy hồi")

    monkeypatch.setattr("app.agents.nodes.qa.get_retriever", khong_duoc_goi)
    ra = await retrieve_node({"question": "Hi", "conversation_id": "c1",
                              "can_tra_cuu": False})

    # Không có chunk -> đồ thị rẽ sang `no_context`, nơi không gắn trích dẫn nào.
    assert ra["chunks"] == []
    assert ra["trace"]["xa_giao"] is True


async def test_model_hong_thi_lui_ve_luat_tu_khoa(monkeypatch):
    """Quyết định là việc của model, nhưng model chết thì vẫn phải quyết được.

    Đây là lý do `la_xa_giao` còn tồn tại sau khi đã nhường quyền cho model: mọi
    tầng trong agent này đều có một đường lùi không cần LLM.
    """
    from app.agents.nodes import qa as qa_mod

    def llm_chet():
        raise RuntimeError("vLLM sập")

    monkeypatch.setattr(qa_mod, "get_llm", llm_chet)

    chao = await qa_mod.quyet_dinh_tra_cuu_node({"question": "hi", "history": []})
    assert chao["can_tra_cuu"] is False
    assert chao["trace"]["nguon_quyet_dinh"] == "từ khoá"

    hoi = await qa_mod.quyet_dinh_tra_cuu_node(
        {"question": "quy định nghỉ phép thế nào", "history": []})
    assert hoi["can_tra_cuu"] is True


async def test_loi_chao_khong_bi_day_sang_nhanh_cong_cu():
    """`chunk_count` bằng 0 ở lượt xã giao là CỐ Ý, không phải tra cứu hụt.

    Thiếu chốt này thì `RETRY_AS["qa"] = "agent"` đẩy lời chào sang vòng lặp
    công cụ ERP - chạy vài giây, gọi vài tool, để nói lại đúng câu "chào bạn".
    """
    from app.agents.graph import _step_is_empty

    xa_giao = {"answer": "Chào bạn!", "xa_giao": True, "result": {"chunk_count": 0}}
    assert not _step_is_empty("qa", xa_giao)

    # Câu hỏi thật mà không truy hồi được thì VẪN phải coi là rỗng để thử lại.
    tra_hut = {"answer": "Không tìm thấy.", "xa_giao": False, "result": {"chunk_count": 0}}
    assert _step_is_empty("qa", tra_hut)


async def test_lap_ke_hoach_cho_loi_chao_khong_goi_llm(monkeypatch):
    """Hai lượt gọi LLM để biết "hi" là lời chào là hai lượt tiêu phí."""
    from app.agents import planner as planner_mod

    def khong_duoc_goi():  # pragma: no cover - gọi tới là hỏng
        raise AssertionError("lời chào không cần hỏi model")

    monkeypatch.setattr(planner_mod, "get_llm", khong_duoc_goi)
    plan = await planner_mod.make_plan("hi", has_file=True)

    assert plan.source == "rule"
    assert [s.intent for s in plan.steps] == ["qa"]


async def test_khong_tra_cuu_thi_khong_bi_day_sang_vong_lap_cong_cu(monkeypatch):
    """Đường "không tra cứu" không đi qua `retrieve_node`, nên cờ phải đặt sớm hơn.

    `graph._step_is_empty` đọc `trace["xa_giao"]` để biết `chunk_count = 0` là CỐ Ý.
    Thiếu cờ, `RETRY_AS["qa"] = "agent"` đẩy lời chào sang vòng lặp công cụ ERP.
    """
    from app.agents.nodes import qa as qa_mod

    def llm_chet():
        raise RuntimeError("vLLM sập")

    monkeypatch.setattr(qa_mod, "get_llm", llm_chet)
    ra = await qa_mod.quyet_dinh_tra_cuu_node({"question": "hi", "history": []})

    assert ra["trace"]["xa_giao"] is True
    # Câu hỏi thật thì KHÔNG mang cờ đó - tra hụt vẫn phải được thử lại.
    hoi = await qa_mod.quyet_dinh_tra_cuu_node({"question": "quy định nghỉ phép", "history": []})
    assert hoi["trace"]["xa_giao"] is False
