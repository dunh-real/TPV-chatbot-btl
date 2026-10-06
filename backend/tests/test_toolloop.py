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
    """Trả lần lượt các lượt đã soạn; hết kịch bản thì trả lời bằng chữ.

    Giả lập đúng hợp đồng của `stream_chat_with_tools`: chữ đi ra qua `on_delta`
    theo từng mảnh, và mảnh chỉ được phát ở lượt KHÔNG gọi tool - giống hệt điều
    kiện mà bản thật áp dụng.
    """

    def __init__(self, turns: list[AssistantTurn], fail: bool = False) -> None:
        self.turns = list(turns)
        self.fail = fail
        self.calls = 0

    def _next(self) -> AssistantTurn:
        self.calls += 1
        if self.fail:
            raise LLMError("vLLM không phản hồi")
        if self.turns:
            return self.turns.pop(0)
        return AssistantTurn(content="Xong.", reasoning="", tool_calls=[], raw={})

    async def chat_with_tools(self, messages, tools, **kwargs):
        return self._next()

    async def stream_chat_with_tools(self, messages, tools, on_delta=None, **kwargs):
        turn = self._next()
        # Bản thật phát mảnh NGAY khi chữ về, tức là trước lúc biết lượt này có
        # gọi tool hay không. Lượt vừa có chữ vừa có tool_call chính là ca "câu
        # dẫn": mảnh đã ra màn hình rồi mới lộ ra nó không phải câu trả lời.
        if on_delta and turn.content:
            giua = max(1, len(turn.content) // 2)   # cắt đôi để lộ lỗi nối mảnh
            on_delta(turn.content[:giua])
            on_delta(turn.content[giua:])
        return turn


def turn_call(name: str, **arguments) -> AssistantTurn:
    call = ToolCall(id=f"c{abs(hash(name)) % 1000}", name=name, arguments=arguments)
    return AssistantTurn(content="", reasoning="", tool_calls=[call],
                         raw={"role": "assistant", "tool_calls": []})


def turn_text(text: str) -> AssistantTurn:
    return AssistantTurn(content=text, reasoning="", tool_calls=[], raw={})


@pytest.fixture
def fake_tools(monkeypatch):
    """Thay registry bằng hai tool giả: một đọc, một ghi."""

    class NhatKyGoi(list):
        """List có chỗ gắn thêm số liệu đo song song."""

        dinh_cao_song_song: dict[str, int]

    goi = NhatKyGoi()

    dang_chay = {"n": 0, "dinh": 0}

    async def doc_so_lieu(ky: str, ma_don_vi: str | None = None):
        import anyio

        goi.append(("doc_so_lieu", {"ky": ky, "ma_don_vi": ma_don_vi}))
        if ky == "sai":
            from app.tools.base import ToolError
            raise ToolError('Kỳ phải có dạng "YYYY-MM"')
        # Đủ chậm để hai lời gọi song song chồng lấn thấy được.
        dang_chay["n"] += 1
        dang_chay["dinh"] = max(dang_chay["dinh"], dang_chay["n"])
        await anyio.sleep(0.03)
        dang_chay["n"] -= 1
        if ky == "rong":
            return {"ky": ky, "units_with_data": 0, "breakdown": []}
        return {"ky": ky, "nhan_su": 48}

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
    goi.dinh_cao_song_song = dang_chay          # type: ignore[attr-defined]
    return goi


def turn_calls(*specs) -> AssistantTurn:
    """Một lượt phát nhiều lời gọi - thứ model làm khi các việc độc lập nhau."""
    calls = [ToolCall(id=f"c{i}", name=name, arguments=arguments)
             for i, (name, arguments) in enumerate(specs)]
    return AssistantTurn(content="", reasoning="", tool_calls=calls,
                         raw={"role": "assistant", "tool_calls": []})


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
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"), turn_text("Nhân sự là 48.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự bao nhiêu")
    assert result["stop_reason"] == "hoàn thành"
    assert result["tools_called"] == ["doc_so_lieu"]
    assert result["answer"] == "Nhân sự là 48."


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
                       turn_text("Nhân sự là 48.")])
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
                       turn_text("Nhân sự là 48, trong đó 37 người đi công tác.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự bao nhiêu")
    assert result["validation"]["status"] == "failed"
    assert "37" in result["validation"]["issues"][0]["numbers"]


async def test_so_truy_duoc_thi_qua_van(fake_tools, monkeypatch):
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"),
                       turn_text("Nhân sự kỳ 2026-08 là 48 người.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự bao nhiêu")
    assert result["validation"]["status"] == "passed"


# --------------------------------------------------------------------------- #
# Gọi song song
# --------------------------------------------------------------------------- #
async def test_nhieu_loi_goi_trong_mot_luot_chay_song_song(fake_tools, monkeypatch):
    """Ba đơn vị là ba truy vấn độc lập - không có lý do gì phải nối đuôi."""
    llm = ScriptedLLM([
        turn_calls(("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV01"}),
                   ("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV02"}),
                   ("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV03"})),
        turn_text("Đã tra xong ba đơn vị."),
    ])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự ba đơn vị tháng 8")

    assert len(result["tool_log"]) == 3
    assert fake_tools.dinh_cao_song_song["dinh"] == 3, "ba lời gọi vẫn chạy nối đuôi"
    # Số thứ tự nguồn phát theo thứ tự model gọi, không phải theo thứ tự chạy xong.
    assert [item["ref_id"] for item in result["tool_log"]] == [1, 2, 3]
    assert [item["arguments"]["ma_don_vi"] for item in result["tool_log"]] == \
        ["DV01", "DV02", "DV03"]


async def test_luot_co_tool_ghi_thi_treo_ca_luot_cho_duyet(fake_tools, monkeypatch):
    """Lượt nào xin ghi thì treo NGUYÊN lượt, kể cả phần đọc đứng trước nó.

    Vòng lặp dừng ngay tại đây nên kết quả đọc cũng không ai đọc tới - chạy nó
    chỉ tốn một truy vấn CSDL rồi vứt đi.
    """
    llm = ScriptedLLM([turn_calls(("doc_so_lieu", {"ky": "2026-08"}),
                                  ("ghi_file", {"noi_dung": "abc"}))])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("tra rồi ghi file")

    assert result["stop_reason"] == "chờ duyệt"
    assert result["pending_approval"]["tool"] == "ghi_file"
    assert fake_tools == [], "chưa duyệt mà đã chạy tool nào đó"


async def test_loi_goi_trung_khong_giet_ca_luot(fake_tools, monkeypatch):
    """Lỗi cũ: một lời gọi trùng làm mất luôn những lời gọi còn lại và câu trả lời.

    Model phát ba lời gọi, cái thứ hai trùng cái đã chạy. Cái thứ ba vẫn phải
    chạy, và phiên vẫn phải kết thúc bằng một câu trả lời.
    """
    llm = ScriptedLLM([
        turn_call("doc_so_lieu", ky="2026-08", ma_don_vi="DV01"),
        turn_calls(("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV02"}),
                   ("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV01"}),
                   ("doc_so_lieu", {"ky": "2026-08", "ma_don_vi": "DV03"})),
        turn_text("Nhân sự mỗi đơn vị là 48 người."),
    ])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự ba đơn vị")

    assert result["stop_reason"] == "hoàn thành"
    assert result["answer"] == "Nhân sự mỗi đơn vị là 48 người."
    assert [item["arguments"]["ma_don_vi"] for item in result["tool_log"]] == \
        ["DV01", "DV02", "DV03"], "lời gọi trùng đã kéo theo lời gọi sau nó"


async def test_chu_trinh_hai_loi_goi_cung_bi_bat(fake_tools, monkeypatch):
    """A -> B -> A -> B: chỉ nhớ lời gọi ngay trước thì không bao giờ thấy."""
    llm = ScriptedLLM([turn_call("doc_so_lieu", ky=f"2026-0{i % 2 + 1}") for i in range(8)])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("hỏi vòng vo")

    assert result["stop_reason"] == "gọi lặp"
    assert len(fake_tools) == 2, "chu trình A-B-A-B vẫn chạy lại tool"


# --------------------------------------------------------------------------- #
# Nguồn trống: khác lỗi, và khác số 0
# --------------------------------------------------------------------------- #
def test_nhan_ra_ket_qua_rong_ruot():
    assert toolloop.looks_empty({"units_with_data": 0, "breakdown": []})
    assert toolloop.looks_empty({"hits": []})
    assert toolloop.looks_empty([])
    # Có dữ liệu thật thì không phải rỗng, kể cả khi vài chỉ tiêu bằng 0.
    assert not toolloop.looks_empty({"units_with_data": 2, "breakdown": [{"nhan_su": 0}]})
    assert not toolloop.looks_empty({"ky": "2026-08", "nhan_su": 0})


async def test_nguon_rong_duoc_bao_cho_model_bang_loi_khac_loi(fake_tools, monkeypatch):
    """Gọi đúng mà không có dữ liệu -> phải nói rõ, nếu không model kết luận "bằng 0"."""
    ghi_nhan: list[dict] = []

    class BatMessages(ScriptedLLM):
        async def stream_chat_with_tools(self, messages, tools, **kwargs):
            ghi_nhan.clear()
            ghi_nhan.extend(messages)
            return await super().stream_chat_with_tools(messages, tools, **kwargs)

    llm = BatMessages([turn_call("doc_so_lieu", ky="rong"),
                       turn_text("Kỳ này chưa có dữ liệu.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    result = await toolloop.run_tool_loop("nhân sự kỳ đó")

    assert result["tool_log"][0]["empty"] is True
    assert result["tool_log"][0]["ok"] is True, "nguồn trống KHÁC lỗi công cụ"
    tool_msg = [m for m in ghi_nhan if m.get("role") == "tool"][-1]
    assert "NGUỒN TRỐNG" in tool_msg["content"]


# --------------------------------------------------------------------------- #
# Nhịp tim tiến trình
# --------------------------------------------------------------------------- #
async def test_phat_nhip_tim_moi_luot_va_khi_goi_cong_cu(fake_tools, monkeypatch):
    """Một lượt LLM mất 10-15 giây; không báo gì thì vòng lặp trông như đã treo."""
    from app.agents import progress

    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"), turn_text("Nhân sự là 48.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    ghi: list[tuple[str, dict]] = []
    with progress.collecting(lambda e, d: ghi.append((e, d))):
        await toolloop.run_tool_loop("nhân sự bao nhiêu")

    nhip = [d for e, d in ghi if e == "step_progress"]
    # Hai lượt LLM -> hai nhịp "đang suy nghĩ", cộng một nhịp lúc gọi công cụ.
    assert [d["phase"] for d in nhip] == ["đang suy nghĩ", "đang tra", "đang suy nghĩ"]
    assert nhip[1]["tools"] == ["doc_so_lieu"]
    assert [d["round"] for d in nhip] == [1, 1, 2]


async def test_nhip_tim_chi_mang_ten_cong_cu_khong_mang_tham_so(fake_tools, monkeypatch):
    """Tên là nhãn việc; tham số và kết quả thì không được rời khỏi vòng lặp."""
    from app.agents import progress

    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08", ma_don_vi="DV01"),
                       turn_text("Xong.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    ghi: list[tuple[str, dict]] = []
    with progress.collecting(lambda e, d: ghi.append((e, d))):
        await toolloop.run_tool_loop("hỏi")

    import json as _json

    for event, data in ghi:
        assert event in progress.EVENTS
        phang = _json.dumps(data, ensure_ascii=False)
        assert "DV01" not in phang and "2026-08" not in phang, phang
        assert "48" not in phang, phang


# --------------------------------------------------------------------------- #
# Chữ chảy ra ngay lúc model sinh
# --------------------------------------------------------------------------- #
async def test_cau_tra_loi_cuoi_duoc_phat_theo_manh(fake_tools, monkeypatch):
    from app.agents import progress

    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"),
                       turn_text("Nhân sự kỳ 2026-08 là 48 người.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    ghi: list[tuple[str, dict]] = []
    with progress.collecting(lambda e, d: ghi.append((e, d))):
        result = await toolloop.run_tool_loop("nhân sự bao nhiêu")

    manh = [d["text"] for e, d in ghi if e == "answer_delta"]
    assert len(manh) > 1, "câu trả lời vẫn về một cục"
    # Ghép lại phải ĐÚNG BẰNG câu cuối - stream không được làm rơi hay lặp chữ.
    assert "".join(manh) == result["answer"]
    assert not any(e == "answer_reset" for e, _ in ghi)


async def test_luot_goi_tool_khong_phat_manh_nao(fake_tools, monkeypatch):
    """Lượt đang sinh tool_call thì không có chữ nào là câu trả lời."""
    from app.agents import progress

    llm = ScriptedLLM([turn_call("doc_so_lieu", ky="2026-08"), turn_text("Xong.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    ghi: list[tuple[str, dict]] = []
    with progress.collecting(lambda e, d: ghi.append((e, d))):
        await toolloop.run_tool_loop("hỏi")

    thu_tu = [e for e, _ in ghi]
    # Mảnh đầu tiên chỉ xuất hiện SAU khi công cụ đã chạy xong.
    assert thu_tu.index("answer_delta") > thu_tu.index("step_progress")


async def test_cau_dan_truoc_khi_goi_tool_thi_bao_xoa(fake_tools, monkeypatch):
    """Model viết vài câu dẫn rồi mới gọi tool -> chữ đó không phải câu trả lời."""
    from app.agents import progress
    from app.services.llm import AssistantTurn, ToolCall

    dan = AssistantTurn(
        content="Để tôi tra số liệu đã.", reasoning="",
        tool_calls=[ToolCall(id="c1", name="doc_so_lieu", arguments={"ky": "2026-08"})],
        raw={"role": "assistant", "tool_calls": []})
    llm = ScriptedLLM([dan, turn_text("Nhân sự là 48.")])
    monkeypatch.setattr(toolloop, "get_llm", lambda: llm)

    ghi: list[tuple[str, dict]] = []
    with progress.collecting(lambda e, d: ghi.append((e, d))):
        await toolloop.run_tool_loop("hỏi")

    assert any(e == "answer_reset" for e, _ in ghi), "câu dẫn không được dọn khỏi màn hình"


def test_gom_manh_tool_call_roi_rac_thanh_loi_goi_hoan_chinh():
    """Tham số tool về rời rạc theo `index`; nối sai là gọi sai công cụ."""
    import json as _json

    from app.services.llm import LLMClient

    partial: dict[int, dict] = {}
    deltas = [
        [{"index": 0, "id": "c1", "function": {"name": "doc_so_lieu", "arguments": '{"ky"'}}],
        [{"index": 0, "function": {"arguments": ': "2026-08"'}}],
        [{"index": 1, "id": "c2", "function": {"name": "ghi_file", "arguments": '{"noi_dung"'}}],
        [{"index": 0, "function": {"arguments": "}"}}],
        [{"index": 1, "function": {"arguments": ': "x"}'}}],
    ]
    for delta in deltas:
        for item in delta:
            slot = partial.setdefault(int(item.get("index") or 0),
                                      {"id": "", "name": "", "args": []})
            if item.get("id"):
                slot["id"] = item["id"]
            fn = item.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"].append(fn["arguments"])

    assert _json.loads("".join(partial[0]["args"])) == {"ky": "2026-08"}
    assert _json.loads("".join(partial[1]["args"])) == {"noi_dung": "x"}
    assert partial[0]["name"] == "doc_so_lieu" and partial[1]["name"] == "ghi_file"
    assert hasattr(LLMClient, "stream_chat_with_tools")
