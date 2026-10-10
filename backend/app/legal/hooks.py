"""Điểm nối của app.legal vào luồng có sẵn (ingest, retrieve, ghi câu trả lời).

Chỉ chạy khi `legal_versioning_enabled`. Mọi lỗi ở đây được ghi log và nuốt: tầng phiên
bản hỏng thì hệ thống quay về hành vi cũ (chunk theo văn bản nạp vào, truy hồi không lọc
ngày), không được làm hỏng việc nạp tài liệu hay trả lời câu hỏi.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from qdrant_client import models
from sqlalchemy import select

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def iso(d: date | None) -> str | None:
    """Ngày -> RFC 3339 cho payload DATETIME của Qdrant."""
    return datetime.combine(d, time.min).isoformat() + "Z" if d else None


def doc_title_of(doc) -> str:
    """"Nghị định 379/2026/NĐ-CP - Quy định tiêu chuẩn ..." - dùng làm nhãn và tiền tố embed.
    Tên file ("Thông tư_k") không chứa số hiệu, nên hỏi "Thông tư 76/2026/TT-BXD" trượt."""
    display = f"{doc.loai} {doc.so_hieu}".strip() if doc.so_hieu else (doc.loai or "")
    ten = " ".join((doc.ten or "").split())
    if ten and ten.isupper():
        ten = ten.capitalize()
    title = " - ".join(p for p in (display, ten[:160]) if p)
    return title or Path(doc.source or "").stem


def validity_label(valid_from: date | None, valid_to: date | None) -> str:
    if valid_from and valid_to:
        return f"hiệu lực {valid_from:%d/%m/%Y} - {valid_to:%d/%m/%Y}"
    if valid_from:
        return f"hiệu lực từ {valid_from:%d/%m/%Y}"
    if valid_to:
        return f"hiệu lực tới {valid_to:%d/%m/%Y}"
    return ""


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class DocSnapshots:
    legal_id: str
    doc_id: str
    source: str
    doc_title: str
    ngay_hieu_luc: date | None
    snapshots: list[Any]                    # service.Snapshot
    amended: bool


@dataclass(slots=True)
class IngestOutcome:
    this: DocSnapshots
    others: list[DocSnapshots] = field(default_factory=list)   # văn bản đích vừa bị sửa
    actions: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)


async def pack(session, legal_id: str) -> DocSnapshots:
    """Các bản dựng lại theo khoảng hiệu lực của một văn bản, kèm thông tin để ghi chunk."""
    from app.db.models import LegalAction, LegalDocument
    from app.legal import service

    doc = await session.get(LegalDocument, legal_id)
    snaps = await service.snapshots(session, legal_id)
    amended = bool((await session.execute(select(LegalAction.action_id).where(
        LegalAction.target_legal_id == legal_id, LegalAction.status == "applied"))).first())
    return DocSnapshots(legal_id, doc.doc_id, doc.source, doc_title_of(doc), doc.ngay_hieu_luc,
                        snaps, amended)


async def on_ingest(text: str, doc_id: str, source: str) -> IngestOutcome | None:
    if not get_settings().legal_versioning_enabled:
        return None
    from app.db.session import session_scope
    from app.legal import service, tracking
    from app.legal.identity import identify

    try:
        async with session_scope() as session:
            ident = identify(text, doc_id, source)
            res = await service.index_document(session, ident, text, doc_id, source)
            if res is None:
                return None
            events = await tracking.mark_stale(session, res.changes) if res.changes else []
            await session.flush()

            outcome = IngestOutcome(this=await pack(session, res.legal_id), actions=res.actions, events=events)
            for other in sorted(res.touched - {res.legal_id}):
                outcome.others.append(await pack(session, other))
            logger.info("VBQPPL %s: %d đơn vị, %d lệnh sửa đổi, %d văn bản đích đổi, %d sự kiện lỗi thời",
                        res.legal_id, res.components, len(res.actions), len(outcome.others), len(events))
            return outcome
    except Exception:  # noqa: BLE001
        logger.exception("Tầng phiên bản VBQPPL lỗi khi nạp %s - bỏ qua, nạp như văn bản thường", source)
        return None


def chunk_payload(snap_doc: DocSnapshots, snap, units: list[dict]) -> dict[str, Any]:
    """Trường pháp lý gắn vào payload của một chunk."""
    legal_id = snap_doc.legal_id
    dieus = [str(u.get("dieu")) for u in units if u.get("dieu")]
    comps = [f"{legal_id}:dieu-{d.lower()}" for d in dieus]
    return {
        "legal_id": legal_id,
        "dieus": dieus,
        "component_ids": comps,
        "version_ids": {c: snap.versions.get(c, "") for c in comps},
        "valid_from": iso(snap.valid_from),
        "valid_to": iso(snap.valid_to),
        "latest": snap.valid_to is None,
        # Văn bản đã bị sửa: nhãn là khoảng hiệu lực của bản dựng lại. Văn bản khác: ngày hiệu
        # lực của chính nó - nhất là văn bản SỬA ĐỔI chưa tới ngày hiệu lực, để model không
        # trình bày chữ mới trong đó như quy định đang áp dụng.
        "validity_label": (validity_label(snap.valid_from, snap.valid_to) if snap_doc.amended
                           else validity_label(snap_doc.ngay_hieu_luc, None)),
        "ngay_hieu_luc": iso(snap_doc.ngay_hieu_luc),
    }


# --------------------------------------------------------------------------- #
# Truy vấn
# --------------------------------------------------------------------------- #
@dataclass(slots=True)
class QueryPlan:
    as_of: date
    query_filter: models.Filter | None
    address_chunks: list[Any] = field(default_factory=list)
    trace: dict[str, Any] = field(default_factory=dict)


def validity_filter(as_of: date) -> models.Filter:
    """Chunk còn hiệu lực tại as_of. Tài liệu không phải VBQPPL không có hai trường này
    (IsEmpty) nên luôn qua."""
    stamp = datetime.combine(as_of, time.min)
    return models.Filter(must=[
        models.Filter(should=[models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_from")),
                              models.FieldCondition(key="valid_from", range=models.DatetimeRange(lte=stamp))]),
        models.Filter(should=[models.IsEmptyCondition(is_empty=models.PayloadField(key="valid_to")),
                              models.FieldCondition(key="valid_to", range=models.DatetimeRange(gt=stamp))]),
    ])


def combine(*filters: models.Filter | None) -> models.Filter | None:
    parts = [f for f in filters if f is not None]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else models.Filter(must=parts)


async def _ly_do_khong_hieu_luc(session, doc, path: str, as_of: date) -> str:
    """Chữ thay cho đơn vị không còn hiệu lực tại ngày hỏi. Văn bản bị thay thế/bãi bỏ CẢ văn bản thì nói rõ
    từ ngày nào, bởi văn bản nào - "đã bị bãi bỏ hoặc chưa có hiệu lực" trơn làm model chỉ đáp được "không
    tìm thấy" (NĐ 72/2023 hỏi tại 20/11/2026, đã bị NĐ 379/2026 thay thế từ 16/11/2026)."""
    from app.db.models import LegalAction, LegalDocument

    acts = (await session.execute(select(LegalAction).where(
        LegalAction.target_legal_id == doc.legal_id, LegalAction.op == "het_hieu_luc",
        LegalAction.status == "applied"))).scalars().all()
    act = min((a for a in acts if a.effective_date and a.effective_date <= as_of),
              key=lambda a: a.effective_date, default=None)
    if act is not None:
        src = await session.get(LegalDocument, act.source_legal_id)
        by = f"{src.loai} {src.so_hieu}".strip() if src is not None else act.source_legal_id
        return (f"{doc_title_of(doc)} đã HẾT HIỆU LỰC từ ngày {act.effective_date:%d/%m/%Y}, bị bãi bỏ/thay thế bởi "
                f"{by} (\"{act.instruction[:300]}\"). Vì vậy {path} không còn áp dụng tại ngày {as_of:%d/%m/%Y}; "
                f"quy định đang áp dụng nằm trong {by}.")
    if doc.ngay_hieu_luc and doc.ngay_hieu_luc > as_of:
        return f"{path} chưa có hiệu lực tại ngày {as_of:%d/%m/%Y} (văn bản có hiệu lực từ {doc.ngay_hieu_luc:%d/%m/%Y})."
    return f"{path} đã bị bãi bỏ hoặc chưa có hiệu lực tại ngày {as_of:%d/%m/%Y}."


async def prepare_query(question: str, base_filter: models.Filter | None) -> QueryPlan | None:
    if not get_settings().legal_versioning_enabled:
        return None
    from app.db.models import LegalAction
    from app.db.session import session_scope
    from app.legal import service
    from app.legal.query import match_document, parse_question
    from app.rag.retrieval import RetrievedChunk

    try:
        intent = parse_question(question)
        as_of = intent.as_of or date.today()
        trace: dict[str, Any] = {"address": intent.address, "address_is_reference": intent.address_is_reference,
                                 "so_hieu": intent.so_hieu or intent.so,
                                 "as_of": as_of.isoformat()}
        address_chunks: list[Any] = []
        extra: models.Filter | None = None
        async with session_scope() as session:
            docs = await service.find_documents(session)
            matched = match_document(intent, docs) if intent.has_doc else []
            trace["matched_docs"] = [d.legal_id for d in matched]
            # "trước/sau khi sửa đổi" mà không nêu ngày: lấy ngày hiệu lực của lệnh sửa đổi.
            if (intent.before_amendment or intent.after_amendment) and not intent.as_of and matched:
                effs = sorted({a.effective_date for a in (await session.execute(select(LegalAction).where(
                    LegalAction.target_legal_id.in_([d.legal_id for d in matched]),
                    LegalAction.status == "applied"))).scalars().all() if a.effective_date})
                if effs:
                    as_of = (effs[0] - timedelta(days=1)) if intent.before_amendment else effs[-1]
                    trace["as_of"] = as_of.isoformat()
                    trace["as_of_reason"] = "trước sửa đổi" if intent.before_amendment else "sau sửa đổi"
            if intent.has_address and matched:
                for doc in matched[:2]:
                    hit = await service.lookup(session, doc.legal_id, intent.address, as_of)
                    if not hit:
                        continue
                    title = doc_title_of(doc)
                    label = validity_label(hit.get("valid_from"), hit.get("valid_to"))
                    text = hit["text"] or await _ly_do_khong_hieu_luc(session, doc, hit["path"], as_of)
                    payload = {"doc_id": doc.doc_id, "doc_title": title, "source": doc.source,
                               "section": hit["path"], "text": text, "legal_id": doc.legal_id,
                               "component_ids": [hit["component_id"]], "version_ids": hit["versions"],
                               "valid_from": iso(hit.get("valid_from")), "valid_to": iso(hit.get("valid_to")),
                               "validity_label": label}
                    address_chunks.append(RetrievedChunk(
                        point_id=f"addr:{hit['component_id']}@{as_of.isoformat()}", text=text,
                        rerank_score=1.0, rrf_score=0.0, payload=payload, matched_by="address"))
            elif intent.has_address and not intent.address_is_reference:
                # Có Điều nhưng không rõ văn bản: chỉ tìm trong các chunk chứa đúng Điều đó.
                # Không áp khi Điều chỉ là viện dẫn ("... quy định tại Điều 135 thì ..."): lọc theo nó
                # loại mất đúng Điều đang được hỏi (GĐ2 MinIO: 4 câu ngữ nghĩa trượt vì thế).
                extra = models.Filter(must=[models.FieldCondition(
                    key="dieus", match=models.MatchAny(any=[intent.address["dieu"]]))])
        trace["address_hits"] = len(address_chunks)
        return QueryPlan(as_of=as_of, query_filter=combine(base_filter, validity_filter(as_of), extra),
                         address_chunks=address_chunks, trace=trace)
    except Exception:  # noqa: BLE001
        logger.exception("Không dựng được kế hoạch truy vấn pháp lý - dùng truy hồi thường")
        return None


# --------------------------------------------------------------------------- #
# Ghi câu trả lời
# --------------------------------------------------------------------------- #
async def record_answer(state: dict[str, Any], answer: str, citations: list[dict[str, Any]]) -> str | None:
    cfg = get_settings()
    if not cfg.legal_versioning_enabled or not answer or not citations:
        return None
    from app.db.session import session_scope
    from app.legal import tracking

    try:
        as_of_raw = (state.get("trace") or {}).get("legal", {}).get("as_of")
        as_of = date.fromisoformat(as_of_raw) if as_of_raw else None
        async with session_scope() as session:
            return await tracking.record_answer(
                session, question=state.get("question", ""), answer=answer, citations=citations,
                conversation_id=state.get("conversation_id", ""), as_of=as_of, model=cfg.llm_model)
    except Exception:  # noqa: BLE001
        logger.exception("Không ghi được nguồn gốc câu trả lời")
        return None
