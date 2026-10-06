"""Lỗi MÁY MÓC trong mặt chữ - đo bằng vị trí ký tự, hoàn toàn tất định.

Thừa dấu cách, thiếu dấu cách sau dấu câu, dấu cách trước dấu câu, lặp từ, ngoặc
đơn không khớp. Đây đúng chỗ LLM mù nhất: bộ tách từ của model nuốt mất khoảng
trắng nên nó không "nhìn" thấy hai dấu cách liền nhau, còn regex thì thấy chính xác.

CHÍNH TẢ KHÔNG CÒN Ở ĐÂY. Trước có một bảng cặp sai->đúng dò bằng từ điển; đã bỏ
vì bảng cặp không kết luận được một mình: tiếng Việt viết rời từng âm tiết, nên hễ
cả hai vế đều là từ có thật thì luôn tồn tại câu ĐÚNG đặt chúng cạnh nhau. Dựng câu
chạy thử thì 26 cặp báo oan, kể cả cặp trông chắc nhất:

    "Đơn vị CŨNG CỐ gắng hoàn thành."        cũng + cố gắng
    "HỒ SƠ XUẤT khẩu đã được phê duyệt."     hồ sơ + xuất khẩu
    "Thành TỰU CHUNG của đơn vị năm 2026."   thành tựu + chung

Việc phân biệt "một từ viết sai" với "hai từ đứng cạnh nhau" cần đọc được ngữ cảnh,
mà đó là việc của LLM. Nay `agents.nodes.document.chinh_ta_llm_node` lo phần chính
tả, với prompt `DOC_SPELL_SYSTEM` mang sẵn phép thử tách rời. Bảng cặp cũ vẫn nằm
trong `config/chinh_ta.yaml` làm tư liệu - code KHÔNG đọc hai mục đó nữa.

Mỗi phát hiện mang theo VỊ TRÍ KÝ TỰ (`start`, `end`) trong khối, không chỉ mang
câu trích. Giao diện cần đúng hai con số đó để khoanh vùng lỗi ngay trên tài liệu
thay vì bắt người đọc tự dò lại câu trích trong văn bản.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator

import yaml

from app.documents.parser import Block

logger = logging.getLogger(__name__)

CHINH_TA_PATH = Path(__file__).resolve().parents[2] / "config" / "chinh_ta.yaml"

# Một chữ cái bất kỳ, kể cả chữ có dấu tiếng Việt. `\w` dính cả chữ số và gạch
# dưới nên không dùng được cho chỗ phân biệt "1,5" với "hôm nay,ngày mai".
CHU = r"[^\W\d_]"

# Hai dấu cách trở lên NẰM GIỮA hai chữ. Dấu cách đầu dòng là thụt lề, cuối dòng
# là rác vô hại - báo cả hai thứ đó chỉ làm loãng bản soát.
_THUA_DAU_CACH = re.compile(r"(?<=\S)[ \t]{2,}(?=\S)")

# Dấu cách trước dấu câu. Loại trừ "..." vì dấu ba chấm có lối viết riêng.
_CACH_TRUOC_DAU = re.compile(r"(?<=\S)[ \t]+(?=[,;:!?](?!\S)|\.(?!\.))")

# Thiếu dấu cách sau dấu phẩy/chấm phẩy. Vế PHẢI bắt buộc là chữ, vế trái nhận cả
# chữ số: "1,5 triệu" có chữ số ở cả hai bên nên là số liệu viết đúng, còn
# "...ngày 20/9/2026,kèm số liệu" thì chữ số bên trái mà chữ bên phải - dính liền
# thật. Đòi chữ ở cả hai bên thì bỏ lọt đúng dạng hay gặp nhất: dính sau một ngày
# tháng hoặc một con số.
_THIEU_CACH_SAU_DAU = re.compile(rf"(?<=[^\W_])([,;])(?={CHU})")

# Cùng một từ viết hai lần liền nhau, kể cả khi bị ngắt dòng ở giữa.
_LAP_TU = re.compile(rf"\b({CHU}+)(\s+)\1\b", re.IGNORECASE)

# "a)", "b)", "12)" - NỬA ngoặc của số thứ tự, không phải ngoặc đơn thiếu vế mở.
# Cách viết này chuẩn trong văn bản hành chính; đếm nó là ngoặc lẻ thì mỗi gạch
# đầu dòng thành một lỗi, và cả bản soát chìm trong báo oan.
_NUA_NGOAC = re.compile(rf"(?:^|[\s;:,])(?:\d{{1,2}}|{CHU})\)$")


@dataclass(slots=True)
class TypoFinding:
    """Một lỗi đối chiếu được bằng chuỗi, kèm vị trí chính xác trong khối."""

    block_id: str
    type: str                 # spelling | spacing | punctuation | duplicate
    start: int                # chỉ số ký tự trong `block.text`
    end: int
    quote: str
    suggest: str = ""
    message: str = ""
    severity: str = "warning"

    def as_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id, "type": self.type,
            "start": self.start, "end": self.end,
            "quote": self.quote, "suggest": self.suggest,
            "message": self.message, "severity": self.severity,
            # Giao diện vẽ khung LIỀN nét cho nguồn này và khung ĐỨT nét cho LLM:
            # hai mức bảo đảm khác nhau thì không được trông giống nhau.
            "source": "rule",
        }


@lru_cache
def load_chinh_ta(path: str | None = None) -> dict[str, Any]:
    target = Path(path) if path else CHINH_TA_PATH
    if not target.is_file():
        logger.warning("Không thấy %s - bỏ qua phần soát chính tả tất định", target)
        return {}
    with target.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _quet_co_hoc(block: Block, cfg: dict[str, Any]) -> Iterator[TypoFinding]:
    """Lỗi đo bằng vị trí ký tự. Bỏ qua khối bảng - xem chú thích trong YAML."""
    if block.kind == "table":
        return
    text = block.text

    if rule := cfg.get("thua_dau_cach"):
        for match in _THUA_DAU_CACH.finditer(text):
            yield TypoFinding(
                block_id=block.id, type="spacing",
                start=match.start(), end=match.end(), quote=match.group(0),
                suggest=" ",
                message=f"{rule.get('message', 'Thừa dấu cách')}: {len(match.group(0))} dấu cách liền nhau",
                severity=rule.get("severity", "warning"))

    if rule := cfg.get("cach_truoc_dau_cau"):
        for match in _CACH_TRUOC_DAU.finditer(text):
            yield TypoFinding(
                block_id=block.id, type="punctuation",
                start=match.start(), end=match.end(), quote=match.group(0),
                suggest="",
                message=rule.get("message", "Thừa dấu cách trước dấu câu"),
                severity=rule.get("severity", "warning"))

    if rule := cfg.get("thieu_cach_sau_dau_cau"):
        for match in _THIEU_CACH_SAU_DAU.finditer(text):
            dau = match.group(1)
            yield TypoFinding(
                block_id=block.id, type="punctuation",
                start=match.start(1), end=match.end(1), quote=dau,
                suggest=f"{dau} ",
                message=rule.get("message", "Thiếu dấu cách sau dấu câu"),
                severity=rule.get("severity", "warning"))

    if rule := cfg.get("lap_tu"):
        cho_phep = {str(t).lower() for t in (rule.get("cho_phep") or [])}
        for match in _LAP_TU.finditer(text):
            tu = match.group(1)
            if tu.lower() in cho_phep:         # từ láy đôi viết rời, lặp có chủ ý
                continue
            yield TypoFinding(
                block_id=block.id, type="duplicate",
                start=match.start(), end=match.end(), quote=match.group(0),
                suggest=tu,
                message=f"{rule.get('message', 'Lặp từ')}: “{tu}” viết hai lần liền nhau",
                severity=rule.get("severity", "warning"))

    if rule := cfg.get("ngoac_khong_dong"):
        yield from _ngoac(block, rule)


def _ngoac(block: Block, rule: dict[str, Any]) -> Iterator[TypoFinding]:
    """Ngoặc đơn mở mà không đóng, hoặc đóng mà chưa mở - chỉ đúng vị trí lẻ ra.

    Đếm số lượng hai bên rồi kết luận "lệch" là chưa đủ: người sửa vẫn phải tự dò
    cả đoạn xem cái nào thừa. Quét bằng ngăn xếp thì chỉ ra được đúng dấu lẻ.
    """
    cho_mo: list[int] = []
    le: list[int] = []
    for vi_tri, ky_tu in enumerate(block.text):
        if ky_tu == "(":
            cho_mo.append(vi_tri)
        elif ky_tu == ")":
            if cho_mo:
                cho_mo.pop()
            # Chỉ xét là số thứ tự khi KHÔNG có ngoặc nào đang mở. Nhờ vậy "1)"
            # đầu dòng được bỏ qua, còn ")" trong "(xem mục 1)" vẫn đóng đúng cái
            # ngoặc của nó thay vì bị nhầm thành số thứ tự rồi bỏ luôn.
            elif not _NUA_NGOAC.search(block.text[:vi_tri + 1]):
                le.append(vi_tri)
    for vi_tri in sorted(cho_mo + le):
        yield TypoFinding(
            block_id=block.id, type="punctuation",
            start=vi_tri, end=vi_tri + 1, quote=block.text[vi_tri],
            message=rule.get("message", "Ngoặc đơn không khớp")
            if block.text[vi_tri] == "(" else "Ngoặc đơn đóng mà chưa mở",
            severity=rule.get("severity", "warning"))


def soat_chinh_ta(blocks: list[Block], rules: dict[str, Any] | None = None) -> list[TypoFinding]:
    """Soát toàn bộ khối; trả về danh sách đã sắp theo đúng thứ tự đọc."""
    cfg = rules if rules is not None else load_chinh_ta()
    if not cfg:
        return []

    findings: list[TypoFinding] = []
    for block in blocks:
        if block.is_empty:
            continue
        findings += _quet_co_hoc(block, cfg.get("co_hoc") or {})

    findings.sort(key=lambda f: (f.block_id, f.start))
    return findings
