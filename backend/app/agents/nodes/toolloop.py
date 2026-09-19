"""Vòng lặp agent: model tự chọn công cụ cho tới khi trả lời được.

    model ─(tool_calls)─> gọi tool SONG SONG ─> nhét kết quả vào hội thoại ─┐
      ▲                                                                      │
      └──────────────────────────────────────────────────────────────────────┘
      └─(không còn tool_calls)─> đối chiếu số ─> câu trả lời

Khác năm workflow tất định ở chỗ: ở đó CODE chọn tool, ở đây MODEL chọn. Đổi lại
tính tiên đoán lấy khả năng ghép nhiều tool cho câu hỏi không có sẵn quy trình.

Model phát nhiều lời gọi trong một lượt thì chúng được chạy CÙNG LÚC: quân số của
ba đơn vị là ba truy vấn độc lập, không có lý do gì phải nối đuôi nhau. Chỉ tool
có tác dụng phụ (sinh file) là bị tách riêng - chạy song song một việc ghi với
một việc đọc thì không còn nói được thứ tự nào đã xảy ra.

Năm điều kiện dừng, vì một vòng lặp không có trần là một vòng lặp vô hạn đang chờ
xảy ra:
    1. model không đòi tool nữa            -> đó là câu trả lời
    2. chạm MAX_STEPS                      -> cắt, báo rõ đã cắt
    3. cả lượt chỉ toàn lời gọi đã chạy    -> model đang kẹt, nhắc một lần rồi dừng
    4. tool lỗi quá MAX_TOOL_ERRORS lần    -> model đang mò tham số
    5. tool xin ghi file                   -> dừng chờ người duyệt

Lời gọi trùng KHÔNG giết cả phiên: nó bị bỏ qua, những lời gọi còn lại trong lượt
vẫn chạy, và model được nhắc bằng một thông báo tool. Trước đây `break` ở đây làm
mất luôn câu trả lời dù dữ liệu đã nằm sẵn trong `tool_log`.

Van chống bịa số tái dùng nguyên của workflow 3/4/5: mọi con số trong câu trả lời
cuối phải truy được về kết quả tool đã gọi.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from app.agents import progress, references
from app.agents.prompts import AGENT_SYSTEM
from app.core.config import get_settings
from app.services import errors
from app.documents.verify import check_numbers, collect_known_numbers
from app.services.llm import AssistantTurn, LLMError, ToolCall, get_llm
from app.tools.base import ToolError
from app.tools.registry import call_tool, is_side_effect, tool_schemas

logger = logging.getLogger(__name__)

MAX_STEPS = 8
MAX_TOOL_ERRORS = 3
# Số lượt liên tiếp mà model chỉ phát lại những lời gọi đã chạy. Một lần là nhắc,
# hai lần là nó không thoát được và sẽ không thoát được ở lần thứ ba.
MAX_REPEAT_TURNS = 2
# Kết quả tool nhét vào hội thoại phải cắt bớt: một breakdown 50 đơn vị sẽ đẩy
# hết ngữ cảnh ra ngoài sau vài vòng.
MAX_RESULT_CHARS = 6000

# Dấu hiệu "gọi đúng nhưng không có gì": tool chạy xong, không lỗi, mà mọi chỉ
# tiêu đều rỗng. Khác hẳn lỗi tham số, nên phải báo cho model bằng lời khác - nếu
# không nó sẽ gọi lại y hệt rồi kết luận "bằng 0".
EMPTY_HINT = ("NGUỒN TRỐNG: công cụ chạy đúng nhưng không có dữ liệu cho tham số này. "
              "Đừng gọi lại y hệt - đổi kỳ, đổi đơn vị, hoặc thử công cụ khác "
              "(vd: search_documents để tìm trong báo cáo các đơn vị đã nộp).")


def _render_result(result: Any) -> str:
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS] + f"… (đã cắt, tổng {len(text)} ký tự)"


def _tool_message(call: ToolCall, content: str) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.id, "name": call.name, "content": content}


def looks_empty(result: Any) -> bool:
    """Kết quả hợp lệ nhưng rỗng ruột.

    Không dựa vào một khoá cố định nào: các tool số liệu báo trống theo những
    cách khác nhau (`units_with_data` bằng 0, `breakdown` rỗng, danh sách rỗng),
    còn tool tra cứu thì trả thẳng list rỗng.
    """
    if result is None or result == [] or result == {}:
        return True
    if isinstance(result, list):
        return not result
    if not isinstance(result, dict):
        return False

    if result.get("units_with_data") == 0:
        return True
    # Có ít nhất một khoá "nội dung" mang dữ liệu thật thì không coi là trống.
    payload_keys = ("breakdown", "hits", "chunks", "items", "results", "metrics", "rows")
    present = [key for key in payload_keys if key in result]
    if present:
        return all(not result.get(key) for key in present)
    return False


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


async def _execute_batch(calls: list[ToolCall]) -> list[tuple[str, Any | None]]:
    """Chạy một lượt lời gọi. Tool đọc chạy song song, tool ghi chạy một mình.

    `return_exceptions` không cần: `_execute` đã nuốt mọi lỗi thành chuỗi "LỖI:"
    để model đọc được. Ngoại lệ lọt ra đây là lỗi lập trình, nên để nó nổ.
    """
    if len(calls) == 1:
        return [await _execute(calls[0])]
    if any(is_side_effect(call.name) for call in calls):
        return [await _execute(call) for call in calls]
    logger.info("Chạy song song %d lời gọi: %s", len(calls), [c.name for c in calls])
    return list(await asyncio.gather(*(_execute(call) for call in calls)))


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
    # Tập chữ ký, không phải một chữ ký: chu trình A -> B -> A cũng là kẹt, mà
    # chỉ nhớ lời gọi ngay trước thì không bao giờ thấy nó.
    seen_signatures: set[str] = set()
    repeat_turns = 0
    tool_errors = 0
    stop_reason = "hoàn thành"
    answer = ""
    pending_approval: dict[str, Any] | None = None

    for step in range(1, max_steps + 1):
        progress.emit("step_progress", round=step, max_rounds=max_steps,
                      phase="đang suy nghĩ")
        try:
            # Streaming: ở lượt CUỐI - lượt model thôi đòi tool và viết câu trả
            # lời - chữ được đẩy ra ngay thay vì đợi cả lượt xong. Ở những lượt
            # gọi tool thì `on_delta` không phát gì (model đang sinh tool_call,
            # không sinh chữ), nên không có gì thừa lọt ra màn hình.
            phat: list[str] = []

            def _delta(piece: str, _phat: list[str] = phat) -> None:
                _phat.append(piece)
                progress.emit("answer_delta", text=piece)

            turn: AssistantTurn = await get_llm().stream_chat_with_tools(
                messages, schemas, on_delta=_delta)
        except LLMError as exc:
            logger.warning("Vòng lặp agent dừng vì LLM lỗi: %s", exc)
            return _result(answer="Hệ thống chưa gọi được mô hình ngôn ngữ, vui lòng thử lại.",
                           tool_log=tool_log, steps=len(tool_log), stop_reason="lỗi LLM",
                           error=errors.friendly(exc))

        # Suy luận của model được phép lên màn hình; KẾT QUẢ công cụ thì không -
        # số liệu thô chưa qua van `check_numbers` không nên đứng cạnh câu trả lời.
        if turn.reasoning:
            progress.emit("thinking", text=turn.reasoning)
        if turn.wants_tools and phat:
            # Model viết vài câu dẫn rồi mới quyết định gọi tool. Chữ đó đã lên
            # màn hình nhưng không phải câu trả lời - bảo giao diện xoá đi. Xét
            # theo "đã phát mảnh nào chưa" chứ không theo `turn.content`: content
            # đã bị strip nên một câu dẫn toàn khoảng trắng sẽ lọt.
            progress.emit("answer_reset")

        if not turn.wants_tools:
            answer = turn.content
            stop_reason = "hoàn thành"
            break

        messages.append(turn.raw)

        # --- lọc lời gọi trước khi chạy ---------------------------------- #
        to_run: list[ToolCall] = []
        for call in turn.tool_calls:
            signature = call.signature()
            if signature in seen_signatures:
                # Dừng 3: bỏ qua lời gọi này, KHÔNG bỏ những lời gọi còn lại.
                logger.info("Bỏ lời gọi đã chạy: %s", signature[:120])
                messages.append(_tool_message(
                    call, "LỖI: lời gọi này đã được thực hiện y hệt ở bước trước. "
                          "Hãy dùng kết quả đã có, hoặc đổi tham số."))
                continue
            # Dừng 5: tool ghi chưa có cổng duyệt nên chặn lại và báo ra ngoài.
            if is_side_effect(call.name):
                pending_approval = {"tool": call.name, "arguments": call.arguments}
                stop_reason = "chờ duyệt"
                logger.info("Agent xin gọi tool ghi %s, dừng chờ người duyệt", call.name)
                break
            seen_signatures.add(signature)
            to_run.append(call)

        if stop_reason == "chờ duyệt":
            break

        if not to_run:
            # Cả lượt không còn gì để chạy: model đang quay vòng.
            repeat_turns += 1
            if repeat_turns >= MAX_REPEAT_TURNS:
                stop_reason = "gọi lặp"
                logger.info("Agent lặp lại lời gọi cũ %d lượt liền, dừng", repeat_turns)
                break
            continue
        repeat_turns = 0
        # Chỉ TÊN công cụ - không tham số, không kết quả.
        progress.emit("step_progress", round=step, max_rounds=max_steps,
                      phase="đang tra", tools=[call.name for call in to_run])

        # --- chạy và ghi kết quả vào hội thoại ---------------------------- #
        for call, (content, result) in zip(to_run, await _execute_batch(to_run), strict=True):
            # Đánh số ngay khi có kết quả: model không biết trước mình sẽ gọi bao
            # nhiêu lần, nên số phải phát dần theo thứ tự gọi.
            ref_id = len(tool_log) + 1
            empty = result is not None and looks_empty(result)
            tool_log.append({"step": step, "ref_id": ref_id, "tool": call.name,
                             "arguments": call.arguments, "ok": result is not None,
                             "empty": empty, "preview": content[:600]})
            if result is None:
                tool_errors += 1
            else:
                collect_known_numbers(result, known_numbers)
            body = f"{content}\n{EMPTY_HINT}" if empty else content
            messages.append(_tool_message(call, f"[{ref_id}] {body}"))

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
