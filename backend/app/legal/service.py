"""Cây phiên bản trong CSDL: nạp văn bản, áp lệnh sửa đổi, dựng ảnh chụp theo ngày, tra địa chỉ.

Quy ước thời gian: phiên bản có hiệu lực tại ngày d khi (valid_from IS NULL OR valid_from <= d)
AND (valid_to IS NULL OR valid_to > d). Văn bản gốc nạp vào có valid_from = NULL: không giấu
một văn bản chỉ vì chưa tới ngày hiệu lực của nó (nhãn trích dẫn vẫn ghi ngày đó); chỉ lệnh
sửa đổi mới tạo ranh giới thời gian. Lệnh không có ngày hiệu lực bị cách ly, không đoán ngày.

Thứ tự áp lệnh: theo ngày hiệu lực. Nạp lại một văn bản gốc = dựng lại cây của nó rồi áp lại
mọi lệnh đã biết nhắm vào nó, nên thứ tự nạp (sửa đổi trước, gốc sau) không làm sai kết quả.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LegalAction, LegalCitation, LegalComponent, LegalDocument, LegalVersion
from app.legal.amendments import ActionSpec, extract_actions, extract_doc_actions
from app.legal.identity import DocIdentity, law_key, normalize_so_hieu
from app.legal.structure import (Node, build_tree, children_map, parse_fragment, render, scan,
                                 subtree_text)

logger = logging.getLogger(__name__)

_NS = uuid.UUID("2b1f6c4e-8a57-4f0e-9a51-6c1d7e2f9b10")


def _vid(*parts: object) -> str:
    return uuid.uuid5(_NS, "|".join(str(p) for p in parts)).hex


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class Change:
    component_id: str
    legal_id: str
    effective_date: date | None
    action_id: str


@dataclass(slots=True)
class IndexResult:
    legal_id: str
    identity: DocIdentity
    components: int
    actions: list[dict] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)
    # văn bản (legal_id) có cây vừa đổi -> phải dựng lại chunk của chúng
    touched: set[str] = field(default_factory=set)


def component_id_for(legal_id: str, address: dict[str, str]) -> str:
    cid = f"{legal_id}:dieu-{address['dieu'].lower()}"
    if address.get("khoan"):
        cid += f":khoan-{address['khoan'].lower()}"
    if address.get("diem"):
        cid += f":diem-{address['diem'].lower()}"
    return cid


# --------------------------------------------------------------------------- #
# Nạp văn bản
# --------------------------------------------------------------------------- #
async def index_document(session: AsyncSession, ident: DocIdentity, text: str, doc_id: str,
                         source: str) -> IndexResult | None:
    nodes = build_tree(text, ident.legal_id)
    if not any(n.unit == "Điều" for n in nodes):
        return None
    legal_id = ident.legal_id

    # Dựng lại cây (nạp lại = làm lại từ đầu); lệnh sửa đổi đã biết được áp lại ở cuối.
    old = (await session.execute(select(LegalComponent.component_id)
                                 .where(LegalComponent.legal_id == legal_id))).scalars().all()
    if old:
        await session.execute(delete(LegalVersion).where(LegalVersion.component_id.in_(old)))
        await session.execute(delete(LegalCitation).where(LegalCitation.from_component_id.in_(old)))
        await session.execute(delete(LegalComponent).where(LegalComponent.legal_id == legal_id))

    doc = await session.get(LegalDocument, legal_id)
    if doc is None:
        doc = LegalDocument(legal_id=legal_id)
        session.add(doc)
    doc.doc_id, doc.so_hieu, doc.loai, doc.ten = doc_id, ident.so_hieu, ident.loai, ident.ten[:1000]
    doc.source, doc.ngay_ban_hanh, doc.ngay_hieu_luc = source, ident.ngay_ban_hanh, ident.ngay_hieu_luc
    doc.sua_doi = json.dumps(ident.sua_doi, ensure_ascii=False)

    for n in nodes:
        session.add(LegalComponent(component_id=n.component_id, legal_id=legal_id, parent_id=n.parent_id,
                                   unit=n.unit, so=n.so, thu_tu=n.thu_tu, path=n.path))
        body = n.text
        session.add(LegalVersion(version_id=_vid(n.component_id, "base", _sha(body)),
                                 component_id=n.component_id, text=body, sha256=_sha(body)))
    _add_citations(session, legal_id, nodes)
    await session.flush()

    result = IndexResult(legal_id=legal_id, identity=ident, components=len(nodes))

    # 1) Văn bản này là văn bản sửa đổi: ghi lệnh, áp lệnh nào đã có văn bản đích.
    sc = scan(text)
    specs = extract_actions(sc) if sc is not None else []
    doc_specs = extract_doc_actions(sc) if sc is not None else []
    if doc_specs:
        # Câu "Bãi bỏ Thông tư số ..." không có Điều/Khoản nên extract_actions ghi nó thành lệnh
        # "không đọc được địa chỉ" - thay bằng lệnh cấp văn bản.
        doc_lines = {d.source_line for d in doc_specs}
        specs = [x for x in specs if not (x.source_line in doc_lines and not x.address)] + doc_specs
    if specs:
        line_owner = _line_owner(nodes, sc)
        for k, spec in enumerate(specs):
            action = await _upsert_action(session, legal_id, spec, k, line_owner, ident.ngay_hieu_luc)
            result.actions.append({"action_id": action.action_id, "op": action.op,
                                   "target": f"{spec.address_text} {spec.target_law}".strip()})

    await session.flush()
    await _suggest_implicit_replacement(session, doc)

    # 2) Áp mọi lệnh đang chờ hoặc đã áp trước đây mà đích là văn bản này / văn bản khác.
    await session.flush()
    await _apply_pending(session, result)
    return result


async def delete_document(session: AsyncSession, doc_id: str) -> list[str]:
    """Gỡ một tài liệu khỏi cây phiên bản. Trả về legal_id các văn bản GỐC phải dựng lại chunk.

    Không chỉ xoá cây của chính nó: tra theo địa chỉ đọc thẳng CSDL, nên còn sót cây là còn trả lời
    bằng văn bản đã xoá. Nếu nó là văn bản sửa đổi, các sửa đổi nó đã áp lên văn bản gốc được hoàn
    tác (xoá phiên bản do lệnh của nó sinh ra, mở lại phiên bản bị lệnh của nó đóng). Lệnh của văn
    bản KHÁC nhắm vào nó quay về chờ đích.
    """
    docs = (await session.execute(select(LegalDocument).where(LegalDocument.doc_id == doc_id))).scalars().all()
    rebuild: set[str] = set()
    for doc in docs:
        legal_id = doc.legal_id
        own = (await session.execute(select(LegalAction).where(
            LegalAction.source_legal_id == legal_id))).scalars().all()
        applied = [a for a in own if a.status == "applied" and a.target_legal_id and a.target_legal_id != legal_id]
        for target in {a.target_legal_id for a in applied}:
            await _undo_actions(session, target, [a for a in applied if a.target_legal_id == target])
            rebuild.add(target)
        for a in own:
            await session.delete(a)

        for a in (await session.execute(select(LegalAction).where(
                LegalAction.target_legal_id == legal_id, LegalAction.source_legal_id != legal_id))).scalars().all():
            a.status, a.reason = "pending_target", "Văn bản đích đã bị xoá khỏi kho"
            a.target_legal_id = a.target_component_id = None
            a.applied_at = None

        comps = (await session.execute(select(LegalComponent.component_id)
                                       .where(LegalComponent.legal_id == legal_id))).scalars().all()
        if comps:
            await session.execute(delete(LegalVersion).where(LegalVersion.component_id.in_(comps)))
            await session.execute(delete(LegalCitation).where(LegalCitation.from_component_id.in_(comps)))
            await session.execute(delete(LegalComponent).where(LegalComponent.legal_id == legal_id))
        await session.delete(doc)
        rebuild.discard(legal_id)
        logger.info("Gỡ VBQPPL %s (doc_id %s): %d lệnh của nó, dựng lại %s", legal_id, doc_id, len(own),
                    sorted(rebuild) or "-")
    await session.flush()
    return sorted(rebuild)


async def _undo_actions(session: AsyncSession, target_legal_id: str, actions: list[LegalAction]) -> None:
    ids = {a.action_id for a in actions}
    closed_at = {a.effective_date for a in actions if a.effective_date}
    comps, versions = await _load(session, target_legal_id)
    await session.execute(delete(LegalVersion).where(LegalVersion.created_by_action.in_(ids)))
    remaining = [v for v in versions if v.created_by_action not in ids]
    starts = {(v.component_id, v.valid_from) for v in remaining if v.valid_from}
    for v in remaining:
        # Mở lại phiên bản bị chính các lệnh này đóng (không có phiên bản nào khác nối tiếp tại mốc đó).
        if v.valid_to in closed_at and (v.component_id, v.valid_to) not in starts:
            v.valid_to = None
    alive = {v.component_id for v in remaining}
    for cid, comp in comps.items():
        if cid not in alive:            # đơn vị do lệnh bổ sung tạo ra
            await session.delete(comp)


def _line_owner(nodes: list[Node], sc) -> dict[int, str]:
    """Dòng (theo chỉ số trong Markdown) -> component chứa nó, để biết lệnh nằm ở đâu."""
    owner: dict[int, str] = {}
    by_line = sorted((n for n in nodes if n.line >= 0), key=lambda n: n.line)
    j = 0
    current = nodes[0].component_id
    for i in range(len(sc.lines)):
        while j < len(by_line) and by_line[j].line <= i:
            current = by_line[j].component_id
            j += 1
        owner[i] = current
    return owner


async def _upsert_action(session: AsyncSession, legal_id: str, spec: ActionSpec, k: int,
                         line_owner: dict[int, str], effective: date | None) -> LegalAction:
    action_id = _vid(legal_id, spec.source_line, spec.op, spec.address_text, k)
    action = await session.get(LegalAction, action_id)
    if action is not None and action.status == "applied":
        return action
    if action is None:
        action = LegalAction(action_id=action_id, source_legal_id=legal_id, op=spec.op, status="new")
        session.add(action)
    action.source_component_id = line_owner.get(spec.source_line, "")
    action.instruction = spec.instruction[:4000]
    action.target_law = spec.target_law[:500]
    action.target_address = json.dumps({"address": spec.address, "anchor": spec.anchor,
                                        "text": spec.address_text}, ensure_ascii=False)
    action.new_text = json.dumps(spec.payload, ensure_ascii=False)
    action.effective_date = effective
    if spec.reason:
        action.status, action.reason = "quarantined", spec.reason
    elif effective is None:
        action.status = "quarantined"
        action.reason = "Chưa xác định được ngày hiệu lực của văn bản sửa đổi - không đoán ngày"
    else:
        action.status, action.reason = "pending_target", ""
    return action


def _ky_hieu(so_hieu: str) -> str:
    """Phần ký hiệu cơ quan của số hiệu: "146/2026/TT-BTC" -> "TT-BTC"."""
    return so_hieu.rsplit("/", 1)[-1] if so_hieu.count("/") >= 2 else ""


def _moi_hon(a: LegalDocument, b: LegalDocument) -> bool | None:
    """a ban hành sau b? None nếu không so được (thiếu ngày và năm trong số hiệu)."""
    if a.ngay_ban_hanh and b.ngay_ban_hanh and a.ngay_ban_hanh != b.ngay_ban_hanh:
        return a.ngay_ban_hanh > b.ngay_ban_hanh
    ya, yb = (re.search(r"/(\d{4})/", x.so_hieu or "") for x in (a, b))
    if ya and yb and ya.group(1) != yb.group(1):
        return ya.group(1) > yb.group(1)
    return None


async def _suggest_implicit_replacement(session: AsyncSession, doc: LegalDocument) -> None:
    """Hai văn bản cùng loại, cùng tên, cùng cơ quan ban hành nhưng khác số hiệu: văn bản mới gần như
    chắc chắn thay văn bản cũ, nhưng nếu văn bản mới không ghi "bãi bỏ/thay thế" thì đó chỉ là suy đoán -
    tạo lệnh `het_hieu_luc` ở trạng thái CÁCH LY để người duyệt (POST /api/legal/actions/{id}/approve),
    không tự ẩn văn bản cũ. Văn bản cũ vẫn được trả lời, kèm số hiệu/ngày để LLM và người đọc phân biệt."""
    key = law_key(doc.ten)
    if len(key) < 12 or not doc.so_hieu:
        return
    others = (await session.execute(select(LegalDocument).where(
        LegalDocument.loai == doc.loai, LegalDocument.legal_id != doc.legal_id))).scalars().all()
    for other in others:
        if not other.so_hieu or other.so_hieu == doc.so_hieu or law_key(other.ten) != key                 or _ky_hieu(other.so_hieu) != _ky_hieu(doc.so_hieu):
            continue
        newer = _moi_hon(doc, other)
        if newer is None:
            continue
        new, old = (doc, other) if newer else (other, doc)
        explicit = (await session.execute(select(LegalAction).where(
            LegalAction.op == "het_hieu_luc", LegalAction.target_law == old.so_hieu))).scalars().first()
        if explicit is not None:
            continue
        action_id = _vid(new.legal_id, "implicit", old.so_hieu)
        if await session.get(LegalAction, action_id) is not None:
            continue
        session.add(LegalAction(
            action_id=action_id, source_legal_id=new.legal_id, op="het_hieu_luc", target_law=old.so_hieu,
            target_address=json.dumps({"address": {}, "anchor": None, "text": "toàn văn"}, ensure_ascii=False),
            instruction=f"(suy đoán) {new.loai} {new.so_hieu} cùng tên với {old.so_hieu} và ban hành sau",
            effective_date=new.ngay_hieu_luc, status="quarantined",
            reason=(f"{new.so_hieu} cùng loại, cùng tên, cùng cơ quan với {old.so_hieu} nhưng không ghi rõ "
                    "bãi bỏ/thay thế - cần người duyệt trước khi coi văn bản cũ hết hiệu lực")))
        logger.info("Đề xuất (cách ly): %s có thể thay thế %s", new.so_hieu, old.so_hieu)


def match_target(action: LegalAction, docs: list[LegalDocument]) -> LegalDocument | None:
    """Văn bản đích của một lệnh: lệnh cấp văn bản khớp theo số hiệu, lệnh khác theo tên văn bản."""
    if action.op == "het_hieu_luc":
        so_hieu = normalize_so_hieu(action.target_law)
        return next((d for d in docs if d.so_hieu and normalize_so_hieu(d.so_hieu) == so_hieu), None)
    by_key: dict[str, list[LegalDocument]] = {}
    for d in docs:
        for key in {law_key(f"{d.loai} {d.ten}"), law_key(d.ten)}:
            if key:
                by_key.setdefault(key, []).append(d)
    return _match_target(by_key, action.target_law)


async def _apply_pending(session: AsyncSession, result: IndexResult) -> None:
    docs = (await session.execute(select(LegalDocument))).scalars().all()

    # Lệnh chờ đích + lệnh đã áp nhắm vào văn bản vừa dựng lại cây.
    rows = (await session.execute(select(LegalAction).where(
        LegalAction.status.in_(("pending_target", "applied"))))).scalars().all()
    todo = [a for a in rows
            if a.status == "pending_target" or a.target_legal_id == result.legal_id]
    todo.sort(key=lambda a: (a.effective_date or date.max, a.created_at or datetime.min, a.action_id))
    for action in todo:
        target = match_target(action, docs)
        if target is None:
            action.status, action.reason = "pending_target", f"Chưa có văn bản đích trong kho: {action.target_law}"
            continue
        if target.legal_id == action.source_legal_id:
            action.status, action.reason = "quarantined", "Văn bản đích trùng văn bản sửa đổi"
            continue
        changed = await apply_action(session, action, target.legal_id)
        if changed is None:
            continue
        result.touched.add(target.legal_id)
        result.changes.extend(Change(cid, target.legal_id, action.effective_date, action.action_id)
                              for cid in changed)


def _match_target(by_key: dict[str, list[LegalDocument]], target_law: str) -> LegalDocument | None:
    key = law_key(target_law)
    if not key:
        return None
    if key in by_key:
        return by_key[key][0]
    # "Luật Ngân hàng Nhà nước Việt Nam số 46/2010/QH12 đã được..." - khớp tiền tố
    for k, docs in by_key.items():
        if k and (key.startswith(k) or k.startswith(key)) and min(len(k), len(key)) >= 12:
            return docs[0]
    return None


# --------------------------------------------------------------------------- #
# Áp một lệnh
# --------------------------------------------------------------------------- #
async def _load(session: AsyncSession, legal_id: str):
    comps = {c.component_id: c for c in (await session.execute(
        select(LegalComponent).where(LegalComponent.legal_id == legal_id))).scalars().all()}
    versions = (await session.execute(select(LegalVersion).where(
        LegalVersion.component_id.in_(list(comps))))).scalars().all() if comps else []
    return comps, versions


def _subtree(comps: dict[str, LegalComponent], root: str) -> list[str]:
    return [cid for cid in comps if cid == root or cid.startswith(root + ":")]


async def apply_action(session: AsyncSession, action: LegalAction, target_legal_id: str) -> list[str] | None:
    """Áp lệnh lên cây văn bản đích. Trả về các component đã đổi; None nếu phải cách ly."""
    info = json.loads(action.target_address or "{}")
    address, anchor = info.get("address") or {}, info.get("anchor")
    payload = json.loads(action.new_text or "[]")
    eff = action.effective_date
    action.target_legal_id = target_legal_id

    def quarantine(reason: str) -> None:
        action.status, action.reason = "quarantined", reason
        logger.warning("Cách ly lệnh %s (%s): %s", action.action_id[:8], action.instruction[:80], reason)

    if action.op == "het_hieu_luc":
        # Cả văn bản hết hiệu lực từ ngày hiệu lực của văn bản bãi bỏ/thay thế: đóng mọi phiên bản đang
        # mở. Không xoá gì - tra theo ngày trước đó vẫn ra văn bản cũ.
        if eff is None:
            quarantine("Chưa xác định được ngày hiệu lực của văn bản thay thế - không đoán ngày")
            return None
        comps, versions = await _load(session, target_legal_id)
        changed = []
        for v in versions:
            if v.valid_to is None and (v.valid_from is None or v.valid_from < eff):
                v.valid_to = eff
                changed.append(v.component_id)
        action.target_legal_id = target_legal_id
        action.status, action.reason, action.applied_at = "applied", "", datetime.now()
        logger.info("Áp lệnh het_hieu_luc %s (hiệu lực %s): đóng %d đơn vị", target_legal_id, eff, len(changed))
        return sorted(set(changed))
    if not address.get("dieu"):
        quarantine("Địa chỉ đích thiếu số Điều")
        return None
    comps, versions = await _load(session, target_legal_id)
    current = {v.component_id: v for v in versions if v.valid_to is None}
    target = component_id_for(target_legal_id, address)
    action.target_component_id = target
    unit = "Điểm" if address.get("diem") else ("Khoản" if address.get("khoan") else "Điều")

    def close(cids: list[str]) -> list[str]:
        closed = []
        for cid in cids:
            v = current.get(cid)
            if v is not None and (v.valid_from is None or v.valid_from < eff):
                v.valid_to = eff
                closed.append(cid)
        return closed

    if action.op == "bai_bo":
        if target not in comps:
            quarantine(f"Không tìm thấy {info.get('text')} trong văn bản đích")
            return None
        changed = close(_subtree(comps, target))
    elif action.op in ("sua_doi", "bo_sung"):
        if not payload:
            quarantine("Thiếu nội dung mới")
            return None
        frag = parse_fragment(payload[0], unit)
        if frag is None:
            quarantine("Không đọc được nội dung mới")
            return None
        if action.op == "sua_doi" and target not in comps:
            quarantine(f"Không tìm thấy {info.get('text')} trong văn bản đích để sửa đổi")
            return None
        if target in comps:
            changed = close(_subtree(comps, target))
            thu_tu = comps[target].thu_tu
            parent_id = comps[target].parent_id
        else:
            parent_id, thu_tu = _insert_position(comps, target, unit, anchor, target_legal_id)
            if parent_id is False:
                quarantine("Không tìm thấy đơn vị cha/mốc chèn trong văn bản đích")
                return None
            changed = []
        changed += _write_fragment(session, comps, target, parent_id, thu_tu, frag, eff, action.action_id)
    else:
        quarantine("Loại lệnh chưa hỗ trợ tự áp dụng")
        return None

    action.status, action.reason, action.applied_at = "applied", "", datetime.now()
    logger.info("Áp lệnh %s %s -> %s (%d đơn vị đổi, hiệu lực %s)", action.op, info.get("text"),
                target_legal_id, len(set(changed)), eff)
    return sorted(set(changed))


def _insert_position(comps, target: str, unit: str, anchor: dict | None, legal_id: str):
    parent_id = target.rsplit(":", 1)[0] if unit != "Điều" else None
    if unit != "Điều" and parent_id not in comps:
        return False, 0.0
    anchor_id = component_id_for(legal_id, anchor) if anchor and anchor.get("dieu") else None
    if anchor_id and anchor_id in comps:
        base = comps[anchor_id]
        siblings = [c.thu_tu for c in comps.values() if c.parent_id == base.parent_id and c.thu_tu > base.thu_tu]
        nxt = min(siblings) if siblings else base.thu_tu + 1.0
        return (base.parent_id if unit == "Điều" else parent_id), (base.thu_tu + nxt) / 2
    if unit == "Điều":
        dieus = [c for c in comps.values() if c.unit == "Điều"]
        last = max(dieus, key=lambda c: c.thu_tu)
        return last.parent_id, last.thu_tu + 0.5
    siblings = [c.thu_tu for c in comps.values() if c.parent_id == parent_id]
    return parent_id, (max(siblings) + 0.5) if siblings else comps[parent_id].thu_tu + 0.01


def _write_fragment(session, comps, cid: str, parent_id, thu_tu: float, frag, eff, action_id) -> list[str]:
    changed = [cid]
    if cid not in comps:
        path_parent = comps[parent_id].path if parent_id and parent_id in comps else ""
        comp = LegalComponent(component_id=cid, legal_id=cid.split(":dieu-")[0], parent_id=parent_id,
                              unit=frag.unit, so=frag.so, thu_tu=thu_tu,
                              path=" > ".join(p for p in (path_parent, f"{frag.unit} {frag.so}") if p))
        session.add(comp)
        comps[cid] = comp
    session.add(LegalVersion(version_id=_vid(cid, action_id, _sha(frag.text)), component_id=cid,
                             text=frag.text, sha256=_sha(frag.text), valid_from=eff,
                             created_by_action=action_id))
    for k, child in enumerate(frag.children):
        seg = "khoan" if child.unit == "Khoản" else "diem"
        child_id = f"{cid}:{seg}-{child.so.lower()}"
        changed += _write_fragment(session, comps, child_id, cid, thu_tu + (k + 1) / 1000.0, child, eff,
                                   action_id)
    return changed


# --------------------------------------------------------------------------- #
# Đọc
# --------------------------------------------------------------------------- #
def _valid_at(v: LegalVersion, d: date) -> bool:
    return (v.valid_from is None or v.valid_from <= d) and (v.valid_to is None or v.valid_to > d)


def _nodes(comps: dict[str, LegalComponent]) -> list[Node]:
    return [Node(c.component_id, c.parent_id, c.unit, c.so, c.thu_tu, c.path) for c in comps.values()]


@dataclass(slots=True)
class Snapshot:
    valid_from: date | None
    valid_to: date | None
    text: str
    versions: dict[str, str]          # component_id -> version_id có hiệu lực trong khoảng này


async def snapshots(session: AsyncSession, legal_id: str) -> list[Snapshot]:
    """Các khoảng thời gian mà văn bản có nội dung khác nhau, mỗi khoảng một bản dựng lại."""
    comps, versions = await _load(session, legal_id)
    if not comps:
        return []
    bounds = sorted({d for v in versions for d in (v.valid_from, v.valid_to) if d is not None})
    edges: list[date | None] = [None, *bounds, None]
    nodes = _nodes(comps)
    out: list[Snapshot] = []
    for start, end in zip(edges, edges[1:]):
        probe = start if start is not None else (bounds[0].fromordinal(bounds[0].toordinal() - 1)
                                                  if bounds else date.today())
        live = [v for v in versions if _valid_at(v, probe)]
        texts = {v.component_id: v.text for v in live}
        out.append(Snapshot(start, end, render(nodes, texts), {v.component_id: v.version_id for v in live}))
    return out


async def lookup(session: AsyncSession, legal_id: str, address: dict[str, str], as_of: date) -> dict | None:
    """Chữ của một Điều/Khoản/Điểm tại ngày `as_of`, kèm ngữ cảnh cha (tiêu đề Điều, câu dẫn Khoản)."""
    comps, versions = await _load(session, legal_id)
    target = component_id_for(legal_id, address)
    approximate = False
    if target not in comps:
        # Điểm đánh thẳng dưới Điều (Điều không chia khoản)
        alt = component_id_for(legal_id, {"dieu": address["dieu"], "diem": address["diem"]}) \
            if address.get("diem") and address.get("khoan") else None
        if alt and alt in comps:
            target = alt
        else:
            # Parser không tách được đơn vị con (vd. legal_grammar bỏ sót một Điểm): lùi về Khoản rồi
            # Điều chứa nó - chữ của đơn vị con vẫn nằm trong chữ đơn vị cha.
            for parent in ({k: v for k, v in address.items() if k != "diem"}, {"dieu": address["dieu"]}):
                pid = component_id_for(legal_id, parent)
                if pid in comps:
                    target, approximate = pid, True
                    break
            else:
                return None
    live = {v.component_id: v for v in versions if _valid_at(v, as_of)}
    if target not in live:
        return {"component_id": target, "text": "", "repealed": True, "path": comps[target].path,
                "versions": {}, "valid_from": None}
    texts = {cid: v.text for cid, v in live.items()}
    nodes = _nodes(comps)
    body = subtree_text(nodes, target, texts)
    # Ngữ cảnh cha: dòng tiêu đề Điều, và câu dẫn Khoản khi hỏi một Điểm.
    context: list[str] = []
    cur = comps[target].parent_id
    while cur and cur in comps and comps[cur].unit in ("Khoản", "Điều"):
        head = (texts.get(cur) or "").split("\n", 1)[0].strip()
        if head:
            context.insert(0, head)
        cur = comps[cur].parent_id
    sub_ids = [cid for cid in live if cid == target or cid.startswith(target + ":")]
    return {"component_id": target, "path": comps[target].path, "text": "\n\n".join([*context, body]),
            "versions": {cid: live[cid].version_id for cid in sub_ids},
            "valid_from": live[target].valid_from, "valid_to": live[target].valid_to, "repealed": False,
            "approximate": approximate}


async def find_documents(session: AsyncSession) -> list[LegalDocument]:
    return list((await session.execute(select(LegalDocument))).scalars().all())


# --------------------------------------------------------------------------- #
# Viện dẫn trong cùng văn bản ("khoản 3 Điều 13 Nghị định này")
# --------------------------------------------------------------------------- #
_REF = re.compile(r"\b(?:(điểm\s+[a-zđ]\d?)\s+)?(?:(khoản\s+\d+[a-z]?)\s+)?(Điều\s+\d+[a-z]?)\b"
                  r"(?P<tail>[^.;:\n]{0,60})")


def _add_citations(session: AsyncSession, legal_id: str, nodes: list[Node]) -> None:
    known = {n.component_id for n in nodes}
    for n in nodes:
        if n.unit not in ("Điều", "Khoản", "Điểm"):
            continue
        body = n.text.split("\n", 1)[1] if n.unit == "Điều" and "\n" in n.text else n.text
        for m in _REF.finditer(body):
            tail = m.group("tail") or ""
            # "... Luật Quản lý, sử dụng tài sản công" -> viện dẫn văn bản khác; chỉ nối
            # đích khi rõ là văn bản này (không nêu tên văn bản khác, hoặc "... này").
            other = re.match(r"\s*(?:của\s+)?(Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định)\b", tail)
            if other and not re.match(r"\s*(?:của\s+)?\S+\s+này\b", tail):
                to = None
            else:
                addr = {"dieu": m.group(3).split()[1]}
                if m.group(2):
                    addr["khoan"] = m.group(2).split()[1]
                if m.group(1):
                    addr["diem"] = m.group(1).split()[1]
                to = component_id_for(legal_id, addr)
                to = to if to in known else None
            ref = " ".join(p for p in (m.group(1), m.group(2), m.group(3)) if p) + (tail[:60] if other else "")
            session.add(LegalCitation(citation_id=_vid(n.component_id, m.start(), ref), from_component_id=n.component_id,
                                      reference=ref[:500], to_component_id=to))
