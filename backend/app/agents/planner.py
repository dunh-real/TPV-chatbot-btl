"""Lập kế hoạch: một yêu cầu tiếng Việt -> một hoặc vài bước có thứ tự.

Định tuyến (`app.agents.router`) trả lời câu hỏi "yêu cầu này thuộc nghiệp vụ
nào". Nó chỉ chọn được MỘT nghiệp vụ, nên "tổng hợp quân số tháng 8 rồi làm
slide" luôn mất một nửa. Tầng này trả lời câu hỏi rộng hơn: "để làm xong việc
này thì phải chạy những gì, theo thứ tự nào, cái nào chạy song song được".

    yêu cầu ─> LLM ─> [{intent, request, depends_on}, ...] ─> kiểm tra ─> Plan
                 └─(hỏng)─> classify_intent -> kế hoạch một bước

Kế hoạch một bước là trường hợp thường gặp nhất và cũng là đường lùi: mọi lỗi
của tầng này - LLM chết, JSON hỏng, ý định lạ, phụ thuộc vòng - đều quy về gọi
`classify_intent` như trước. Thêm khả năng phân rã mà không đánh đổi độ tin cậy
của đường cũ.

Ba ràng buộc đặt ở code chứ không gửi gắm vào prompt:

    1. Không có file đính kèm -> không bước nào được là `document`.
    2. `depends_on` chỉ trỏ ngược về bước đứng trước -> không thể có chu trình.
    3. Trần MAX_STEPS bước -> model không kéo dài kế hoạch vô hạn.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.agents import progress
from app.agents.history import format_history
from app.agents.prompts import PLANNER_SYSTEM, PLANNER_USER
from app.agents.router import INTENTS, MIN_CONFIDENCE, Intent, classify_intent
from app.core.config import get_settings
from app.services.llm import LLMError, get_llm
from app.tools.registry import describe_tools

logger = logging.getLogger(__name__)

# Trần số bước. Ba là đủ cho mọi yêu cầu ghép mà người dùng thật sự gõ ra
# ("tổng hợp rồi làm slide", "soát văn bản này rồi soạn công văn trả lời"); dài
# hơn thì đằng nào người dùng cũng phải xem lại từng bước một.
MAX_STEPS = 3

# Những ý định KHÔNG bao giờ nên xuất hiện hai lần trong một kế hoạch: chúng sinh
# ra file, chạy hai lần là hai file gần giống nhau.
SINGLE_USE: frozenset[str] = frozenset({"draft", "report", "presentation", "document"})


@dataclass(slots=True)
class PlanStep:
    """Một bước: chạy nghiệp vụ `intent` với yêu cầu con `request`."""

    id: str
    intent: Intent
    request: str
    depends_on: list[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "intent": self.intent, "request": self.request,
                "depends_on": list(self.depends_on), "reason": self.reason}


@dataclass(slots=True)
class Plan:
    """Kế hoạch đã qua kiểm tra: chạy được ngay, không cần dò lại."""

    steps: list[PlanStep] = field(default_factory=list)
    confidence: float = 0.0
    clarify: str = ""
    reason: str = ""
    source: str = "llm"          # llm | router | rule

    @property
    def primary(self) -> PlanStep | None:
        """Bước quyết định câu trả lời chính - bước cuối của chuỗi phụ thuộc."""
        return self.steps[-1] if self.steps else None

    @property
    def is_multi(self) -> bool:
        return len(self.steps) > 1

    def waves(self) -> list[list[PlanStep]]:
        """Nhóm các bước thành từng đợt chạy song song được.

        Một bước vào được đợt hiện tại khi mọi thứ nó phụ thuộc đã nằm ở đợt
        trước. Bước không phụ thuộc gì thì luôn ở đợt đầu - đó là chỗ duy nhất
        agent lấy lại được thời gian: hai truy vấn độc lập không có lý do gì
        phải nối đuôi nhau.
        """
        remaining = list(self.steps)
        done: set[str] = set()
        result: list[list[PlanStep]] = []
        while remaining:
            wave = [s for s in remaining if set(s.depends_on) <= done]
            if not wave:
                # Không bao giờ xảy ra sau `_validate` - nhưng nếu xảy ra thì
                # chạy nốt tuần tự còn hơn treo vòng lặp.
                logger.warning("Kế hoạch còn bước không giải được phụ thuộc, chạy tuần tự")
                wave = remaining[:1]
            result.append(wave)
            done |= {s.id for s in wave}
            remaining = [s for s in remaining if s not in wave]
        return result

    def as_dict(self) -> dict[str, Any]:
        return {"steps": [s.as_dict() for s in self.steps],
                "confidence": round(self.confidence, 2), "clarify": self.clarify,
                "reason": self.reason, "source": self.source}


def single_step(intent: Intent, request: str, confidence: float, reason: str = "",
                clarify: str = "", source: str = "router") -> Plan:
    """Kế hoạch một bước - hình dạng của mọi yêu cầu đơn giản và mọi đường lùi."""
    return Plan(steps=[PlanStep(id="s1", intent=intent, request=request, reason=reason)],
                confidence=confidence, clarify=clarify, reason=reason, source=source)


async def _fallback(request: str, has_file: bool, history: list[dict[str, str]] | None) -> Plan:
    """Đường lùi: định tuyến một bước như trước khi có tầng lập kế hoạch."""
    routed = await classify_intent(request, has_file=has_file, history=history)
    return single_step(routed.intent, request, routed.confidence, routed.reason,
                       routed.clarify, source=routed.source)


def _validate(raw_steps: Any, request: str, has_file: bool) -> list[PlanStep]:
    """Lọc kế hoạch thô của model thành thứ chạy được, hoặc trả về rỗng.

    Rỗng nghĩa là "không dùng được, hãy lùi về định tuyến" chứ không phải "không
    có việc gì để làm".
    """
    if not isinstance(raw_steps, list) or not raw_steps:
        return []

    steps: list[PlanStep] = []
    seen_intents: set[str] = set()
    for index, item in enumerate(raw_steps[:MAX_STEPS]):
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "").lower()
        if intent not in INTENTS:
            logger.info("Bỏ bước có ý định lạ %r", intent)
            continue
        # Ràng buộc 1: workflow 2 cần một file có thật để parse.
        if intent == "document" and not has_file:
            logger.info("Bỏ bước 'document' vì không có file đính kèm")
            continue
        # Hai bước cùng sinh file là dấu hiệu model tách nhầm một việc thành hai.
        if intent in SINGLE_USE and intent in seen_intents:
            logger.info("Bỏ bước %r trùng lặp trong kế hoạch", intent)
            continue
        seen_intents.add(intent)

        sub_request = str(item.get("request") or "").strip() or request
        step_id = f"s{len(steps) + 1}"
        # Ràng buộc 2: chỉ giữ phụ thuộc trỏ NGƯỢC về bước đã có. Model hay trả
        # về id theo cách đánh số của riêng nó; chuẩn hoá về id mình vừa cấp.
        known = {s.id for s in steps}
        depends = [d for d in _as_ids(item.get("depends_on"), index) if d in known]
        steps.append(PlanStep(id=step_id, intent=intent, request=sub_request,  # type: ignore[arg-type]
                              depends_on=depends, reason=str(item.get("reason") or "")))
    return steps


def _as_ids(value: Any, index: int) -> list[str]:
    """Chấp nhận cả `["s1"]`, `[1]` và `"s1"` - model viết kiểu nào cũng có."""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in items:
        text = str(item).strip().lower()
        if not text:
            continue
        ids.append(text if text.startswith("s") else f"s{text}")
    # Bước đầu tiên không thể phụ thuộc vào gì.
    return [] if index == 0 else ids


async def make_plan(
    request: str,
    has_file: bool = False,
    history: list[dict[str, str]] | None = None,
) -> Plan:
    """Lập kế hoạch cho một yêu cầu. Không bao giờ ném lỗi - luôn có đường lùi."""
    request = (request or "").strip()
    progress.emit("thinking", text="Đang đọc yêu cầu và tách thành các bước…")
    if not request:
        return Plan(steps=[], confidence=0.0, clarify="Bạn muốn hỏi hoặc làm gì?",
                    reason="Yêu cầu rỗng", source="rule")

    try:
        data = await get_llm().chat_json(
            [
                {"role": "system", "content": PLANNER_SYSTEM.format(
                    tools=describe_tools(), has_file="có" if has_file else "không",
                    max_steps=MAX_STEPS)},
                {"role": "user", "content": PLANNER_USER.format(
                    history=format_history(history), request=request)},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=600,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001 - hỏng thì vẫn phải làm việc
        logger.warning("Lập kế hoạch bằng LLM thất bại, lùi về định tuyến: %s", exc)
        return await _fallback(request, has_file, history)

    if not isinstance(data, dict):
        logger.warning("Kế hoạch không phải JSON object, lùi về định tuyến")
        return await _fallback(request, has_file, history)

    steps = _validate(data.get("steps"), request, has_file)
    if not steps:
        logger.info("Kế hoạch rỗng sau khi kiểm tra, lùi về định tuyến")
        return await _fallback(request, has_file, history)

    confidence = float(data.get("confidence") or 0.0)
    clarify = str(data.get("clarify") or "")
    plan = Plan(steps=steps, confidence=confidence, clarify=clarify,
                reason=str(data.get("reason") or ""), source="llm")

    # Kế hoạch một bước mà model tự tin thấp: đối chiếu lại với lớp từ khoá, đúng
    # như định tuyến vẫn làm. Kế hoạch nhiều bước thì không đối chiếu được - lớp
    # từ khoá chỉ biết chọn một nghiệp vụ.
    if not plan.is_multi and confidence < MIN_CONFIDENCE:
        fallback = await _fallback(request, has_file, history)
        if fallback.steps and fallback.steps[0].intent != steps[0].intent:
            logger.info("Kế hoạch một bước %r (%.2f) thua tín hiệu từ khoá %r",
                        steps[0].intent, confidence, fallback.steps[0].intent)
            fallback.clarify = fallback.clarify or clarify
            return fallback
    return plan
