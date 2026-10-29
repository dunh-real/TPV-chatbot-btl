"""Danh tính của một văn bản quy phạm: số hiệu, loại, tên, ngày ban hành, ngày hiệu lực.

Thẻ thể thức (`app.rag.doc_card`) phục vụ câu hỏi "ai ký, số mấy" và đọc thiếu ở VBQPPL:
không thấy "Luật số: 23/2026/QH16", và cắt "76 /2026/TT-BXD" thành "76". Ở đây đọc lại
cùng các dòng logic (`logical_lines`) nhưng với mẫu riêng cho số hiệu VBQPPL.

Ngày hiệu lực lấy từ câu "có hiệu lực (thi hành) (kể) từ ngày …" trong thân văn bản; không
thấy thì để None - chưa biết, không lấy ngày ký thay vào.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from app.rag.doc_card import logical_lines

# "Số: 379/2026/NĐ-CP", "Luật số: 23/2026/QH16", "Số: 76 /2026/TT-BXD"
_SO_HIEU = re.compile(r"(?:^|\b)(?:Luật\s+|Bộ luật\s+)?[Ss]ố\s*:?\s*(\d+\s*/\s*\d{4}\s*/\s*[A-ZĐa-zđ0-9][A-ZĐa-zđ0-9\-]*)")
_SO_HIEU_NHAN = re.compile(r"(?:Luật\s+|Bộ luật\s+)?Số\s*:\s*(\d+\s*/\s*\d{4}\s*/\s*[A-ZĐa-zđ0-9][A-ZĐa-zđ0-9\-]*)")
_SO_HIEU_TRONG = re.compile(r"Số\s*:\s*/\s*(\d{4})\s*/\s*([A-ZĐ][A-ZĐ0-9\-]*)")
_SO_HIEU_TRAN = re.compile(r"\b(\d+\s*/\s*\d{4}\s*/\s*[A-ZĐ][A-ZĐ0-9\-]*)")
_LOAI = re.compile(r"^(BỘ LUẬT|LUẬT|PHÁP LỆNH|NGHỊ QUYẾT|NGHỊ ĐỊNH|THÔNG TƯ LIÊN TỊCH|THÔNG TƯ|QUYẾT ĐỊNH|CHỈ THỊ)$")
# Tên loại VIẾT HOA rồi tên văn bản trên cùng dòng; "Luật số: 24/2008/QH12" (viết thường, có "số") không khớp.
_LOAI_TEN = re.compile(r"^(BỘ LUẬT|LUẬT|PHÁP LỆNH|NGHỊ QUYẾT|NGHỊ ĐỊNH|THÔNG TƯ LIÊN TỊCH|THÔNG TƯ|QUYẾT ĐỊNH|CHỈ THỊ)"
                       r"\s+(?!SỐ\b|số\b)(\S.{2,300})$")
_CAU_TRUC = re.compile(r"^(?:Phần|PHẦN|Chương|CHƯƠNG|Mục|MỤC|Điều)\s+\S")
_NGAY = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I)
_HIEU_LUC = re.compile(
    r"có\s+hiệu\s+lực(?:\s+thi\s+hành)?(?:\s+kể)?\s+từ\s+ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})",
    re.I)
_HIEU_LUC_KY = re.compile(r"có\s+hiệu\s+lực(?:\s+thi\s+hành)?\s+(?:kể\s+)?từ\s+ngày\s+ký", re.I)
_HIEU_LUC_SO = re.compile(r"có\s+hiệu\s+lực(?:\s+thi\s+hành)?(?:\s+kể)?\s+từ\s+ngày\s+(\d{1,2})/(\d{1,2})/(\d{4})", re.I)

# Mã loại trong số hiệu -> loại văn bản (dùng khi văn bản không in tên loại).
_MA_LOAI = (("NĐ-CP", "Nghị định"), ("TTLT", "Thông tư liên tịch"), ("TT-", "Thông tư"),
            ("QĐ-", "Quyết định"), ("NQ-", "Nghị quyết"), ("CT-", "Chỉ thị"), ("QH", "Luật"))
_VIET_TAT = {"Bộ luật": "bl", "Luật": "luat", "Pháp lệnh": "pl", "Nghị quyết": "nq", "Nghị định": "nd",
             "Thông tư liên tịch": "ttlt", "Thông tư": "tt", "Quyết định": "qd", "Chỉ thị": "ct"}


@dataclass(slots=True)
class DocIdentity:
    legal_id: str
    so_hieu: str = ""
    loai: str = ""
    ten: str = ""
    ngay_ban_hanh: date | None = None
    ngay_hieu_luc: date | None = None
    # Tên văn bản bị sửa, đọc từ tiêu đề Điều ("Sửa đổi, bổ sung một số điều của Luật ...").
    sua_doi: list[str] = field(default_factory=list)

    @property
    def display(self) -> str:
        """"Nghị định 379/2026/NĐ-CP" - tên ngắn dùng trong nhãn trích dẫn."""
        if self.loai and self.so_hieu:
            return f"{self.loai} {self.so_hieu}"
        return self.ten or self.so_hieu or self.legal_id


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.replace("đ", "d").replace("Đ", "D")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", strip_accents(text).lower()).strip("-")


def law_key(name: str) -> str:
    """Khoá so khớp tên văn bản: "Luật Ngân hàng Nhà nước Việt Nam số 46/2010/QH12"
    -> "luat ngan hang nha nuoc viet nam". Bỏ số hiệu, chữ "số", dấu câu, dấu thanh."""
    name = _SO_HIEU_TRAN.sub(" ", name)
    name = re.sub(r"\bsố\b", " ", name, flags=re.I)
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", strip_accents(name).lower()).split())


def _date(m: re.Match[str] | None) -> date | None:
    if m is None:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def normalize_so_hieu(raw: str) -> str:
    return re.sub(r"\s+", "", raw).upper()


def _loai_theo_ma(so_hieu: str) -> str:
    return next((ten_loai for ma, ten_loai in _MA_LOAI if ma in so_hieu), "")


def _cung_loai(so_hieu: str, loai: str) -> bool:
    """Mã trong số hiệu khớp tên loại in ở đầu văn bản (Bộ luật cũng mang mã QH như Luật)."""
    theo_ma = _loai_theo_ma(so_hieu)
    return not (theo_ma and loai) or theo_ma == loai or (theo_ma == "Luật" and loai == "Bộ luật")


def identify(text: str, doc_id: str, source: str = "") -> DocIdentity:
    from app.rag.converter import bo_chu_kirin_gia

    lines = logical_lines(bo_chu_kirin_gia(text))
    head = lines[:80]
    head20 = "\n".join(head[:20])

    loai, ten = "", ""
    for i, line in enumerate(head):
        # Tên loại đứng riêng một dòng ("NGHỊ ĐỊNH") hoặc chung dòng với tên văn bản ("LUẬT Quốc tịch Việt
        # Nam", "THÔNG TƯ Quy định ..."): bản PDF hay gộp hai dòng đó. Thiếu tên thì lệnh sửa đổi nhắm "Luật
        # Quốc tịch Việt Nam" không khớp được văn bản đích (Luật 79/2025 -> Luật 24/2008).
        m = _LOAI.match(line.strip()) or _LOAI_TEN.match(line.strip())
        if m:
            loai = m.group(1).capitalize()            # "NGHỊ ĐỊNH" -> "Nghị định"
            parts = [m.group(2).strip()] if m.lastindex and m.lastindex >= 2 else []
            # Tên: các dòng ngay sau tên loại, trước "Căn cứ ..."
            for nxt in head[i + 1:i + 4]:
                if nxt.lower().startswith(("căn cứ", "theo đề nghị", "quốc hội ban hành", "chính phủ ban hành")) \
                        or _CAU_TRUC.match(nxt):
                    break
                parts.append(nxt.strip())
            ten = " ".join(parts)
            break

    so_hieu = ""
    # Ưu tiên nhãn "Số:" của phần đầu văn bản, cho phép số hiệu rơi sang dòng sau: logical_lines tách ô
    # bảng tại chỗ có hai dấu cách, nên "Số:␣␣76 /2026/TT-BXD" thành hai dòng. Không có nhãn đó mới dò
    # từng dòng - và bỏ dòng "Căn cứ ..." (số hiệu của văn bản KHÁC: "Căn cứ Bộ luật ... số 95/2015/QH13").
    if (m := _SO_HIEU_NHAN.search(head20)) is not None:
        so_hieu = normalize_so_hieu(m.group(1))
    elif (m := _SO_HIEU_TRONG.search(head20)) is not None:
        # "Số:     /2026/TT-BNV": bản ký số để trống số, số nằm trong dấu ký số (OCR/lớp chữ đọc thành
        # dòng rời "25 01 10"). Không dò tiếp thân văn bản - dòng đầu tiên có số hiệu ở đó là văn bản
        # được viện dẫn (TT 25/2026 bị gán 368/2026/NĐ-CP). Lấy số từ tên file nếu đứng ngay trước năm.
        nam, ma = m.group(1), m.group(2)
        if (n := re.search(rf"(?<!\d)(\d{{1,4}})[._\-\s]+{nam}(?!\d)", source)) is not None:
            so_hieu = normalize_so_hieu(f"{n.group(1)}/{nam}/{ma}")
    else:
        for line in head:
            if line.lower().startswith(("căn cứ", "theo đề nghị")):
                continue
            if (m := _SO_HIEU.search(line)) is not None and _cung_loai(normalize_so_hieu(m.group(1)), loai):
                so_hieu = normalize_so_hieu(m.group(1))
                break

    if not loai and so_hieu:
        loai = _loai_theo_ma(so_hieu)

    ngay_ban_hanh = next((_date(_NGAY.search(line)) for line in head if _NGAY.search(line)), None)
    ngay_hieu_luc = _date(_HIEU_LUC.search(text)) or _date(_HIEU_LUC_SO.search(text))
    if ngay_hieu_luc is None and _HIEU_LUC_KY.search(text):
        # "... có hiệu lực thi hành kể từ ngày ký ban hành": ngày hiệu lực = ngày ký.
        ngay_hieu_luc = ngay_ban_hanh

    sua_doi = [m.group(1).strip(" .;:") for m in
               re.finditer(r"^(?:#+\s*)?\**Điều\s+\d+[a-z]?\.\s*(?:Sửa đổi|Bổ sung|Sửa đổi, bổ sung)[^\n]*?\bcủa\s+([^\n*]+)",
                           text, re.M)]

    abbr = _VIET_TAT.get(loai, "vb")
    legal_id = f"vn:{abbr}:{slug(so_hieu)}" if so_hieu else f"vn:{abbr}:doc-{doc_id}"
    if not ten:
        ten = source
    return DocIdentity(legal_id=legal_id, so_hieu=so_hieu, loai=loai, ten=" ".join(ten.split()),
                       ngay_ban_hanh=ngay_ban_hanh, ngay_hieu_luc=ngay_hieu_luc, sua_doi=sua_doi)
