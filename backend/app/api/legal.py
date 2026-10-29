"""API của tầng VBQPPL theo phiên bản: cây điều khoản, lịch sử phiên bản, lệnh sửa đổi
(kể cả hàng chờ cách ly), sự kiện sửa đổi và câu trả lời lỗi thời."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.agents import quyen
from app.core.config import get_settings
from app.db.models import (AnswerEvidence, AnswerLog, LegalAction, LegalChangeEvent, LegalComponent,
                           LegalDocument, LegalVersion)
from app.db.session import session_scope

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/legal", tags=["legal"])
_DOC = [Depends(quyen.can_quyen(quyen.QUYEN_KHO_DOC, "xem văn bản quy phạm"))]
_GHI = [Depends(quyen.can_quyen(quyen.QUYEN_KHO_GHI, "duyệt lệnh sửa đổi văn bản"))]


def _enabled() -> None:
    if not get_settings().legal_versioning_enabled:
        raise HTTPException(status_code=404, detail="Tầng phiên bản VBQPPL đang tắt (LEGAL_VERSIONING_ENABLED)")


def _d(value: date | None) -> str | None:
    return value.isoformat() if value else None


@router.get("/documents", dependencies=_DOC, summary="Các VBQPPL đã có cây phiên bản")
async def documents() -> list[dict[str, Any]]:
    _enabled()
    async with session_scope() as s:
        docs = (await s.execute(select(LegalDocument))).scalars().all()
        return [{"legal_id": d.legal_id, "doc_id": d.doc_id, "so_hieu": d.so_hieu, "loai": d.loai, "ten": d.ten,
                 "source": d.source, "ngay_ban_hanh": _d(d.ngay_ban_hanh), "ngay_hieu_luc": _d(d.ngay_hieu_luc),
                 "sua_doi": json.loads(d.sua_doi or "[]")} for d in docs]


@router.get("/documents/{legal_id}/tree", dependencies=_DOC, summary="Cây Điều/Khoản/Điểm tại một ngày")
async def tree(legal_id: str, as_of: date | None = None) -> dict[str, Any]:
    _enabled()
    day = as_of or date.today()
    async with session_scope() as s:
        if await s.get(LegalDocument, legal_id) is None:
            raise HTTPException(status_code=404, detail="Không có văn bản này")
        comps = (await s.execute(select(LegalComponent).where(LegalComponent.legal_id == legal_id)
                                 .order_by(LegalComponent.thu_tu))).scalars().all()
        versions = (await s.execute(select(LegalVersion).where(
            LegalVersion.component_id.in_([c.component_id for c in comps])))).scalars().all()
    live = {v.component_id: v for v in versions
            if (v.valid_from is None or v.valid_from <= day) and (v.valid_to is None or v.valid_to > day)}
    nodes = [{"component_id": c.component_id, "parent_id": c.parent_id, "unit": c.unit, "so": c.so,
              "path": c.path, "in_force": c.component_id in live,
              "text": (live[c.component_id].text[:400] if c.component_id in live else ""),
              "valid_from": _d(live[c.component_id].valid_from) if c.component_id in live else None}
             for c in comps if c.unit != "Mở đầu"]
    return {"legal_id": legal_id, "as_of": day.isoformat(), "nodes": nodes}


@router.get("/components/{component_id}/history", dependencies=_DOC, summary="Mọi phiên bản của một điều khoản")
async def history(component_id: str) -> dict[str, Any]:
    _enabled()
    async with session_scope() as s:
        versions = (await s.execute(select(LegalVersion).where(LegalVersion.component_id == component_id)
                                    .order_by(LegalVersion.valid_from))).scalars().all()
        if not versions:
            raise HTTPException(status_code=404, detail="Không có điều khoản này")
        actions = {a.action_id: a for a in (await s.execute(select(LegalAction).where(
            LegalAction.action_id.in_([v.created_by_action for v in versions if v.created_by_action])))).scalars()}
    return {"component_id": component_id, "versions": [
        {"version_id": v.version_id, "valid_from": _d(v.valid_from), "valid_to": _d(v.valid_to), "text": v.text,
         "by_action": ({"action_id": v.created_by_action, "instruction": actions[v.created_by_action].instruction,
                        "source": actions[v.created_by_action].source_legal_id}
                       if v.created_by_action in actions else None)} for v in versions]}


def _action(a: LegalAction) -> dict[str, Any]:
    target = json.loads(a.target_address or "{}")
    return {"action_id": a.action_id, "op": a.op, "status": a.status, "reason": a.reason,
            "source_legal_id": a.source_legal_id, "source_component_id": a.source_component_id,
            "instruction": a.instruction, "target_law": a.target_law, "target": target.get("text", ""),
            "target_legal_id": a.target_legal_id, "target_component_id": a.target_component_id,
            "effective_date": _d(a.effective_date)}


@router.get("/actions", dependencies=_DOC, summary="Lệnh sửa đổi (lọc theo trạng thái)")
async def actions(status: str | None = Query(default=None, description="applied|pending_target|quarantined")):
    _enabled()
    async with session_scope() as s:
        q = select(LegalAction)
        if status:
            q = q.where(LegalAction.status == status)
        return [_action(a) for a in (await s.execute(q.order_by(LegalAction.created_at))).scalars().all()]


class ApproveRequest(BaseModel):
    effective_date: date | None = None


@router.post("/actions/{action_id}/approve", dependencies=_GHI,
             summary="Duyệt một lệnh đang cách ly (bổ sung ngày hiệu lực nếu thiếu) và áp dụng")
async def approve(action_id: str, request: ApproveRequest) -> dict[str, Any]:
    _enabled()
    from app.legal import hooks, service, tracking
    from app.rag.ingestion import get_ingestion_pipeline

    async with session_scope() as s:
        action = await s.get(LegalAction, action_id)
        if action is None:
            raise HTTPException(status_code=404, detail="Không có lệnh này")
        if action.status == "applied":
            return _action(action)
        if request.effective_date:
            action.effective_date = request.effective_date
        if action.effective_date is None:
            raise HTTPException(status_code=422, detail="Cần ngày hiệu lực để áp dụng lệnh")
        target = service.match_target(action, await service.find_documents(s))
        if target is None:
            raise HTTPException(status_code=409, detail=f"Chưa có văn bản đích trong kho: {action.target_law}")
        changed = await service.apply_action(s, action, target.legal_id)
        if changed is None:
            return _action(action)
        await tracking.mark_stale(s, [service.Change(c, target.legal_id, action.effective_date, action.action_id)
                                      for c in changed])
        await s.flush()
        snap_doc = await hooks.pack(s, target.legal_id)
        result = _action(action)
    result["chunks"] = await get_ingestion_pipeline().refresh_legal_document(snap_doc)
    return result


@router.get("/changes", dependencies=_DOC, summary="Sự kiện sửa đổi và các câu trả lời bị ảnh hưởng")
async def changes(limit: int = 100) -> list[dict[str, Any]]:
    _enabled()
    async with session_scope() as s:
        rows = (await s.execute(select(LegalChangeEvent).order_by(LegalChangeEvent.created_at.desc())
                                .limit(limit))).scalars().all()
    return [{"event_id": e.event_id, "action_id": e.action_id, "target": e.target_component_id,
             "effective_date": _d(e.effective_date), "affected_answers": json.loads(e.affected_answers),
             "created_at": e.created_at.isoformat() if e.created_at else None} for e in rows]


@router.get("/answers", dependencies=_DOC, summary="Câu trả lời theo trạng thái (stale = nguồn đã bị sửa)")
async def answers(status: str = "stale", limit: int = 100) -> list[dict[str, Any]]:
    _enabled()
    async with session_scope() as s:
        rows = (await s.execute(select(AnswerLog).where(AnswerLog.status == status)
                                .order_by(AnswerLog.created_at.desc()).limit(limit))).scalars().all()
        ev = (await s.execute(select(AnswerEvidence).where(
            AnswerEvidence.answer_id.in_([r.answer_id for r in rows])))).scalars().all()
    by: dict[str, list[str]] = {}
    for e in ev:
        by.setdefault(e.answer_id, []).append(e.component_id)
    return [{"answer_id": r.answer_id, "question": r.question, "answer": r.answer[:600], "status": r.status,
             "stale_reason": r.stale_reason, "stale_from": _d(r.stale_from), "evidence": by.get(r.answer_id, []),
             "superseded_by": r.superseded_by, "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


@router.post("/answers/{answer_id}/recheck", dependencies=_DOC,
             summary="Hỏi lại câu hỏi cũ trên nguồn hiện hành, lưu câu trả lời mới")
async def recheck(answer_id: str) -> dict[str, Any]:
    _enabled()
    from app.agents.graph import build_initial_state, run_qa

    async with session_scope() as s:
        old = await s.get(AnswerLog, answer_id)
        if old is None:
            raise HTTPException(status_code=404, detail="Không có câu trả lời này")
        question, old_answer = old.question, old.answer
    state = await build_initial_state(question=question, load_history=False)
    result = await run_qa(state)
    new_id = result.get("answer_id")
    async with session_scope() as s:
        old = await s.get(AnswerLog, answer_id)
        old.status, old.superseded_by = "rechecked", new_id
    return {"answer_id": answer_id, "question": question, "old_answer": old_answer,
            "new_answer_id": new_id, "new_answer": result.get("answer", ""),
            "citations": result.get("used_citations", [])}
