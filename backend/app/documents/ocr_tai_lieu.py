"""Đọc một tài liệu thành Markdown, trang nào scan thì OCR trang đó.

KHÁC GÌ VỚI `app.rag.converter`

Converter phục vụ việc NẠP KHO: nó chỉ cần một khối Markdown liền mạch để đem đi
chunk, ai đọc trang nào không quan trọng. Màn OCR phục vụ NGƯỜI XEM: phải nói rõ
trang nào máy đọc thẳng từ lớp text, trang nào phải nhờ mô hình thị giác đọc ảnh,
và phải trả từng trang ngay khi xong thay vì bắt đợi cả tài liệu.

Hai đường dùng chung đúng những mảnh đáng dùng chung: `classify_pages` để phân
loại, `extract_digital_pages` để lấy chữ số hoá, `cleanup_markdown` để làm sạch.

Riêng `noi_dong_bi_ngat` và `dung_bo_cuc` thì CHỈ đường này gọi: một cái nối lại
những dòng bị ngắt giữa câu, một cái thêm marker hai cột / căn giữa cho phần đầu
văn bản hành chính. Cả hai đều là thứ để NHÌN. Đường nạp kho không gọi, vì marker
bố cục lẫn vào chữ đem đi nhúng vector chỉ làm loãng ngữ nghĩa của đoạn.

HAI CHẾ ĐỘ

    "auto"    trang digital lấy thẳng lớp text (nhanh, đúng từng dấu), chỉ trang
              scan mới đi qua mô hình. Mặc định.
    "tat_ca"  ép mọi trang qua mô hình. Dùng khi lớp text có mà hỏng - PDF xuất
              từ máy scan kèm lớp OCR cũ sai bét là trường hợp thường gặp nhất.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from app.documents.bo_cuc_hanh_chinh import dung_bo_cuc, noi_dong_bi_ngat
from app.rag.converter import cleanup_markdown, extract_digital_pages
from app.rag.ocr import IMAGE_SUFFIXES, get_ocr_service

logger = logging.getLogger(__name__)

# Chỉ nhận thứ thật sự có ảnh trang để đọc. .docx/.xlsx không có "trang scan" -
# nhận vào rồi trả về đúng chữ của chính nó thì đó là màn chuyển định dạng, không
# phải màn OCR, và người dùng sẽ tưởng mô hình vừa đọc hộ mình.
SUPPORTED_SUFFIXES = {".pdf"} | IMAGE_SUFFIXES

CHE_DO = ("auto", "tat_ca")

NGUON_OCR = "ocr"
NGUON_DIGITAL = "digital"
NGUON_TRONG = "trong"
NGUON_LOI = "loi"

# Ngăn giữa hai trang trong bản Markdown gộp. Dùng `---` chứ không phải dòng
# trắng: ranh giới trang là thông tin có thật của tài liệu, tải bản .md về mở ra
# vẫn phải thấy được trang kết thúc ở đâu.
NGAN_TRANG = "\n\n---\n\n"


@dataclass(slots=True)
class TrangKetQua:
    """Một trang đã đọc xong."""

    so_trang: int      # đánh số từ 1, đúng như người dùng đếm
    nguon: str         # NGUON_OCR | NGUON_DIGITAL | NGUON_TRONG
    markdown: str

    @property
    def so_ky_tu(self) -> int:
        return len(self.markdown)


@dataclass(slots=True)
class KeHoach:
    """Đã biết tài liệu có mấy trang, trang nào phải nhờ mô hình đọc."""

    so_trang: int
    trang_can_ocr: list[int]              # 0-based
    trang_digital: dict[int, str]         # 0-based -> Markdown đã làm sạch


class OcrError(ValueError):
    """Không đọc được tài liệu - lý do đã nằm trong thông điệp."""


def kiem_tra_dinh_dang(ten_file: str) -> str:
    """Trả về đuôi file đã chuẩn hoá, hoặc ném `OcrError` nếu không đọc được."""
    suffix = Path(ten_file or "").suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise OcrError(
            f"Màn OCR chỉ đọc được ảnh trang: {', '.join(sorted(SUPPORTED_SUFFIXES))}. "
            f"File .docx/.xlsx/.txt đã có sẵn chữ - nạp thẳng vào Kho tri thức."
        )
    return suffix


def lap_ke_hoach(path: Path, che_do: str = "auto") -> KeHoach:
    """Phân loại trang và lấy sẵn chữ của những trang không cần OCR.

    Chạy đồng bộ và có thể mất vài giây trên file lớn (đọc lớp text của cả tài
    liệu), nên nơi gọi phải đẩy sang thread.
    """
    if che_do not in CHE_DO:
        raise OcrError(f"Chế độ phải là một trong {list(CHE_DO)}")

    ocr = get_ocr_service()
    can_ocr = ocr.classify_pages(path)
    if not can_ocr:
        raise OcrError(f"Không đọc được trang nào trong {path.name} - file rỗng hoặc hỏng.")

    so_trang = len(can_ocr)

    if che_do == "tat_ca":
        # Ép hết qua mô hình: lớp text có thể có, nhưng người dùng đã nói là
        # không tin nó, nên đọc ra rồi lại vứt đi chỉ tổ tốn thời gian.
        return KeHoach(so_trang=so_trang, trang_can_ocr=list(range(so_trang)), trang_digital={})

    digital_raw = extract_digital_pages(path, so_trang)
    trang_digital: dict[int, str] = {}
    trang_can_ocr: list[int] = []
    for index in range(so_trang):
        if can_ocr[index]:
            trang_can_ocr.append(index)
        else:
            trang_digital[index] = cleanup_markdown(
                dung_bo_cuc(noi_dong_bi_ngat(digital_raw[index])))

    logger.info("OCR tài liệu %s (%s): %d/%d trang cần mô hình đọc",
                path.name, che_do, len(trang_can_ocr), so_trang)
    return KeHoach(so_trang=so_trang, trang_can_ocr=trang_can_ocr, trang_digital=trang_digital)


def doc_trang_ocr(path: Path, ke_hoach: KeHoach) -> Iterator[TrangKetQua]:
    """Sinh từng trang OCR ngay khi trang đó xong - KHÔNG theo thứ tự trang."""
    ocr = get_ocr_service()
    if not ke_hoach.trang_can_ocr:
        return
    if not ocr.enabled:
        raise OcrError(
            "OCR đang tắt (OCR_ENABLED=false) nên không đọc được trang scan. "
            "Bật lại trong .env của backend rồi khởi động lại."
        )

    for index, text in ocr.ocr_pages_stream(path, ke_hoach.trang_can_ocr):
        yield TrangKetQua(
            so_trang=index + 1,
            nguon=NGUON_OCR,
            markdown=cleanup_markdown(dung_bo_cuc(noi_dong_bi_ngat(text))),
        )


def trang_digital(ke_hoach: KeHoach) -> list[TrangKetQua]:
    """Những trang đọc được ngay, không phải chờ mô hình."""
    return [
        TrangKetQua(so_trang=index + 1,
                    nguon=NGUON_DIGITAL if text else NGUON_TRONG,
                    markdown=text)
        for index, text in sorted(ke_hoach.trang_digital.items())
    ]


def dung_day_du(ke_hoach: KeHoach, da_doc: dict[int, TrangKetQua]) -> list[TrangKetQua]:
    """Danh sách trang theo đúng thứ tự, trang nào thiếu thì vẫn có chỗ.

    Trang OCR lỗi hoặc quá hạn sẽ không có trong `da_doc`. Bỏ hẳn nó khỏi kết quả
    là làm lệch số trang của mọi trang sau - người xem đọc "Trang 7" mà thực ra
    đang xem trang 8. Nên trang thiếu vẫn giữ chỗ, chỉ là rỗng.

    Trang trắng và trang đọc hỏng đều rỗng nhưng KHÁC NHAU với người xem: một bên
    là tài liệu vốn thế, một bên là chỗ thiếu chữ cần đọc lại. Nên đánh dấu riêng.
    """
    can_ocr = {i + 1 for i in ke_hoach.trang_can_ocr}
    return [
        da_doc.get(so, TrangKetQua(
            so_trang=so,
            nguon=NGUON_LOI if so in can_ocr else NGUON_TRONG,
            markdown="",
        ))
        for so in range(1, ke_hoach.so_trang + 1)
    ]


def ghep_markdown(trang: list[TrangKetQua]) -> str:
    """Gộp các trang thành một bản Markdown để sao chép / tải về."""
    phan = [t.markdown.strip() for t in trang]
    if len(phan) <= 1:
        return phan[0] if phan else ""
    return NGAN_TRANG.join(phan).strip()
