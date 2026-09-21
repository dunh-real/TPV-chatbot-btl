"""Dựng lại bố cục phần đầu văn bản hành chính từ chữ OCR tuyến tính.

VẤN ĐỀ

OCR trả về một dòng chữ nối tiếp nhau. Nhưng phần đầu văn bản hành chính Việt Nam
(Nghị định 30/2020/NĐ-CP) là bố cục HAI CỘT cố định:

    NHNo&PTNT TỈNH QUẢNG NAM          CỘNG HOÀ XÃ HỘI CHỦ NGHĨA VIỆT NAM
    Chi nhánh Tam Đàn                      Độc lập - Tự do - Hạnh phúc
    Số: 15/NHNo-TD                    Tam Đàn, ngày 19 tháng 01 năm 2009

                              ĐƠN KHỞI KIỆN

Đọc tuyến tính thì hai cột chồng thành sáu dòng rời, tên văn bản mất căn giữa, và
người xem không còn nhận ra đây là một tờ công văn.

VÌ SAO DỰNG LẠI ĐƯỢC MÀ KHÔNG CẦN TOẠ ĐỘ

Bố cục này được quy định chứ không tự do, nên nhận ra nó bằng NỘI DUNG là đủ:
quốc hiệu là một chuỗi cố định, tiêu ngữ là một chuỗi cố định, "Số: ..." và
"địa danh, ngày ... tháng ... năm ..." có hình dạng cố định. Mô hình đã tách sẵn
các khối bằng dòng trống đúng theo khối thị giác trên trang, nên việc còn lại chỉ
là xếp khối nào sang cột nào.

Chỉ dựng lại khi NHẬN CHẮC: thấy đủ quốc hiệu. Không thấy thì trả nguyên văn bản
về, vì đoán bố cục sai còn tệ hơn để tuyến tính.

NỐI LẠI DÒNG BỊ NGẮT

Mô hình lúc thì giữ nguyên chỗ xuống dòng của bản gốc, lúc thì nối cả đoạn thành
một dòng dài - không đoán trước được, và khác nhau ngay giữa hai trang của cùng
một tệp. Trang giữ ngắt dòng gốc thì mỗi dòng chỉ ~38 ký tự, đổ vào khung rộng
thành một dải chữ hẹp dính bên trái, bỏ trống hai phần ba trang. Nối lại các dòng
bị ngắt giữa câu làm đoạn văn chảy đúng theo bề ngang khung, và quan trọng hơn:
mọi trang trông như nhau bất kể mô hình ngắt dòng kiểu gì.

CHỈ DÙNG CHO MÀN XEM

Đường nạp kho KHÔNG đi qua đây. Marker bố cục là thứ để nhìn; nhét vào chữ đem đi
nhúng vector chỉ làm loãng ngữ nghĩa của đoạn.
"""

from __future__ import annotations

import re
import unicodedata

# Marker bố cục. Dùng `:::` kiểu directive vì nó hiếm trong văn bản hành chính,
# và khi người dùng tải bản .md về mở bằng trình soạn thảo thường thì vẫn đọc
# được nội dung, chỉ là không thấy hai cột.
MO_TIEU_NGU = "::: tieu-ngu"
MO_GIUA = "::: giua"
MO_DAU = "::: dau"
DONG = ":::"
# Ngăn giữa hai cột trên cùng một dòng. Ba gạch đứng: một hoặc hai gạch còn gặp
# trong bảng biểu, ba thì không.
NGAN_COT = "|||"

_QUOC_HIEU = "cong hoa xa hoi chu nghia viet nam"
_TIEU_NGU = "doc lap"

# "Tam Đàn, ngày 19 tháng 01 năm 2009" - địa danh có thể thiếu, ngày có thể bỏ trống.
_DIA_DANH_NGAY = re.compile(
    r"^\s*(?:[^,\n]{1,40},\s*)?ngày\s+.{1,4}\s*tháng\s+.{1,4}\s*năm\s+.{1,6}\s*$",
    re.IGNORECASE,
)
_SO_HIEU = re.compile(r"^\s*số\s*:", re.IGNORECASE)
_NGAY = re.compile(r"^\s*ngày\s*:", re.IGNORECASE)

# Tên loại văn bản: dòng viết hoa đứng một mình, mở đầu bằng một trong các từ này.
_LOAI_VAN_BAN = re.compile(
    r"^(ĐƠN|BẢN ÁN|BẢN|QUYẾT ĐỊNH|BIÊN BẢN|CÔNG VĂN|THÔNG BÁO|TỜ TRÌNH|BÁO CÁO"
    r"|KẾ HOẠCH|NGHỊ QUYẾT|CHỈ THỊ|GIẤY|HỢP ĐỒNG|CÁO TRẠNG|KẾT LUẬN|LỆNH"
    r"|NHÂN DANH)\b",
)


def _khong_dau(text: str) -> str:
    """Bỏ dấu tiếng Việt để so khớp chuỗi cố định bất kể OCR đọc sai dấu."""
    bo_dau = unicodedata.normalize("NFD", text.lower())
    bo_dau = "".join(c for c in bo_dau if unicodedata.category(c) != "Mn")
    return bo_dau.replace("đ", "d").strip()


def _la_quoc_hieu(dong: str) -> bool:
    return _QUOC_HIEU in _khong_dau(dong)


def _la_tieu_ngu(dong: str) -> bool:
    khong_dau = _khong_dau(dong)
    return khong_dau.startswith(_TIEU_NGU) and "hanh phuc" in khong_dau


_KET_CAU = (".", ":", ";", "!", "?", "…")
_DAU_DANH_SACH = re.compile(r"^\s*(?:[-+•–*]\s|\d+[.)]\s|#|\||:::)")
# "Kính gửi: - Toà án nhân dân tỉnh Quảng Nam;" - nhãn rồi gạch đầu dòng đầu tiên
# nằm chung một dòng, các mục sau xuống dòng riêng.
_NHAN_KEM_MUC = re.compile(r"^(?P<nhan>[^:]{1,40}:)\s*-\s*(?P<muc>.+)$")


def _noi_duoc(dong: str, sau: str, dai_nhat: int) -> bool:
    """`sau` có phải phần đuôi của `dong` bị ngắt xuống không?

    Ba điều kiện cùng lúc, cố ý chặt: thà bỏ sót một chỗ đáng nối (chỉ thừa một
    lần xuống dòng) còn hơn nối nhầm hai dòng vốn tách nhau (dính "Địa chỉ" vào
    "Người đại diện" thì đọc ra nghĩa khác).
    """
    if not dong or not sau:
        return False
    if dong.endswith(_KET_CAU):
        return False
    if _DAU_DANH_SACH.match(sau):
        return False
    # Dòng bị ngắt do hết chỗ thì phải gần chạm mép phải của khối.
    if len(dong) < dai_nhat * 0.6:
        return False
    dau = sau.lstrip()[:1]
    # Chữ thường hoặc mở ngoặc = câu còn dở. Chữ hoa có thể là tên riêng giữa câu,
    # nhưng cũng có thể là một mục mới - không phân biệt được nên không nối.
    return dau == "(" or (dau.isalpha() and dau.islower())


def _noi_dong_trong_khoi(cac_dong: list[str]) -> list[str]:
    if len(cac_dong) < 2:
        return cac_dong
    dai_nhat = max(len(d) for d in cac_dong)
    ra: list[str] = [cac_dong[0]]
    for dong in cac_dong[1:]:
        if _noi_duoc(ra[-1], dong, dai_nhat):
            ra[-1] = f"{ra[-1]} {dong.lstrip()}"
        else:
            ra.append(dong)
    return ra


def _tach_nhan_khoi_muc(cac_dong: list[str]) -> list[str]:
    """Tách "Kính gửi: - A;" thành nhãn riêng + gạch đầu dòng.

    Để nguyên thì mục đầu nằm dính sau nhãn còn mục thứ hai thành bullet - hai
    mục cùng một danh sách mà hiện ra hai kiểu khác nhau.
    """
    ra: list[str] = []
    for index, dong in enumerate(cac_dong):
        khop = _NHAN_KEM_MUC.match(dong)
        # Chỉ tách khi ngay dưới còn mục khác, nếu không thì đây là câu bình thường.
        co_muc_sau = index + 1 < len(cac_dong) and cac_dong[index + 1].lstrip().startswith("-")
        if khop and co_muc_sau:
            ra.append(khop.group("nhan"))
            ra.append(f"- {khop.group('muc')}")
        else:
            ra.append(dong)
    return ra


def noi_dong_bi_ngat(text: str) -> str:
    """Nối lại các dòng bị ngắt giữa câu, giữ nguyên ranh giới khối."""
    if not text or not text.strip():
        return text
    khoi = _tach_khoi(text)
    return "\n\n".join(
        "\n".join(_tach_nhan_khoi_muc(_noi_dong_trong_khoi(k))) for k in khoi
    )


def _tach_khoi(text: str) -> list[list[str]]:
    """Cắt văn bản thành các khối ngăn nhau bằng dòng trống."""
    khoi: list[list[str]] = []
    hien_tai: list[str] = []
    for dong in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if dong.strip():
            hien_tai.append(dong.strip())
        elif hien_tai:
            khoi.append(hien_tai)
            hien_tai = []
    if hien_tai:
        khoi.append(hien_tai)
    return khoi


def _chi_so_khoi_quoc_hieu(khoi: list[list[str]]) -> int:
    """Vị trí khối chứa quốc hiệu, hoặc -1. Chỉ tìm trong phần đầu trang."""
    for index, cac_dong in enumerate(khoi[:6]):
        if any(_la_quoc_hieu(dong) for dong in cac_dong):
            return index
    return -1


def _la_con_dau(cac_dong: list[str]) -> bool:
    """Con dấu "CÔNG VĂN ĐẾN" đóng ở góc trang: có cả "Số:" và "Ngày:".

    Khác khối cơ quan ban hành ở chỗ có thêm dòng "Ngày:" - cơ quan ban hành chỉ
    ghi số hiệu, ngày tháng của nó nằm bên cột quốc hiệu. Nhận ra để đóng khung
    riêng, chứ để trôi vào văn bản thì mấy dòng chữ trong dấu trông như tiêu đề
    của tài liệu.
    """
    if len(cac_dong) > 6:
        return False
    return (any(_SO_HIEU.match(d) for d in cac_dong)
            and any(_NGAY.match(d) for d in cac_dong))


def _la_khoi_co_quan(cac_dong: list[str]) -> bool:
    """Khối tên cơ quan ban hành: có "Số: ...", không phải quốc hiệu, không phải dấu.

    Phải loại con dấu ra: dấu cũng có "Số:" nên tài liệu nào chỉ đóng dấu mà
    không ghi cơ quan ban hành sẽ bị kéo nhầm cái dấu sang làm cột trái của tiêu
    ngữ. Dấu khác ở chỗ có thêm dòng "Ngày:" - ngày của cơ quan ban hành nằm bên
    cột phải, dưới tiêu ngữ, chứ không nằm cùng khối với số hiệu.
    """
    if any(_la_quoc_hieu(d) or _la_tieu_ngu(d) for d in cac_dong):
        return False
    if _la_con_dau(cac_dong) or len(cac_dong) > 4:
        return False
    if any(_SO_HIEU.match(d) for d in cac_dong):
        return True
    # Toà án ghi số hiệu ở khối riêng bên dưới ("Bản án số: 13/2022/HC-ST"), nên
    # khối cơ quan ban hành của bản án chỉ còn mỗi tên: "TÒA ÁN NHÂN DÂN / TỈNH
    # QUẢNG NGÃI". Nhận thêm hình dạng đó - tên cơ quan luôn viết hoa toàn bộ,
    # và đứng sát ngay trên quốc hiệu thì khó là thứ gì khác.
    #
    # Chỉ soi DÒNG ĐẦU: tên đơn vị cấp dưới viết thường ("Phòng Đăng ký kinh
    # doanh", "Chi nhánh Tam Đàn") và luôn nằm dưới tên cơ quan. Bắt mọi dòng
    # phải viết hoa thì hụt đúng những khối ba dòng hay gặp nhất.
    if any(len(d) > 60 for d in cac_dong):
        return False
    dau = cac_dong[0]
    return bool(dau) and dau == dau.upper() and any(c.isalpha() for c in dau)


def _ghep_hai_cot(trai: list[str], phai: list[str]) -> list[str]:
    """Xếp hai khối thành các dòng `trái ||| phải`, bên ngắn hơn để trống."""
    cao = max(len(trai), len(phai))
    ra = [MO_TIEU_NGU]
    for i in range(cao):
        o_trai = trai[i] if i < len(trai) else ""
        o_phai = phai[i] if i < len(phai) else ""
        ra.append(f"{o_trai} {NGAN_COT} {o_phai}".strip())
    ra.append(DONG)
    return ra


def _la_ten_van_ban(cac_dong: list[str]) -> bool:
    """Khối chỉ gồm tên loại văn bản (và phụ đề), đứng căn giữa trên trang."""
    if not cac_dong or len(cac_dong) > 4:
        return False
    dau = cac_dong[0].strip()
    if len(dau) > 90 or not _LOAI_VAN_BAN.match(dau):
        return False
    # Phải viết hoa toàn bộ: "ĐƠN KHỞI KIỆN" là tên văn bản, còn "Đơn này do ông
    # Huỳnh Chẳn nộp ngày..." chỉ là một câu mở đầu bằng cùng một từ.
    return dau == dau.upper()


def dung_bo_cuc(text: str) -> str:
    """Thêm marker bố cục vào phần đầu một trang văn bản hành chính.

    Trả về nguyên `text` nếu không nhận chắc đây là văn bản hành chính - im lặng
    không làm gì là hành vi đúng, vì marker sai làm hỏng trang nhiều hơn là thiếu
    marker.
    """
    if not text or not text.strip():
        return text

    khoi = _tach_khoi(text)
    vi_tri_quoc_hieu = _chi_so_khoi_quoc_hieu(khoi)
    # Không thấy quốc hiệu thì không nhận chắc đây là văn bản hành chính - trả
    # nguyên về. Cố đoán tiếp bằng từ khoá chức danh ("TRƯỞNG PHÒNG", "CHỦ TỊCH")
    # thì mỗi loại tài liệu mới lại phải thêm từ khoá, mà tài liệu thì có thể là
    # bất cứ thứ gì. Căn lề chung cho mọi tài liệu là việc của typography, không
    # phải của danh sách từ khoá.
    co_tieu_ngu = vi_tri_quoc_hieu >= 0
    if not co_tieu_ngu:
        return text

    # Cột trái là khối cơ quan ban hành đứng ngay trước quốc hiệu. Không có thì
    # để trống - vài văn bản (đơn của công dân) vốn không có cơ quan ban hành.
    vi_tri_co_quan = -1
    if co_tieu_ngu and vi_tri_quoc_hieu > 0 and _la_khoi_co_quan(khoi[vi_tri_quoc_hieu - 1]):
        vi_tri_co_quan = vi_tri_quoc_hieu - 1

    ra: list[str] = []
    for index, cac_dong in enumerate(khoi):
        if index == vi_tri_co_quan:
            continue                        # đã gộp vào khối hai cột bên dưới
        if co_tieu_ngu and index == vi_tri_quoc_hieu:
            trai = khoi[vi_tri_co_quan] if vi_tri_co_quan >= 0 else []
            ra.extend(_ghep_hai_cot(trai, khoi[vi_tri_quoc_hieu]))
        elif co_tieu_ngu and index < vi_tri_quoc_hieu and _la_con_dau(cac_dong):
            ra.extend([MO_DAU, *cac_dong, DONG])
        # Tên văn bản chỉ đoán khi đã chắc đây là văn bản hành chính: "BÁO CÁO
        # QUÝ 3" trong một tệp bất kỳ là tiêu đề mục, không phải tên văn bản.
        elif co_tieu_ngu and _la_ten_van_ban(cac_dong):
            ra.extend([MO_GIUA, *cac_dong, DONG])
        else:
            ra.extend(cac_dong)
        ra.append("")

    return "\n".join(ra).strip()
