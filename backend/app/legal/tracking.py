"""Nguồn gốc câu trả lời và đánh dấu câu trả lời lỗi thời khi nguồn bị sửa đổi.

Mỗi câu trả lời có trích dẫn VBQPPL được ghi kèm các component (Điều/Khoản/Điểm) và
phiên bản đã dùng. Khi một lệnh sửa đổi đóng phiên bản của component X, mọi câu trả lời có
bằng chứng là X, tổ tiên của X (chunk ghi bằng chứng ở cấp Điều) hoặc con cháu của X (tra
thẳng một Khoản thuộc Điều bị thay cả), hoặc là đơn vị viện dẫn tới X, bị đánh `stale` -
tra ngược theo tập component, không xoá cache theo thời gian (provenance invalidation).

Ancestor/descendant suy từ chính component_id ("...:dieu-4:khoan-15") nên không phải đi cây.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from typing import Any, Iterable

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AnswerEvidence, AnswerLog, LegalChangeEvent, LegalCitation

logger = logging.getLogger(__name__)


def ancestors(cid: str) -> list[str]:
    """"doc:dieu-4:khoan-15:diem-a" -> ["doc:dieu-4:khoan-15", "doc:dieu-4"]."""
    out = []
    parts = cid.split(":")
    while len(parts) > 3 and parts[-1].split("-", 1)[0] in ("khoan", "diem"):
        parts = parts[:-1]
        out.append(":".join(parts))
    return out


def evidence_from_citations(citations: Iterable[dict[str, Any]]) -> list[tuple[str, str]]:
    """(component_id, version_id) từ các trích dẫn đã dùng trong câu trả lời."""
    out: list[tuple[str, str]] = []
    for c in citations:
        comps = c.get("component_ids") or []
        vers = c.get("version_ids") or {}
        for cid in comps:
            pair = (cid, vers.get(cid, "") if isinstance(vers, dict) else "")
            if pair not in out:
                out.append(pair)
    return out


async def record_answer(session: AsyncSession, *, question: str, answer: str,
                        citations: Iterable[dict[str, Any]], conversation_id: str = "",
                        as_of: date | None = None, model: str = "") -> str | None:
    """Ghi câu trả lời + bằng chứng; None khi câu trả lời không dựa trên VBQPPL nào."""
    evidence = evidence_from_citations(citations)
    if not evidence:
        return None
    answer_id = uuid.uuid4().hex
    session.add(AnswerLog(answer_id=answer_id, conversation_id=conversation_id or "", question=question,
                          answer=answer, as_of=as_of, model=model[:200]))
    for cid, vid in evidence:
        session.add(AnswerEvidence(answer_id=answer_id, component_id=cid, version_id=vid))
    return answer_id


async def mark_stale(session: AsyncSession, changes: list[Any]) -> list[dict[str, Any]]:
    """Đánh dấu câu trả lời bị ảnh hưởng bởi các thay đổi (service.Change). Trả về sự kiện."""
    by_action: dict[str, list[Any]] = {}
    for ch in changes:
        by_action.setdefault(ch.action_id, []).append(ch)
    events = []
    for action_id, items in by_action.items():
        changed = {c.component_id for c in items}
        related = set(changed)
        for cid in changed:
            related.update(ancestors(cid))
        # Đơn vị viện dẫn tới phần bị sửa cũng có thể đổi nghĩa.
        citers = (await session.execute(select(LegalCitation.from_component_id).where(
            LegalCitation.to_component_id.in_(list(changed))))).scalars().all()
        related.update(citers)
        conds = [AnswerEvidence.component_id.in_(list(related))]
        conds += [AnswerEvidence.component_id.like(f"{cid}:%") for cid in changed
                  if cid.rsplit(":", 1)[-1].startswith(("dieu-", "khoan-"))]
        answer_ids = sorted(set((await session.execute(
            select(AnswerEvidence.answer_id).where(or_(*conds)))).scalars().all()))
        eff = items[0].effective_date
        target = min(changed, key=len)
        if answer_ids:
            reason = (f"Nguồn trích dẫn {target} đã bị sửa đổi/bãi bỏ"
                      + (f", có hiệu lực từ {eff:%d/%m/%Y}" if eff else ""))
            await session.execute(update(AnswerLog).where(
                AnswerLog.answer_id.in_(answer_ids), AnswerLog.status == "fresh").values(
                status="stale", stale_reason=reason, stale_from=eff))
        event = LegalChangeEvent(event_id=uuid.uuid4().hex, action_id=action_id, target_component_id=target,
                                 effective_date=eff, affected_answers=json.dumps(answer_ids))
        session.add(event)
        events.append({"action_id": action_id, "target": target, "effective_date": eff,
                       "affected_answers": answer_ids})
        if answer_ids:
            logger.info("Sửa đổi %s: %d câu trả lời bị đánh dấu lỗi thời", target, len(answer_ids))
    return events
