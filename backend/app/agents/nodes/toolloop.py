"""Vòng lặp agent: model tự chọn công cụ cho tới khi trả lời được.

    model ─(tool_calls)─> gọi tool ─> nhét kết quả vào hội thoại ─┐
      ▲                                                            │
      └────────────────────────────────────────────────────────────┘
      └─(không còn tool_calls)─> đối chiếu số ─> câu trả lời

Khác năm workflow tất định ở chỗ: ở đó CODE chọn tool, ở đây MODEL chọn. Đổi lại
tính tiên đoán lấy khả năng ghép nhiều tool cho câu hỏi không có sẵn quy trình.

Bốn điều kiện dừng, vì một vòng lặp không có trần là một vòng lặp vô hạn đang chờ
xảy ra:
    1. model không đòi tool nữa            -> đó là câu trả lời
    2. chạm MAX_STEPS                      -> cắt, báo rõ đã cắt
    3. gọi lặp đúng một thứ hai lần liền   -> model đang kẹt
    4. tool lỗi quá MAX_TOOL_ERRORS lần    -> model đang mò tham số

Van chống bịa số tái dùng nguyên của workflow 3/4/5: mọi con số trong câu trả lời
cuối phải truy được về kết quả tool đã gọi.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from app.agents import references
from app.agents.prompts import AGENT_SYSTEM
from app.core.config import get_settings
from app.documents.verify import check_numbers, collect_known_numbers
from app.services.llm import AssistantTurn, LLMError, ToolCall, get_llm
from app.tools.base import ToolError
from app.tools.registry import call_tool, is_side_effect, tool_schemas

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_TOOL_ERRORS = 3
# Kết quả tool nhét vào hội thoại phải cắt bớt: một breakdown 50 đơn vị sẽ đẩy
# hết ngữ cảnh ra ngoài sau vài vòng.
MAX_RESULT_CHARS = 6000


def _render_result(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"… (đã cắt, tổng {len(text)} ký tự)"


def _tool_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}


async def _execute(call: ToolCall) -> tuple[str, Any | None]:
    """Gọi một tool. Lỗi tham số được trả NGƯỢC cho model để nó tự sửa."""
    try:
        result = await call_tool(call.name, **call.arguments)
    except ToolError as exc:
        return f"LỖI: {exc}", None
    except Exception as exc:  # noqa: BLE001 - tool hỏng không được làm chết vòng lặp
        logger.exception("Tool %s hỏng", call.name)
        return f"LỖI: công cụ gặp sự cố: {exc}", None
    return _render_result(result), result


async def run_tool_loop(
    request: str,
    history: list[dict[str, str]] | None = None,
    max_steps: int = MAX_STEPS,
) -> dict[str, Any]:
    """Chạy vòng lặp tới khi model trả lời hoặc chạm một điều kiện dừng."""
    cfg = get_settings()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": AGENT_SYSTEM.format(today=date.today().strftime("%d/%m/%Y"))}
    ]
    for turn in (history or [])[-4:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": request})

    schemas = tool_schemas()
    tool_log: list[dict[str, Any]] = []
    known_numbers: set[str] = set()
    last_signature = ""
    tool_errors = 0
    stop_reason = "hoàn thành"
    answer = ""
    pending_approval: dict[str, Any] | None = None

    for step in range(1, max_steps + 1):
        try:
            turn: AssistantTurn = await get_llm().chat_with_tools(messages, schemas)
        except LLMError as exc:
            logger.warning("Vòng lặp agent dừng vì LLM lỗi: %s", exc)
            return _result(answer="Hệ thống chưa gọi được mô hình ngôn ngữ, vui lòng thử lại.",
                           tool_log=tool_log, steps=step - 1, stop_reason="lỗi LLM",
                           error=str(exc))

        if not turn.wants_tools:
            answer = turn.content
            stop_reason = "hoàn thành"
            break

        messages.append(turn.raw)

        for call in turn.tool_calls:
            # Dừng 3: model lặp đúng một lời gọi -> nói thẳng ra thay vì quay vòng.
            signature = call.signature()
            if signature == last_signature:
                stop_reason = "gọi lặp"
                logger.info("Agent gọi lặp %s, dừng", signature[:120])
                messages.append(_tool_message(
                    call, "LỖI: lời gọi này vừa được thực hiện y hệt. "
                          "Hãy dùng kết quả đã có để trả lời."))
                break
            last_signature = signature

            # Tool ghi: chưa có cổng duyệt nên chặn lại và báo ra ngoài.
            if is_side_effect(call.name):
                pending_approval = {"tool": call.name, "arguments": call.arguments}
                stop_reason = "chờ duyệt"
                logger.info("Agent xin gọi tool ghi %s, dừng chờ người duyệt", call.name)
                break

            content, result = await _execute(call)
            # Đánh số ngay khi có kết quả: model không biết trước mình sẽ gọi bao
            # nhiêu lần, nên số phải phát dần theo thứ tự gọi.
            ref_id = len(tool_log) + 1
            tool_log.append({"step": step, "ref_id": ref_id, "tool": call.name,
                             "arguments": call.arguments, "ok": result is not None,
                             "preview": content[:600]})
            if result is None:
                tool_errors += 1
            else:
                collect_known_numbers(result, known_numbers)
            messages.append(_tool_message(call, f"[{ref_id}] {content}"))

        if stop_reason in ("chờ duyệt", "gọi lặp"):
            break
        # Dừng 4: model mò tham số mãi không ra.
        if tool_errors >= MAX_TOOL_ERRORS:
            stop_reason = "quá nhiều lỗi công cụ"
            break
    else:
        # Dừng 2: hết vòng mà model vẫn đòi gọi tool.
        stop_reason = "chạm trần số bước"

    # Van số: chỉ kiểm khi thực sự có câu trả lời và đã gọi được tool nào đó.
    validation: dict[str, Any] = {"status": "skipped", "issues": []}
    # Marker phải bị gỡ TRƯỚC khi đối chiếu: "[1]" mà lọt vào `check_numbers` sẽ bị
    # đọc thành con số 1 không truy được về kết quả tool nào.
    answer_plain = references.strip_markers(answer)
    if answer and known_numbers:
        # Kỳ báo cáo nhắc trong câu hỏi cũng là số hợp lệ.
        known_numbers |= collect_known_numbers(request)
        check = check_numbers(answer_plain, known_numbers, "agent")
        validation = {
            "status": "failed" if not check.ok else "passed",
            "issues": ([{"type": "unverified_number", "section": "agent",
                         "numbers": check.unverified, "severity": "error",
                         "quote": answer_plain[:160]}] if not check.ok else []),
            "checked_numbers": len(known_numbers),
        }
        if not check.ok:
            logger.warning("Agent viết số không truy được: %s", check.unverified)

    # Số trong hội thoại là thứ tự gọi tool; đánh lại 1..n theo thứ tự xuất hiện
    # để câu trả lời không mang "[2][5]" khi chỉ có hai nguồn.
    (answer,), used_refs = references.renumber([answer], references.from_tool_log(tool_log)) \
        if references.MARKER_RE.search(answer) else ([answer], [])
    return _result(answer=answer, tool_log=tool_log, steps=len(tool_log),
                   stop_reason=stop_reason, validation=validation,
                   pending_approval=pending_approval, refs=used_refs)


def _result(
    answer: str,
    tool_log: list[dict[str, Any]],
    steps: int,
    stop_reason: str,
    validation: dict[str, Any] | None = None,
    pending_approval: dict[str, Any] | None = None,
    refs: list[references.Reference] | None = None,
    error: str = "",
) -> dict[str, Any]:
    return {
        "answer": answer,
        "refs": refs or [],
        "tool_log": tool_log,
        "tools_called": [item["tool"] for item in tool_log],
        "steps": steps,
        "stop_reason": stop_reason,
        "validation": validation or {"status": "skipped", "issues": []},
        "pending_approval": pending_approval,
        "error": error,
    }
