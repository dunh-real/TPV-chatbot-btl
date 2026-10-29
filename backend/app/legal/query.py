"""Đọc ý định pháp lý trong câu hỏi: địa chỉ (Điều/khoản/điểm), văn bản nào, tại ngày nào.

Không dùng LLM: địa chỉ và số hiệu có dạng cố định, đọc bằng mẫu là tất định và kiểm được.
Reranker không phân biệt được "Điều 8" với "Điều 25" (rag_testing: câu hỏi theo địa chỉ chỉ
Hit@4 43,8%), nên địa chỉ phải được tra thẳng chứ không đem đi chấm điểm ngữ nghĩa.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from app.legal.identity import law_key, normalize_so_hieu, strip_accents

_ADDR = re.compile(
    r"(?:\bđiểm\s+(?P<diem>[a-zđ])\b[\s,]*)?(?:\bkhoản\s+(?P<khoan>\d{1,3}[a-z]?)\b[\s,]*)?"
    r"\b[Đđ]iều\s+(?P<dieu>\d{1,3}[a-z]?)\b", re.I)
_SO_HIEU = re.compile(r"\b(\d{1,4}\s*/\s*\d{4}\s*/\s*[A-ZĐa-zđ][A-ZĐa-zđ0-9\-]*)")
_LOAI_SO = re.compile(r"\b(Bộ luật|Luật|Nghị định|Thông tư|Nghị quyết|Quyết định|Pháp lệnh)\s+(?:số\s+)?"
                      r"(\d{1,4})(?:\s*/\s*(\d{4}))?", re.I)
_NGAY = re.compile(r"ngày\s+(\d{1,2})\s*[/-]\s*(\d{1,2})\s*[/-]\s*(\d{4})|ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})"
                   r"\s+năm\s+(\d{4})", re.I)
_NAM = re.compile(r"\b(?:năm|trong năm|vào năm)\s+(\d{4})\b", re.I)
_TRUOC = re.compile(r"\btrước\s+khi\s+(?:được\s+)?(?:sửa\s+đổi|bổ\s+sung|thay\s+thế)", re.I)
_SAU = re.compile(r"\b(?:sau\s+khi\s+(?:được\s+)?(?:sửa\s+đổi|bổ\s+sung)|đã\s+(?:được\s+)?sửa\s+đổi|"
                  r"mới\s+(?:được\s+)?sửa\s+đổi|quy\s+định\s+mới)", re.I)


_VIEN_DAN = re.compile(r"\b(?:tại|theo|trong|ngoài|của|với|như|thuộc|dẫn\s+chiếu|căn\s+cứ)\s*$", re.I)


@dataclass(slots=True)
class LegalIntent:
    address: dict[str, str] = field(default_factory=dict)
    so_hieu: str = ""
    loai: str = ""
    so: str = ""
    nam: str = ""
    name_hint: str = ""
    as_of: date | None = None
    before_amendment: bool = False
    after_amendment: bool = False
    # Địa chỉ chỉ là viện dẫn trong câu hỏi về chuyện khác ("... nếu không thuộc trường hợp quy định
    # tại Điều 135 thì ...", "ngoài các quy định chung tại Điều 5"), không phải đơn vị được hỏi.
    address_is_reference: bool = False

    @property
    def has_address(self) -> bool:
        return bool(self.address.get("dieu"))

    @property
    def has_doc(self) -> bool:
        return bool(self.so_hieu or self.so or self.name_hint)


def _date(y: str, m: str, d: str) -> date | None:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_question(question: str) -> LegalIntent:
    q = " ".join(question.split())
    intent = LegalIntent()
    if (m := _ADDR.search(q)) is not None:
        intent.address = {k: v.lower() if k != "dieu" else v for k, v in m.groupdict().items() if v}
        intent.address_is_reference = bool(_VIEN_DAN.search(q[:m.start()]))
    if (m := _SO_HIEU.search(q)) is not None:
        intent.so_hieu = normalize_so_hieu(m.group(1))
    if (m := _LOAI_SO.search(q)) is not None:
        intent.loai, intent.so, intent.nam = m.group(1).capitalize(), m.group(2), m.group(3) or ""
    # Tên văn bản không số: "Bộ luật Hình sự", "Luật Ngân hàng Nhà nước Việt Nam"
    if (m := re.search(r"\b((?:Bộ luật|Luật)\s+[A-ZĐ][^\s?,.;:]*(?:\s+[^\s?,.;:]+){0,6})", q)) and not intent.so:
        intent.name_hint = m.group(1)
    if (m := _NGAY.search(q)) is not None:
        g = m.groups()
        intent.as_of = _date(g[2], g[1], g[0]) if g[0] else _date(g[5], g[4], g[3])
    elif (m := _NAM.search(q)) is not None:
        intent.as_of = _date(m.group(1), "12", "31")
    intent.before_amendment = bool(_TRUOC.search(q))
    intent.after_amendment = bool(_SAU.search(q)) and not intent.before_amendment
    return intent


def match_document(intent: LegalIntent, docs: list) -> list:
    """Văn bản trong kho khớp với văn bản người hỏi nêu (rỗng nếu không nêu / không khớp)."""
    if intent.so_hieu:
        hit = [d for d in docs if d.so_hieu and d.so_hieu.upper() == intent.so_hieu]
        if hit:
            return hit
    if intent.so:
        hit = [d for d in docs if d.so_hieu and d.so_hieu.split("/")[0] == intent.so
               and (not intent.nam or f"/{intent.nam}/" in d.so_hieu)
               and (not intent.loai or strip_accents(d.loai).lower() == strip_accents(intent.loai).lower())]
        if hit:
            return hit
    if intent.name_hint:
        key = law_key(intent.name_hint)
        hit = [d for d in docs if key and (law_key(f"{d.loai} {d.ten}").startswith(key)
                                           or key.startswith(law_key(f"{d.loai} {d.ten}") or "\0"))]
        if hit:
            return hit
    return []
