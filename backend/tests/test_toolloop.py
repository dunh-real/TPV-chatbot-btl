"""Vòng lặp agent: schema sinh từ chữ ký hàm, và bốn điều kiện dừng.

Không cần LLM thật: model được thay bằng bản giả trả sẵn kịch bản tool_calls.
Trọng tâm là những thứ khiến một vòng lặp không chạy vô hạn.
"""

from __future__ import annotations

import pytest

from app.agents.nodes import toolloop
from app.services.llm import AssistantTurn, LLMError, ToolCall
from app.tools import registry
from app.tools.base import ToolSpec


class ScriptedLLM:
    """Trả lần lượt các lượt đã soạn; hết kịch bản thì trả lời bằng chữ."""

    def __init__(self, turns: list[AssistantTurn], fail: bool = False) -> None:
        self.turns = list(turns)
        self.fail = fail
        self.calls = 0

    async def chat_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        if self.turns:
            return self.turns.pop(0)
        return AssistantTurn(content="Xong.", reasoning="", tool_calls=[], raw={})


def turn_call(name: str, **arguments) -> AssistantTurn:
    call = ToolCall(id=f"c{abs(hash(name)) % 1000}", name=name, arguments=arguments)
    return AssistantTurn(content="", reasoning="", tool_calls=[call],
                         raw={"role": "assistant", "tool_calls": []})


def turn_text(text: str) -> AssistantTurn:
    return AssistantTurn(content=text, reasoning="", tool_calls=[], raw={})


@pytest.fixture
def fake_tools(monkeypatch):
    """Thay registry bằng hai tool giả: một đọc, một ghi."""
    goi: list[tuple[str, dict]] = []

    async def doc_so_lieu(ky: str, ma_don_vi: str | None = None):
        goi.append(("doc_so_lieu", {"ky": ky, "ma_don_vi": ma_don_vi}))
        if ky == "sai":
            from app.tools.base import ToolError
            raise ToolError('Kỳ phải có dạng "YYYY-MM"')
        return {"ky": ky, "quan_so": 48}

    async def ghi_file(noi_dung: str):
        goi.append(("ghi_file", {"noi_dung": noi_dung}))
        return {"path": "/tmp/x.docx"}

    tools = {
        "doc_so_lieu": ToolSpec(name="doc_so_lieu", description="đọc số liệu",
                                parameters={"ky": "YYYY-MM", "ma_don_vi": "mã đơn vị"},
                                func=doc_so_lieu),
        "ghi_file": ToolSpec(name="ghi_file", description="ghi ra file",
                             parameters={"noi_dung": "nội dung"},
                             func=ghi_file, side_effect=True),
    }
    monkeypatch.setattr(registry, "TOOLS", tools)
    return goi


# --------------------------------------------------------------------------- #
# Schema sinh từ chữ ký hàm
# --------------------------------------------------------------------------- #
def test_schema_suy_kieu_va_truong_bat_buoc_tu_chu_ky():
    """Kiểu lấy từ type hint nên không thể lệch khi sửa hàm."""
    schema = registry.TOOLS["search_documents"].to_json_schema()["function"]
    props = schema["parameters"]["properties"]

    assert schema["parameters"]["required"] == ["query"]     # chỉ query không có default
    assert props["query"]["type"] == "string"
    assert props["top_n"]["type"] == "integer"               # int | None -> integer
    assert props["doc_ids"]["type"] == "array"               # list[str] | str | None
    assert props["doc_ids"]["items"]["type"] == "string"


def test_schema_khong_phoi_tham_so_session():
    """Tool CSDL nhận session làm tham số đầu - model không được thấy nó."""
    schema = registry.TOOLS["get_personnel_statistics"].to_json_schema()["function"]
    assert "session" not in schema["parameters"]["properties"]
    assert schema["parameters"]["required"] == ["ky"]


def test_schema_khong_phoi_bi_danh():
    """Bí danh để nhận đầu vào cũ, không phải để model có hai cách gọi."""
    props = registry.TOOLS["get_personnel_statistics"].to_json_schema()["function"][
        "parameters"]["properties"]
    assert "start_date" not in props and "end_date" not in props


def test_moi_tool_deu_sinh_duoc_schema():
    for name, spec in registry.TOOLS.items():
        schema = spec.to_json_schema()["function"]
        assert schema["name"] == name
        assert set(schema["parameters"]["properties"]) == set(spec.parameters), name


# --------------------------------------------------------------------------- #
# Bốn điều kiện dừng
# --------------------------------------------------------------------------- #
async def test_dung_khi_model_thoi_doi_tool(fake_tools, monkeypatch):
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"), turn_text("Quân số là 48.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("quân số bao nhiêu")
    assert result["stop_reason"] == "hoàn thành"
    assert result["tools_called"] == ["doc_so_lieu"]
    assert result["answer"] == "Quân số là 48."


async def test_dung_khi_cham_tran_so_buoc(fake_tools, monkeypatch):
    """Model đòi tool mãi -> phải cắt, và nói rõ là đã cắt."""
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky=f"2026-0{i}") for i in range(1, 9)])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("hỏi vòng vo", max_steps=3)
    assert result["stop_reason"] == "chạm trần số bước"
    assert result["steps"] == 3


async def test_dung_khi_model_goi_lap_dung_mot_thu(fake_tools, monkeypatch):
    """Gọi trùng hệt hai lần liền = kẹt vòng, đây là kiểu lặp vô hạn hay gặp nhất."""
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08") for _ in range(5)])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("hỏi")
    assert result["stop_reason"] == "gọi lặp"
    assert len(fake_tools) == 1, "lời gọi trùng không được thực hiện lần hai"


async def test_dung_khi_tool_loi_qua_nhieu(fake_tools, monkeypatch):
    # Tham số khác nhau để không chạm luật gọi lặp.
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="sai", ma_don_vi=f"DV{i}")
                       for i in range(6)])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("hỏi")
    assert result["stop_reason"] == "quá nhiều lỗi công cụ"
    assert all(not item["ok"] for item in result["tool_log"])


async def test_loi_tool_duoc_tra_nguoc_cho_model_tu_sua(fake_tools, monkeypatch):
    """Sai tham số thì model phải được đọc thông báo lỗi, không phải sập request."""
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="sai"),
                       turn_call("doc_so_lieu", ky="2026-08"),
                       turn_text("Quân số là 48.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("hỏi")
    assert result["stop_reason"] == "hoàn thành"
    assert [i["ok"] for i in result["tool_log"]] == [False, True]
    assert "YYYY-MM" in result["tool_log"][0]["preview"]


async def test_llm_hong_thi_dung_co_kiem_soat(fake_tools, monkeypatch):
    monkeypatch.setattr(toolloop, "get_llm", lambda: ScriptedLLM([], fail=True))
    result = await toolloop.run_tool_loop("hỏi")
    assert result["stop_reason"] == "lỗi LLM"
    assert result["error"]


# --------------------------------------------------------------------------- #
# Cổng duyệt và van số
# --------------------------------------------------------------------------- #
async def test_tool_ghi_bi_chan_cho_nguoi_duyet(fake_tools, monkeypatch):
    """Đọc thì tự do, ghi thì phải có người gật."""
    llm = ScriptedLLM([turn_call("ghi_file", noi_dung="abc")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("soạn file")
    assert result["stop_reason"] == "chờ duyệt"
    assert result["pending_approval"]["tool"] == "ghi_file"
    assert fake_tools == [], "tool ghi không được chạy khi chưa duyệt"


async def test_so_khong_truy_duoc_thi_bi_danh_dau(fake_tools, monkeypatch):
    """Van chống bịa số của workflow 3/4/5, áp lại cho vòng lặp."""
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"),
                       turn_text("Quân số là 48, trong đó 37 người đi công tác.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("quân số bao nhiêu")
    assert result["validation"]["status"] == "failed"
    assert "37" in result["validation"]["issues"][0]["numbers"]


async def test_so_truy_duoc_thi_qua_van(fake_tools, monkeypatch):
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"),
                       turn_text("Quân số kỳ 2026-08 là 48 người.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("quân số bao nhiêu")
    assert result["validation"]["status"] == "passed"
