"""Client gọi LLM qua endpoint OpenAI-compatible của vLLM.

Chỉ dùng httpx nên không phụ thuộc SDK riêng; `vllm serve <model>` mặc định
phục vụ /v1/chat/completions tại cổng 8000.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

Message = dict[str, str]

# Model dòng Qwen3/R1 có thể trả kèm khối suy luận; câu trả lời cuối nằm sau nó.
_THINK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


class LLMError(RuntimeError):
    """Lỗi khi gọi LLM (mạng, HTTP, hoặc phản hồi không hợp lệ)."""


@dataclass(slots=True)
class ToolCall:
    """Một lời gọi tool do model đề xuất."""

    id: str
    name: str
    arguments: dict[str, Any]

    def signature(self) -> str:
        """Chữ ký để phát hiện model gọi lặp đúng một thứ."""
        return f"{self.name}({json.dumps(self.arguments, sort_keys=True, ensure_ascii=False)})"


@dataclass(slots=True)
class AssistantTurn:
    """Một lượt trả lời: hoặc là chữ, hoặc là yêu cầu gọi tool."""

    content: str
    reasoning: str
    tool_calls: list[ToolCall]
    raw: dict[str, Any]

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def strip_reasoning(text: str) -> str:
    """Bỏ phần suy luận, chấp nhận cả khi thiếu thẻ mở.

    Chat template của Qwen3.5/3.6 chèn sẵn `<think>` vào CUỐI PROMPT, nên thứ model
    sinh ra chỉ có thẻ đóng: "suy luận...</think>\\n\\ncâu trả lời". Bắt theo cặp thẻ
    đầy đủ sẽ trượt và trả về nguyên phần lý luận.
    """
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip()
    # Sinh bị cắt giữa chừng: có thẻ mở mà chưa kịp đóng.
    cleaned = _THINK_RE.sub("", text)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


class LLMClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings or get_settings()
        self._client = client or httpx.AsyncClient(
            base_url=self.settings.llm_base_url.rstrip("/"),
            timeout=httpx.Timeout(self.settings.llm_timeout, connect=10.0),
            headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ payload -- #
    def _payload(
        self,
        messages: list[Message],
        *,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        extra: dict[str, Any] | None,
        thinking: bool | None = None,
    ) -> dict[str, Any]:
        cfg = self.settings
        payload: dict[str, Any] = {
            "model": model or cfg.llm_model,
            "messages": messages,
            "temperature": cfg.llm_temperature if temperature is None else temperature,
            "max_tokens": max_tokens or cfg.llm_max_tokens,
            "stream": stream,
        }
        # Con số max_tokens ở nơi gọi là độ dài mong muốn của CÂU TRẢ LỜI. Khối suy
        # luận được cấp hạn mức riêng cộng thêm, vì nó tiêu token trước khi model bắt
        # đầu trả lời - trừ chung một hạn mức thì lời gọi 300 token sẽ bị cắt giữa
        # phần lý luận và `content` trả về rỗng.
        if self.thinking_enabled(thinking):
            payload["max_tokens"] += cfg.llm_thinking_budget
        else:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if extra:
            payload.update(extra)
        return payload

    def thinking_enabled(self, thinking: bool | None) -> bool:
        """`None` = theo cấu hình chung; True/False = nơi gọi quyết định.

        Có tác vụ hợp với suy luận, có tác vụ không: đo trên workflow 2 cho thấy bật
        suy luận thì bước soát chữ nghĩa tìm được 0 lỗi thay vì 3.
        """
        return self.settings.llm_enable_thinking if thinking is None else thinking

    # --------------------------------------------------------------- chat -- #
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any], timeout: float | None = None) -> httpx.Response:
        response = await self._client.post(
            "/chat/completions", json=payload, timeout=timeout or self.settings.llm_timeout
        )
        response.raise_for_status()
        return response

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        extra: dict[str, Any] | None = None,
        thinking: bool | None = None,
    ) -> str:
        payload = self._payload(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, stream=False, extra=extra, thinking=thinking,
        )
        try:
            response = await self._post(payload, timeout=timeout)
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM trả về HTTP {exc.response.status_code}: {exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Không gọi được LLM tại {self.settings.llm_base_url}: {exc}") from exc

        try:
            choice = data["choices"][0]
            content = strip_reasoning(choice["message"]["content"] or "")
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Phản hồi LLM không đúng định dạng: {str(data)[:300]}") from exc

        # Suy luận ăn hết hạn mức trước khi model kịp trả lời. Không nói rõ thì lỗi
        # nổi lên thành "không phân tích được JSON" và người đọc log đi tìm nhầm chỗ.
        if not content and choice.get("finish_reason") == "length":
            raise LLMError(
                "Model dùng hết token cho phần suy luận, chưa kịp trả lời. "
                "Tác vụ phân loại/trích xuất nên gọi với thinking=False."
            )
        return content

    async def chat_json(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        thinking: bool | None = None,
    ) -> Any:
        """Ép model trả JSON; tự bóc khối ```json nếu model vẫn bọc markdown."""
        raw = await self.chat(
            messages, model=model, temperature=temperature, max_tokens=max_tokens,
            timeout=timeout, extra={"response_format": {"type": "json_object"}},
            thinking=thinking,
        )
        return parse_json_response(raw)

    async def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        thinking: bool | None = None,
    ) -> "AssistantTurn":
        """Một lượt của vòng lặp agent: model hoặc trả lời, hoặc đòi gọi tool.

        Server phải chạy kèm `--tool-call-parser` thì `tool_calls` mới được tách ra
        khỏi phần chữ; thiếu nó model vẫn sinh XML tool call nhưng nằm lẫn trong
        `content` và không ai gọi được.
        """
        payload = self._payload(
            messages, model=model, temperature=temperature,  # type: ignore[arg-type]
            max_tokens=max_tokens, stream=False,
            extra={"tools": tools, "tool_choice": tool_choice}, thinking=thinking,
        )
        try:
            response = await self._post(payload)
            data = response.json()
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM trả về HTTP {exc.response.status_code}: "
                           f"{exc.response.text[:300]}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Không gọi được LLM tại {self.settings.llm_base_url}: {exc}") from exc

        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Phản hồi LLM không đúng định dạng: {str(data)[:300]}") from exc

        calls: list[ToolCall] = []
        for raw in message.get("tool_calls") or []:
            function = raw.get("function") or {}
            arguments = function.get("arguments")
            # Tham số về dưới dạng chuỗi JSON; hỏng thì coi như model gọi sai, để
            # vòng lặp báo lỗi lại cho model chứ không làm chết cả request.
            if isinstance(arguments, str):
                try:
                    arguments = parse_json_response(arguments) if arguments.strip() else {}
                except LLMError:
                    arguments = {"__parse_error__": arguments}
            calls.append(ToolCall(id=str(raw.get("id") or ""),
                                  name=str(function.get("name") or ""),
                                  arguments=arguments if isinstance(arguments, dict) else {}))

        return AssistantTurn(
            content=strip_reasoning(message.get("content") or ""),
            reasoning=str(message.get("reasoning") or ""),
            tool_calls=calls,
            raw=message,
        )

    async def stream_chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tool_choice: str = "auto",
        thinking: bool | None = None,
        on_delta: Callable[[str], None] | None = None,
        on_reasoning: Callable[[str], None] | None = None,
    ) -> "AssistantTurn":
        """Như `chat_with_tools` nhưng phát chữ ra ngay khi model sinh.

        Một lượt của vòng lặp agent mất 8-15 giây. Ở lượt CUỐI - lượt model thôi
        đòi tool và viết câu trả lời - toàn bộ thời gian đó là chữ đã sẵn sàng mà
        người dùng chưa được thấy. `on_delta` trả từng mảnh ngay lúc nó về.

        Model có thể viết vài câu dẫn rồi mới quyết định gọi tool. Mảnh đã phát
        lúc đó không phải câu trả lời, nên người gọi nhận `wants_tools` và phải tự
        xoá phần đã hiện - xem `answer_reset` trong vòng lặp agent.

        Trả về cùng một `AssistantTurn` như bản không stream, nên phần còn lại của
        vòng lặp không biết mình đang chạy ở chế độ nào.
        """
        payload = self._payload(
            messages, model=model, temperature=temperature,  # type: ignore[arg-type]
            max_tokens=max_tokens, stream=True,
            extra={"tools": tools, "tool_choice": tool_choice}, thinking=thinking,
        )

        content: list[str] = []
        reasoning: list[str] = []
        # Mảnh tool_call về rời rạc theo `index`: tên ở mảnh đầu, tham số nối dần
        # thành một chuỗi JSON. Gom theo index chứ không theo thứ tự đến.
        partial: dict[int, dict[str, Any]] = {}
        in_reasoning = self.thinking_enabled(thinking)
        pending: list[str] = []
        emitted = False

        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw_data = line[5:].strip()
                    if not raw_data or raw_data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw_data)
                        delta = chunk["choices"][0].get("delta", {})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

                    for item in delta.get("tool_calls") or []:
                        slot = partial.setdefault(
                            int(item.get("index") or 0), {"id": "", "name": "", "args": []})
                        if item.get("id"):
                            slot["id"] = str(item["id"])
                        function = item.get("function") or {}
                        if function.get("name"):
                            slot["name"] = str(function["name"])
                        if function.get("arguments"):
                            slot["args"].append(str(function["arguments"]))

                    if (think := delta.get("reasoning") or delta.get("reasoning_content")):
                        in_reasoning = False
                        pending.clear()
                        reasoning.append(str(think))
                        if on_reasoning:
                            on_reasoning(str(think))
                        continue

                    piece = delta.get("content") or ""
                    if not piece:
                        continue
                    if "<think>" in piece:
                        in_reasoning = True
                        pending.clear()
                        piece = piece.split("<think>", 1)[0]
                    if in_reasoning:
                        if "</think>" not in piece:
                            pending.append(piece)
                            continue
                        in_reasoning = False
                        pending.clear()
                        piece = piece.split("</think>", 1)[1]
                    if not piece:
                        continue
                    content.append(piece)
                    # Không phát khi: (a) model đã quyết định gọi tool - chữ đang
                    # ra là câu dẫn chứ không phải câu trả lời; (b) mảnh chỉ toàn
                    # khoảng trắng mà chưa có chữ nào - model hay mở lượt bằng
                    # "\n\n", phát ra thì màn hình có một dòng trống đứng trước
                    # câu trả lời.
                    if on_delta and not partial and (emitted or piece.strip()):
                        emitted = True
                        on_delta(piece)

            if in_reasoning and pending:
                text = "".join(pending)
                content.append(text)
                if on_delta and not partial and (emitted or text.strip()):
                    emitted = True
                    on_delta(text)
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM trả về HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Mất kết nối tới LLM: {exc}") from exc

        calls: list[ToolCall] = []
        raw_calls: list[dict[str, Any]] = []
        for index in sorted(partial):
            slot = partial[index]
            if not slot["name"]:
                continue
            arguments_text = "".join(slot["args"]) or "{}"
            try:
                arguments = parse_json_response(arguments_text) if arguments_text.strip() else {}
            except LLMError:
                logger.warning("Tham số tool %s không phải JSON: %s", slot["name"],
                               arguments_text[:200])
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            call_id = slot["id"] or f"call_{index}"
            calls.append(ToolCall(id=call_id, name=slot["name"], arguments=arguments))
            raw_calls.append({"id": call_id, "type": "function",
                              "function": {"name": slot["name"], "arguments": arguments_text}})

        text = "".join(content).strip()
        raw_message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if raw_calls:
            raw_message["tool_calls"] = raw_calls
        return AssistantTurn(content=text, reasoning="".join(reasoning).strip(),
                             tool_calls=calls, raw=raw_message)

    async def stream_chat(
        self,
        messages: list[Message],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        extra: dict[str, Any] | None = None,
        thinking: bool | None = None,
    ) -> AsyncIterator[str]:
        """Sinh từng mảnh nội dung (đã bỏ phần suy luận nếu model có)."""
        payload = self._payload(
            messages, model=model, temperature=temperature,
            max_tokens=max_tokens, stream=True, extra=extra, thinking=thinking,
        )
        # Bật suy luận thì luồng bắt đầu NGAY BÊN TRONG khối think (template đã chèn
        # thẻ mở vào prompt), nên phải lọc từ mảnh đầu tiên chứ không chờ thấy <think>.
        in_reasoning = self.thinking_enabled(thinking)
        # Mảnh giữ lại khi chưa biết phần suy luận đi đường nào: model có thể không
        # sinh khối suy luận nào cả, lúc đó những mảnh này chính là câu trả lời.
        pending: list[str] = []
        try:
            async with self._client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                        delta = chunk["choices"][0].get("delta", {})
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    # Server chạy kèm `--reasoning-parser`: phần suy luận đi riêng ở
                    # `reasoning`/`reasoning_content`, còn `content` đã là câu trả lời
                    # sạch và KHÔNG có thẻ </think> nào để mà chờ. Không tắt cờ lọc ở
                    # đây thì mọi mảnh đều bị bỏ và client nhận về chuỗi rỗng.
                    if delta.get("reasoning") or delta.get("reasoning_content"):
                        in_reasoning = False
                        pending.clear()
                        continue
                    piece = delta.get("content") or ""
                    if not piece:
                        continue
                    # Lọc khối <think> ngay trên luồng để client không thấy.
                    if "<think>" in piece:
                        in_reasoning = True
                        pending.clear()
                        piece = piece.split("<think>", 1)[0]
                    if in_reasoning:
                        if "</think>" not in piece:
                            pending.append(piece)
                            continue
                        in_reasoning = False
                        pending.clear()
                        piece = piece.split("</think>", 1)[1]
                    if piece:
                        yield piece
            # Hết luồng mà không thấy </think> lẫn mảnh suy luận riêng: model đã
            # trả lời thẳng, phần giữ lại là câu trả lời chứ không phải suy luận.
            if in_reasoning and pending:
                text = "".join(pending).strip()
                if text:
                    yield text
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"LLM trả về HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Mất kết nối tới LLM: {exc}") from exc

    async def health(self) -> bool:
        try:
            response = await self._client.get("/models", timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False


def parse_json_response(raw: str) -> Any:
    """Bóc JSON từ phản hồi model, chấp nhận cả khi bị bọc trong ```json."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Vớt vát: lấy object/array đầu tiên xuất hiện trong chuỗi.
        match = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
    raise LLMError(f"Không phân tích được JSON từ phản hồi: {raw[:300]}")


_llm: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm
    if _llm is None:
        _llm = LLMClient()
    return _llm
