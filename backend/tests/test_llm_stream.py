"""Luồng streaming phải bỏ phần suy luận mà KHÔNG nuốt mất câu trả lời.

Cùng một model có ba cách trả phần suy luận tuỳ server dựng thế nào, và cách
lọc đúng cho cách này lại làm rỗng câu trả lời ở cách kia:

  1. server chạy `--reasoning-parser`  -> suy luận nằm ở `delta.reasoning`,
     `content` đã sạch và KHÔNG có thẻ `</think>` nào để chờ;
  2. không có parser                   -> suy luận nằm lẫn trong `content`,
     kết thúc bằng `</think>`;
  3. có parser nhưng model trả lời thẳng, không suy luận gì.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import Settings
from app.services.llm import LLMClient


def _sse(chunks: list[dict]) -> bytes:
    """Dựng thân phản hồi SSE giống hệt vLLM trả về."""
    lines = []
    for delta in chunks:
        payload = {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n")
    lines.append("data: [DONE]\n\n")
    return "".join(lines).encode()


def _client(chunks: list[dict], *, thinking: bool = True) -> LLMClient:
    settings = Settings(llm_enable_thinking=thinking)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=_sse(chunks),
                                       headers={"content-type": "text/event-stream"})
    )
    http = httpx.AsyncClient(transport=transport, base_url="http://llm/v1")
    return LLMClient(settings=settings, client=http)


async def _collect(client: LLMClient) -> str:
    return "".join([piece async for piece in client.stream_chat([{"role": "user", "content": "hỏi"}])])


@pytest.mark.asyncio
async def test_suy_luan_tach_rieng_thi_van_phat_du_cau_tra_loi():
    """Đây là trường hợp làm hỏng bản cũ: không có `</think>` nên cờ lọc không
    bao giờ tắt và client nhận về chuỗi rỗng."""
    client = _client([
        {"role": "assistant", "content": ""},
        {"reasoning": "người dùng hỏi về nhân sự"},
        {"reasoning": ", cần tra bảng kiểm kê"},
        {"content": "Nhân sự tháng 8 là "},
        {"content": "144 người."},
    ])
    assert await _collect(client) == "Nhân sự tháng 8 là 144 người."


@pytest.mark.asyncio
async def test_suy_luan_nam_lan_trong_content_thi_bi_cat_den_het_the_dong():
    client = _client([
        {"content": "cần đọc mục I "},
        {"content": "rồi đối chiếu</think>Nhân sự là "},
        {"content": "144 người."},
    ])
    assert await _collect(client) == "Nhân sự là 144 người."


@pytest.mark.asyncio
async def test_model_tra_loi_thang_khong_suy_luan_thi_khong_mat_chu():
    """Bật suy luận nhưng model không sinh khối nào: phần giữ lại chính là câu
    trả lời, không được im lặng bỏ đi."""
    client = _client([{"content": "Nhân sự là "}, {"content": "144 người."}])
    assert await _collect(client) == "Nhân sự là 144 người."


@pytest.mark.asyncio
async def test_tat_suy_luan_thi_phat_thang_tung_manh():
    client = _client([{"content": "144 "}, {"content": "người."}], thinking=False)
    assert await _collect(client) == "144 người."
