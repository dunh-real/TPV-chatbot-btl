"""Đọc lệnh sửa đổi trong một văn bản sửa đổi -> danh sách `ActionSpec` (chưa áp dụng).

Mẫu câu lệnh theo thể thức VBQPPL (Luật Ban hành VBQPPL, Nghị định 30/2020):

    "Sửa đổi, bổ sung khoản 15 Điều 4 như sau:"                -> sua_doi  khoản 15 Điều 4
    "Bổ sung Điều 31a vào sau Điều 31 như sau:"                 -> bo_sung  Điều 31a (sau Điều 31)
    "Sửa đổi, bổ sung một số khoản của Điều 35 như sau:"        -> lệnh chứa; các Điểm con
       "a) Sửa đổi, bổ sung khoản 1 như sau:"                   -> sua_doi  khoản 1 Điều 35
    "Bãi bỏ khoản 3 Điều 7, khoản 2 Điều 22 và Điều 49."        -> bai_bo   (nhiều đích)
    "Thay cụm từ “A” bằng cụm từ “B” tại ..."                   -> thay_cum_tu (luôn cách ly)

Văn bản đích lấy từ tiêu đề Điều chứa lệnh ("Sửa đổi, bổ sung một số điều của Luật Ngân hàng
Nhà nước Việt Nam"). Nội dung mới là các dòng trích dẫn ngay sau lệnh (legal_grammar đã
tách chúng khỏi đơn vị của chính văn bản). Ở đây chỉ ĐỌC; áp dụng nằm ở `service`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documents.legal_grammar import AMEND_VERB
from app.legal.structure import Scan, plain_line, strip_quotes, unit_number

_TOKEN = re.compile(r"\b(điểm|khoản|Điều)\s+(\d{1,3}[a-z]?|[a-zđ]\d?)\b", re.I)
_LEVEL = {"điểm": 0, "khoản": 1, "điều": 2}
_KEY = {"điểm": "diem", "khoản": "khoan", "điều": "dieu"}
_CONTAINER = re.compile(r"\b(một số|các)\s+(điểm|khoản|điều)", re.I)
_INSERT_AFTER = re.compile(r"\bvào\s+(?:sau|trước)\b", re.I)
_LAW_IN_TITLE = re.compile(r"\bcủa\s+(.+?)\s*$")
_IMPERATIVE = re.compile(r"^(?:\d{1,3}[a-z]?\.|[a-zđ]\d?\))\s*(?:Sửa đổi|Bổ sung|Bãi bỏ|Thay thế|Thay cụm từ|"
                         r"Bỏ cụm từ|Đính chính)\b", re.I)
# Tên văn bản đích dừng trước số hiệu / cơ quan ban hành / phần mô tả sửa đổi trước đó:
# "của Pháp lệnh Ngoại hối số 28/2005/PL-UBTVQH11 của Ủy ban thường vụ Quốc hội đã được ..."
_LAW_INLINE = re.compile(r"\bcủa\s+((?:Bộ luật|Luật|Pháp lệnh|Nghị định|Nghị quyết|Thông tư|Quyết định)\b[^;:]*?)"
                         r"(?=\s+(?:số\s+\d|của\s|đã\s+được|được\s+sửa|có hiệu lực|kể từ|hết hiệu lực)|[.;:]?\s*$)")


@dataclass(slots=True)
class ActionSpec:
    op: str                                   # sua_doi | bo_sung | bai_bo | thay_cum_tu
    target_law: str
    address: dict[str, str]                   # {"dieu": "35", "khoan": "1", "diem": ...}
    anchor: dict[str, str] | None = None      # "vào sau ..." của bo_sung
    payload: list[list[str]] = field(default_factory=list)   # mỗi nhóm = một đơn vị mới
    instruction: str = ""
    source_line: int = -1
    reason: str = ""

    @property
    def target_unit(self) -> str:
        if self.address.get("diem"):
            return "Điểm"
        if self.address.get("khoan"):
            return "Khoản"
        return "Điều"

    @property
    def address_text(self) -> str:
        parts = []
        if self.address.get("diem"):
            parts.append(f"điểm {self.address['diem']}")
        if self.address.get("khoan"):
            parts.append(f"khoản {self.address['khoan']}")
        if self.address.get("dieu"):
            parts.append(f"Điều {self.address['dieu']}")
        return " ".join(parts)


def parse_addresses(text: str, ctx: dict[str, str] | None = None) -> list[dict[str, str]]:
    """"khoản 3 Điều 7, khoản 2 Điều 22 và Điều 49" -> ba địa chỉ. Địa chỉ viết từ nhỏ tới
    lớn, nên gặp một cấp không lớn hơn cấp vừa đọc là sang địa chỉ mới. Cấp còn thiếu lấy
    từ ngữ cảnh (lệnh chứa: "...của Điều 59 như sau:")."""
    # Bỏ phần trong ngoặc kép: "Thay cụm từ “Điều 5”..." không phải địa chỉ đích.
    text = re.sub(r"[“\"][^”\"]*[”\"]", " ", text)
    out: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    last = -1
    for m in _TOKEN.finditer(text):
        word = m.group(1).lower()
        lvl = _LEVEL[word]
        if cur and lvl <= last:
            out.append(cur)
            cur = {}
        cur[_KEY[word]] = m.group(2) if word == "điều" else m.group(2).lower()
        last = lvl
    if cur:
        out.append(cur)
    ctx = ctx or {}
    for a in out:
        # Điền cấp trên còn thiếu từ ngữ cảnh, chỉ khi địa chỉ chưa tự nêu cấp đó.
        if "dieu" not in a and ctx.get("dieu"):
            a["dieu"] = ctx["dieu"]
            if "khoan" not in a and "diem" in a and ctx.get("khoan"):
                a["khoan"] = ctx["khoan"]
    return out


def _payload_groups(lines: list[str], top_unit: str) -> list[list[str]]:
    """Tách đoạn trích thành từng đơn vị mới ("4. ..." và "5. ..." khi bổ sung hai khoản)."""
    lines = strip_quotes(lines)
    if not lines:
        return []
    starts = {
        "Điều": re.compile(r"^[“\"]?\s*Điều\s+\d+[a-z]?\."),
        "Khoản": re.compile(r"^[“\"]?\s*\d{1,3}[a-z]?\.\s"),
        "Điểm": re.compile(r"^[“\"]?\s*[a-zđ]\d?\)\s"),
    }[top_unit]
    groups: list[list[str]] = []
    for ln in lines:
        p = plain_line(ln)
        if starts.match(p) or not groups:
            groups.append([p.lstrip("“\"‘ ")])
        else:
            groups[-1].append(p)
    # Nhóm ngoài cùng chỉ hợp lệ khi mở đầu đúng đơn vị đích (bỏ ngoặc từng nhóm con).
    return [strip_quotes(g) for g in groups]


def _instructions(sc: Scan):
    """Các dòng lệnh theo thứ tự: (dòng, chữ, loại, ngữ cảnh Điều, luật đích, các dòng thân).

    Lệnh nằm ở ba chỗ: một Khoản ("3. Bổ sung Điều 31a ..."), một Điểm của lệnh chứa
    ("a) Sửa đổi, bổ sung khoản 1 như sau:"), hoặc ngay trong thân Điều khi chính tiêu đề
    Điều đã nêu đích ("Điều 3. Sửa đổi, bổ sung khoản 2 Điều 114 của Luật Các tổ chức tín
    dụng" / "Bổ sung điểm e vào sau điểm đ như sau:").
    """
    heads = sorted(sc.heads.items())
    own_lines = [i for i, h in heads if h.kind.startswith("OWN_")]
    law, title_ctx = "", {}
    for i, h in heads:
        text = sc.plain.get(i, "")
        nxt = next((j for j in own_lines if j > i), len(sc.lines))
        if h.kind == "OWN_DIEU":
            m = _LAW_IN_TITLE.search(text)
            law = m.group(1).strip(" .;:") if (m and AMEND_VERB.search(text)) else ""
            # Bỏ "Điều n." của chính văn bản trước khi đọc đích trong tiêu đề.
            title = re.sub(r"^Điều\s+\d+[a-z]?\.\s*", "", text[:m.start()] if m else text)
            addrs = parse_addresses(title) if law else []
            title_ctx = addrs[-1] if addrs else {}
            if not law:
                continue
            # Lệnh trong thân Điều (trước Khoản đầu tiên).
            body_lines = [k for k in range(i + 1, nxt) if sc.lines[k].strip()]
            for pos, k in enumerate(body_lines):
                line = sc.plain.get(k, "")
                if line.startswith(("“", '"', "‘")) or not AMEND_VERB.search(line) \
                        or not re.search(r"như sau|bãi bỏ|thay cụm từ|bỏ cụm từ", line, re.I):
                    continue
                end = next((body_lines[p] for p in range(pos + 1, len(body_lines))
                            if not sc.plain.get(body_lines[p], "").startswith(("“", '"', "‘"))
                            and AMEND_VERB.search(sc.plain.get(body_lines[p], ""))
                            and re.search(r"như sau", sc.plain.get(body_lines[p], ""), re.I)), nxt)
                yield k, line, "body", title_ctx, law, [ln for ln in sc.lines[k + 1:end] if ln.strip()]
            continue
        if h.kind not in ("OWN_KHOAN", "OWN_DIEM"):
            continue
        unit_law = law
        if not unit_law and _IMPERATIVE.match(text):
            # Lệnh tự nêu văn bản đích, đặt ở Điều không mang tiêu đề sửa đổi - thường là Điều
            # khoản thi hành: "2. Bãi bỏ Điều 22 và khoản 2 Điều 34 của Pháp lệnh Ngoại hối ...".
            # Bắt buộc MỞ ĐẦU bằng động từ mệnh lệnh: câu dẫn chiếu "... của Luật X được sửa
            # đổi, bổ sung bởi ..." cũng có động từ sửa đổi nhưng không phải lệnh.
            m = _LAW_INLINE.search(text)
            unit_law = m.group(1).strip(" .;:") if m else ""
        if not unit_law:
            continue
        yield i, text, ("khoan" if h.kind == "OWN_KHOAN" else "diem"), title_ctx, unit_law, \
            [ln for ln in sc.lines[i + 1:nxt] if ln.strip()]


def extract_actions(sc: Scan) -> list[ActionSpec]:
    """Mọi lệnh sửa đổi trong văn bản đã quét (`structure.scan` với đủ Khoản/Điểm)."""
    actions: list[ActionSpec] = []
    ctx: dict[str, str] = {}
    ctx_unit = ""
    for i, text, kind, title_ctx, law, body in _instructions(sc):
        if kind == "khoan":
            ctx, ctx_unit = dict(title_ctx), ""
        elif kind == "body":
            ctx, ctx_unit = dict(title_ctx), "container"
        if not AMEND_VERB.search(text):
            continue
        low = text.lower()

        if re.search(r"\b(thay|bỏ)\s+cụm\s+từ\b|\bthay\s+thế\s+cụm\s+từ\b", low):
            actions.append(ActionSpec("thay_cum_tu", law, {}, instruction=text, source_line=i,
                                      reason="Lệnh thay/bỏ cụm từ cần người duyệt (chưa tự áp dụng)"))
            continue

        # Lệnh chứa luôn kết thúc bằng "như sau:"; câu khác có "một số điều" (vd. "... đã được
        # sửa đổi, bổ sung một số điều theo ...") không phải lệnh chứa.
        if kind == "khoan" and _CONTAINER.search(low) and not body and re.search(r"như sau\s*:?\s*$", low):
            # Lệnh chứa: "Sửa đổi, bổ sung một số điểm, khoản của Điều 59 như sau:" -
            # các Điểm con mang lệnh thật; ghi lại Điều/khoản làm ngữ cảnh.
            addrs = parse_addresses(text, title_ctx)
            ctx = addrs[-1] if addrs else dict(title_ctx)
            ctx_unit = "container"
            continue

        sub_ctx = ctx if (kind in ("diem", "body") and ctx_unit == "container") else dict(title_ctx)
        addrs = parse_addresses(text, sub_ctx)
        if not addrs:
            actions.append(ActionSpec("sua_doi", law, {}, instruction=text, source_line=i,
                                      reason="Không đọc được địa chỉ đích"))
            continue

        # Loại lệnh theo ĐỘNG TỪ ĐỨNG ĐẦU câu lệnh. Dò cả câu thì sai: "Bãi bỏ Điều 22 ...
        # của Pháp lệnh Ngoại hối ... đã được sửa đổi, bổ sung bởi ..." chứa cả "sửa đổi".
        lead = _lead_verb(text)
        if lead == "bãi bỏ" or (not lead and re.search(r"\bbãi\s+bỏ\b", low)
                                 and not re.search(r"\bsửa\s+đổi\b|\bbổ\s+sung\b", low)):
            for a in addrs:
                actions.append(ActionSpec("bai_bo", law, a, instruction=text, source_line=i))
            continue

        if _INSERT_AFTER.search(low) and (lead == "bổ sung" or (not lead and re.search(r"\bbổ\s+sung\b", low))):
            # "Bổ sung khoản 4 và khoản 5 vào sau khoản 3": địa chỉ cuối là mốc chèn.
            *news, anchor = addrs if len(addrs) > 1 else (addrs + [None])
            if anchor:
                for a in news:
                    for key in ("dieu", "khoan"):
                        if key not in a and key in anchor and _rank(key) > _rank(_lowest(a)):
                            a[key] = anchor[key]
            groups = _payload_groups(body, ActionSpec("", "", news[0]).target_unit) if body else []
            for k, a in enumerate(news):
                payload = [groups[k]] if k < len(groups) else []
                actions.append(ActionSpec("bo_sung", law, a, anchor=anchor, payload=payload,
                                          instruction=text, source_line=i,
                                          reason="" if payload else "Thiếu nội dung bổ sung"))
            continue

        # Sửa đổi (có nội dung mới) - mặc định cho "sửa đổi, bổ sung ... như sau:"
        target = addrs[0]
        groups = _payload_groups(body, ActionSpec("", "", target).target_unit) if body else []
        actions.append(ActionSpec("sua_doi", law, target, payload=groups[:1], instruction=text,
                                  source_line=i,
                                  reason="" if groups else "Thiếu nội dung sửa đổi"))
    return actions


# Lệnh làm CẢ một văn bản hết hiệu lực - thường ở Điều khoản thi hành:
#   "Bãi bỏ Thông tư số 118/2025/TT-BTC ngày ..."            "Nghị định này thay thế Nghị định số 72/2023/NĐ-CP"
#   "Luật Đất đai số 45/2013/QH13 ... hết hiệu lực kể từ ngày Luật này có hiệu lực thi hành"
# Câu nhắm một phần văn bản ("Bãi bỏ Điều 5 của Thông tư số ...", "các quy định tại ...") KHÔNG phải lệnh
# cấp văn bản - phần đó do extract_actions xử lý (hoặc cách ly).
_LOAI_VB = r"(?:Bộ luật|Luật|Pháp lệnh|Nghị quyết|Nghị định|Thông tư liên tịch|Thông tư|Quyết định|Chỉ thị)"
_SO_HIEU_VB = r"(\d{1,4}\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9\-]*)"
_HET_HIEU_LUC_DONG_TU = re.compile(r"\b(?:bãi\s+bỏ|thay\s+thế)\s+(?:toàn\s+bộ\s+)?(?:các\s+)?(?=" + _LOAI_VB + r"\b)", re.I)
_VB_SO = re.compile(_LOAI_VB + r"\b[^;:]{0,200}?\bsố\s+" + _SO_HIEU_VB)
_VB_HET_HIEU_LUC = re.compile(_LOAI_VB + r"\b[^;:]{0,200}?\bsố\s+" + _SO_HIEU_VB + r"[^;:]{0,250}?\bhết\s+hiệu\s+lực\b",
                              re.I)
_NOI_TIEP = re.compile(r"(?:\s*ngày\s+\d{1,2}(?:\s+tháng\s+|/)\d{1,2}(?:\s+năm\s+|/)\d{4})?\s*(?:,|và|,\s*và)\s*")
_MOT_PHAN = re.compile(r"\b(?:điều|khoản|điểm|cụm\s+từ|quy\s+định\s+tại|nội\s+dung)\b", re.I)


def extract_doc_actions(sc: Scan) -> list[ActionSpec]:
    """Lệnh làm hết hiệu lực cả một văn bản (op `het_hieu_luc`, target_law = số hiệu văn bản đích)."""
    actions: list[ActionSpec] = []
    seen: set[str] = set()
    for i, raw in enumerate(sc.lines):
        text = plain_line(raw)
        if not text or text.startswith(("“", '"', "‘")):
            continue
        found: list[str] = []
        for m in _HET_HIEU_LUC_DONG_TU.finditer(text):
            clause = re.split(r"[;:]|\.\s", text[m.end():], maxsplit=1)[0]
            first = _VB_SO.search(clause)
            if first is None or _MOT_PHAN.search(clause[:first.start()]):
                continue
            found.append(first.group(1))
            # Văn bản liệt kê tiếp chỉ khi nối thẳng bằng "," / "và" (có thể kèm "ngày ..."): tên văn bản
            # bị bãi bỏ hay chứa số hiệu văn bản khác ("... hướng dẫn Luật ... số 06/2022/QH15").
            end = first.end()
            for nxt in _VB_SO.finditer(clause, end):
                if not _NOI_TIEP.fullmatch(clause[end:nxt.start()]):
                    break
                found.append(nxt.group(1))
                end = nxt.end()
        for m in _VB_HET_HIEU_LUC.finditer(text):
            if not _MOT_PHAN.search(text[max(0, m.start() - 40):m.start()]):
                found.append(m.group(1))
        for so_hieu in found:
            so_hieu = re.sub(r"\s+", "", so_hieu).upper()
            if so_hieu in seen:
                continue
            seen.add(so_hieu)
            actions.append(ActionSpec("het_hieu_luc", so_hieu, {}, instruction=text, source_line=i))
    return actions


_LEAD = re.compile(r"^(?:\d{1,3}[a-z]?\.|[a-zđ]\d?\))?\s*(sửa đổi, bổ sung|sửa đổi|bổ sung|bãi bỏ|thay thế|"
                   r"thay cụm từ|bỏ cụm từ|đính chính)\b", re.I)


def _lead_verb(text: str) -> str:
    m = _LEAD.match(text.strip())
    return m.group(1).lower() if m else ""


def _lowest(addr: dict[str, str]) -> str:
    return "diem" if "diem" in addr else ("khoan" if "khoan" in addr else "dieu")


def _rank(key: str) -> int:
    return {"diem": 0, "khoan": 1, "dieu": 2}[key]


def unit_of_payload(group: list[str]) -> tuple[str, str]:
    """(loại, số) của dòng đầu đoạn trích."""
    first = plain_line(group[0]).lstrip("“\"‘ ") if group else ""
    for unit in ("Điều", "Khoản", "Điểm"):
        so = unit_number(unit, first)
        if so:
            return unit, so
    return "", ""
