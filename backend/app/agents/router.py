"""Định tuyến ý định: một yêu cầu tiếng Việt -> một trong năm workflow.

Đây là node đầu tiên của agent, và là chỗ dễ hỏng nhất: đoán sai ý định thì mọi
bước sau đều đúng quy trình nhưng sai việc. Vì vậy có hai lớp:

    LLM         - hiểu được câu chữ tự nhiên, phân biệt "soạn cho DV01" với
                  "tổng hợp toàn cơ quan"
    từ khoá     - đường lùi khi LLM hỏng hoặc trả về ý định không hợp lệ; cũng là
                  lớp kiểm tra chéo khi LLM tự tin thấp

Ràng buộc cứng đặt ở code chứ không gửi gắm vào prompt: không có file đính kèm thì
không bao giờ đi nhánh `document`, vì workflow 2 cần một file có thật để parse.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.agents.history import format_history
from app.agents.prompts import ROUTER_SYSTEM, ROUTER_USER
from app.core.config import get_settings
from app.services.llm import LLMError, get_llm
from app.tools.registry import describe_tools

logger = logging.getLogger(__name__)

Intent = Literal["qa", "document", "draft", "report", "presentation", "agent"]
INTENTS: tuple[Intent, ...] = ("qa", "document", "draft", "report", "presentation", "agent")

# Ý định nào thắng khi điểm từ khoá bằng nhau: cụ thể trước, chung chung sau.
PRIORITY: tuple[Intent, ...] = ("presentation", "report", "draft", "document", "agent", "qa")

# Ngưỡng tin cậy: dưới mức này thì đối chiếu lại với lớp từ khoá.
MIN_CONFIDENCE = 0.5

KEYWORDS: dict[Intent, tuple[str, ...]] = {
    "presentation": ("slide", "trình chiếu", "power ?point", "pptx", "thuyết trình",
                     "bài trình bày", "giao ban"),
    "report": ("tổng hợp", "toàn cơ quan", "toàn đơn vị", "các đơn vị", "tất cả đơn vị",
               "nhiều đơn vị", "thống kê", "đối chiếu"),
    "draft": ("soạn", "dự thảo", "lập báo cáo", "viết báo cáo", "ra văn bản", "làm báo cáo"),
    "document": ("thể thức", "soát", "rà soát", "kiểm tra văn bản", "văn bản này",
                 "file này", "tài liệu này", "công văn này", "phân loại", "giao việc"),
    # Hỏi SỐ LIỆU nghiệp vụ (khác `qa` là hỏi nội dung tài liệu).
    "agent": ("quân số", "trang thiết bị", "trang bị", "đã gửi", "chưa gửi", "đã nộp",
              "chưa nộp", "đơn vị nào", "kiểm kê", "vắng mặt", "bao nhiêu người"),
    "qa": ("quy định", "thế nào", "là gì", "bao nhiêu", "tra cứu", "tìm", "hỏi",
           "cho biết", "ở đâu", "khi nào"),
}


@dataclass(slots=True)
class IntentResult:
    intent: Intent
    confidence: float
    reason: str = ""
    clarify: str = ""
    source: str = "llm"          # llm | keyword | rule

    def as_dict(self) -> dict[str, Any]:
        return {"intent": self.intent, "confidence": round(self.confidence, 2),
                "reason": self.reason, "clarify": self.clarify, "source": self.source}


def score_keywords(request: str, has_file: bool = False) -> dict[Intent, int]:
    text = (request or "").lower()
    scores: dict[Intent, int] = {
        intent: sum(1 for pattern in patterns if re.search(pattern, text))
        for intent, patterns in KEYWORDS.items()
    }
    # File đính kèm là tín hiệu mạnh nhất cho việc "xử lý văn bản này".
    if has_file:
        scores["document"] += 2
    else:
        scores["document"] = 0
    return scores


def classify_by_keywords(request: str, has_file: bool = False) -> IntentResult:
    scores = score_keywords(request, has_file)
    best = max(PRIORITY, key=lambda intent: (scores.get(intent, 0), -PRIORITY.index(intent)))
    hits = scores.get(best, 0)
    if hits == 0:
        return IntentResult("qa", 0.3, "Không có tín hiệu rõ ràng, mặc định tra cứu",
                            source="keyword")
    # Nhiều từ khoá trùng thì tin hơn, nhưng đây vẫn chỉ là đường lùi.
    return IntentResult(best, min(0.4 + 0.15 * hits, 0.8),
                        f"Khớp {hits} từ khoá của nhóm {best}", source="keyword")


async def classify_intent(
    request: str,
    has_file: bool = False,
    history: list[dict[str, str]] | None = None,
) -> IntentResult:
    """Chọn workflow cho một yêu cầu. Không bao giờ ném lỗi - luôn có đường lùi."""
    request = (request or "").strip()
    fallback = classify_by_keywords(request, has_file)
    if not request:
        return IntentResult("qa", 0.0, "Yêu cầu rỗng", clarify="Bạn muốn hỏi hoặc làm gì?",
                            source="rule")

    try:
        data = await get_llm().chat_json(
            [
                {"role": "system", "content": ROUTER_SYSTEM.format(
                    tools=describe_tools(), has_file="có" if has_file else "không")},
                {"role": "user", "content": ROUTER_USER.format(
                    history=format_history(history), request=request)},
            ],
            model=get_settings().utility_model, temperature=0.0, max_tokens=300,
            thinking=False,
        )
    except (LLMError, Exception) as exc:  # noqa: BLE001 - LLM hỏng thì vẫn phải định tuyến
        logger.warning("Định tuyến bằng LLM thất bại, dùng từ khoá: %s", exc)
        return fallback

    intent = str(data.get("intent") or "").lower()
    if intent not in INTENTS:
        logger.warning("Model trả về ý định lạ %r, dùng từ khoá", intent)
        return fallback

    confidence = float(data.get("confidence") or 0.0)
    result = IntentResult(
        intent=intent,  # type: ignore[arg-type]
        confidence=confidence,
        reason=str(data.get("reason") or ""),
        clarify=str(data.get("clarify") or ""),
    )

    # Ràng buộc cứng: workflow 2 cần file có thật để parse.
    if result.intent == "document" and not has_file:
        logger.info("Bỏ ý định 'document' vì không có file đính kèm")
        return fallback

    # Model tự tin thấp mà từ khoá nói khác -> tin từ khoá, nó dựa trên chữ có thật.
    if confidence < MIN_CONFIDENCE and fallback.intent != result.intent and fallback.confidence > 0.3:
        logger.info("Ý định LLM %r (%.2f) thua tín hiệu từ khoá %r",
                    result.intent, confidence, fallback.intent)
        return fallback
    return result
