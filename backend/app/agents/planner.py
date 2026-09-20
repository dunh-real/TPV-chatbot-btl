"""Lập kế hoạch: một yêu cầu tiếng Việt -> một hoặc vài bước có thứ tự.

Định tuyến (`app.agents.router`) trả lời câu hỏi "yêu cầu này thuộc nghiệp vụ
nào". Nó chỉ chọn được MỘT nghiệp vụ, nên "tổng hợp nhân sự tháng 8 rồi làm
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

# Những ý định KHÔNG bao giờ nên xuất hiện hai lần trong một kế hoạch.
#
# Bốn cái đầu vì chúng sinh ra file: chạy hai lần là hai file gần giống nhau.
# `agent` thì khác lý do: vòng lặp công cụ gọi được nhiều tool trong MỘT lượt,
# nên câu hỏi nhiều vế vẫn là một bước. Tách đôi chỉ làm câu trả lời bị ghép
# thành "1. Tra số liệu — ... 2. Tra số liệu — ...", và bước thừa bị bỏ ở đây
# thì `_validate` rơi về kế hoạch một bước, tức là lấy lại nguyên câu người dùng
# hỏi - vế thứ hai không mất đi đâu cả.
SINGLE_USE: frozenset[str] = frozenset({"draft", "report", "presentation",
                                        "document", "agent"})

# Nghiệp vụ tự tra lấy số liệu của mình. Một bước `agent` chạy trước chúng chỉ để
# "lấy số liệu" là thừa.
TU_TRA_SO_LIEU: frozenset[str] = frozenset({"draft", "report", "presentation"})


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
    """Đường lùi: định tuyến một bước như trước khi có tầng lập kế hoạch.

    Đường này KHÔNG đi qua `_validate`, nên mọi chốt đặt trong đó đều không chạy.
    Trước đây nó âm thầm nuốt mất chốt "có file thì đọc file": model trả JSON quá
    dài bị cắt cụt -> rơi vào đây -> chọn `report` -> file người dùng gửi bị vứt
    đi. Vì vậy chốt nào cần đúng cho cả hai đường thì phải gọi lại ở đây.
    """
    routed = await classify_intent(request, has_file=has_file, history=history)
    plan = single_step(routed.intent, request, routed.confidence, routed.reason,
                       routed.clarify, source=routed.source)
    plan.steps = _uu_tien_file_dinh_kem(plan.steps, has_file)
    return plan


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

    steps = _bo_buoc_lay_so_lieu_thua(steps)
    steps = _uu_tien_file_dinh_kem(steps, has_file)

    # Kế hoạch chỉ có một bước thì câu của người dùng ĐÃ tự đứng một mình được -
    # không cần model viết lại, và mỗi lần viết lại là một dịp rụng chữ. Đã có ca
    # thật: "nhân sự và trang thiết bị" bị rút còn "nhân sự", và báo cáo xuất ra
    # thiếu hẳn mục thiết bị vì bước khoanh vùng nội dung đọc chính câu này.
    # Phần diễn giải ngữ cảnh ("vẫn kỳ đó") không mất: mỗi workflow đều tự trích
    # tham số kèm `history`.
    if len(steps) == 1 and request.strip():
        steps[0].request = request.strip()
    return steps


# Động từ nói rằng người dùng muốn MỘT VĂN BẢN, không phải một câu trả lời.
_DONG_TU_SOAN = ("soạn", "dự thảo", "lập báo cáo", "viết báo cáo", "ra văn bản",
                 "làm báo cáo", "tổng hợp báo cáo", "lập bảng", "xuất báo cáo")

# Nghiệp vụ KHÔNG đọc file đính kèm: chúng lấy số từ CSDL hoặc từ kho tài liệu.
_BO_QUA_FILE = frozenset({"report", "agent", "qa"})


def _uu_tien_file_dinh_kem(steps: list[PlanStep], has_file: bool) -> list[PlanStep]:
    """Có file đính kèm + đòi một văn bản => soạn TỪ FILE ĐÓ.

    `report`, `agent`, `qa` đều không đọc file đính kèm: chúng lấy số từ CSDL hoặc
    từ kho tri thức. Người dùng gửi kèm một tài liệu rồi bảo "soạn báo cáo về
    trang thiết bị" mà rơi vào một trong ba nhánh đó thì file bị vứt đi - đã xảy
    ra thật: báo cáo trả "Tổng thiết bị: 0" trong khi file họ gửi ghi 79 thiết bị.

    Chỉ đổi khi câu có ĐỘNG TỪ SOẠN. Đính kèm file rồi hỏi "quy định nghỉ phép
    thế nào" vẫn là `qa` - file lúc đó chỉ là thứ còn sót lại của lượt trước, và
    biến câu hỏi đó thành lệnh soạn văn bản mới là sai.

    Nhắc trong prompt không dứt được: model lúc chọn `report` vì câu có chữ "báo
    cáo", lúc chọn `agent` vì nghĩ chỉ cần tra số. Nên chặn ở đây.
    """
    if not has_file:
        return steps
    doi = [s for s in steps
           if s.intent in _BO_QUA_FILE
           and any(v in s.request.lower() for v in _DONG_TU_SOAN)]
    if not doi:
        return steps
    can_doi = {s.id for s in doi}
    logger.info("Đổi %d bước %s -> 'draft': có file đính kèm và yêu cầu soạn văn bản",
                len(doi), [s.intent for s in doi])
    return [
        PlanStep(id=s.id, intent="draft" if s.id in can_doi else s.intent,
                 request=s.request, depends_on=list(s.depends_on), reason=s.reason)
        for s in steps
    ]


def _bo_buoc_lay_so_lieu_thua(steps: list[PlanStep]) -> list[PlanStep]:
    """Bỏ bước `agent` chỉ dựng ra để mớm số liệu cho một bước sinh file.

    Chỉ bỏ khi có bước khác PHỤ THUỘC vào nó: người dùng vừa muốn xem số vừa muốn
    có file thì hai bước đó độc lập, `depends_on` rỗng, và cả hai được giữ.
    """
    dua_vao_agent = {
        dep
        for s in steps if s.intent in TU_TRA_SO_LIEU
        for dep in s.depends_on
    }
    thua = {s.id for s in steps if s.intent == "agent" and s.id in dua_vao_agent}
    if not thua:
        return steps
    logger.info("Bỏ %d bước 'agent' thừa: nghiệp vụ sinh file tự tra số liệu", len(thua))
    return [
        PlanStep(id=s.id, intent=s.intent, request=s.request,
                 depends_on=[d for d in s.depends_on if d not in thua], reason=s.reason)
        for s in steps if s.id not in thua
    ]


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


# Model hay kèm "vui lòng tải file lên" cho nhánh soát tài liệu, kể cả khi file đã
# nằm sẵn trong yêu cầu. Nhắc trong prompt không dứt được.
#
# Giao diện hiện tại chưa in `clarify` ra nên chưa ai thấy, nhưng nó nằm trong
# hợp đồng API (`RoutingInfo.clarify`, `PlanModel.clarify`) - client nào dựng sau
# cũng có quyền hiển thị, và lúc đó người vừa đính kèm xong lại bị đòi file lần
# nữa. Trả về dữ liệu tự mâu thuẫn với chính `has_file` là sai từ gốc, cắt thẳng.
_DOI_FILE = ("tải lên", "tải file", "đính kèm", "upload", "gửi file", "cung cấp file")


def _bo_clarify_doi_file(clarify: str, has_file: bool) -> str:
    if not (has_file and clarify):
        return clarify
    thap = clarify.lower()
    if any(cum in thap for cum in _DOI_FILE):
        logger.info("Bỏ clarify đòi file: yêu cầu đã có file đính kèm")
        return ""
    return clarify


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
            # 600 token là quá chật: model viết `reason` dài vài dòng cho mỗi bước,
            # JSON chưa đóng ngoặc đã hết token, `chat_json` ném lỗi phân tích, và
            # MỌI yêu cầu lặng lẽ rơi về router dự phòng - tức toàn bộ tầng lập kế
            # hoạch ngừng hoạt động mà không ai thấy, chỉ thấy định tuyến kém đi.
            model=get_settings().utility_model, temperature=0.0, max_tokens=1600,
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
    clarify = _bo_clarify_doi_file(str(data.get("clarify") or ""), has_file)
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
