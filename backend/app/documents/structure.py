"""Mẫu nhận diện thành phần thể thức văn bản hành chính Việt Nam.

Chỉ còn phục vụ `app.rag.doc_card`: khi nạp tài liệu vào kho tri thức, phần thể
thức (số ký hiệu, ngày ban hành, người ký, nơi nhận) được tách thành một thẻ riêng
để hỏi lẻ "ai ký", "số mấy" thì tìm ra. Đó là chuyện của RAG.

Bước soát văn bản KHÔNG dùng gì ở đây: nó soát mọi loại tài liệu nên không đòi hỏi
thể thức hành chính - xem `app.documents.outline` và `app.documents.rules`.
"""

from __future__ import annotations

import re

# Mỗi thành phần: (regex nhận diện, nhóm trích giá trị nếu có)
_PATTERNS: dict[str, tuple[re.Pattern[str], int | None]] = {
    "quoc_hieu": (# "HÒA" và "HOÀ" đều hợp lệ - dấu có thể đặt trên O hoặc A.
        re.compile(r"CỘNG\s*H[OÒ][AÀ]\s*XÃ\s*HỘI\s*CHỦ\s*NGHĨA\s*VIỆT\s*NAM", re.I), None),
    "tieu_ngu": (re.compile(r"Độc\s*lập\s*[-–]\s*Tự\s*do\s*[-–]\s*Hạnh\s*phúc", re.I), None),
    "so_ky_hieu": (re.compile(r"^\s*Số\s*:\s*(\S+)", re.I | re.M), 1),
    "dia_danh_ngay": (
        re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I), 0),
    # "V/v Kiểm kê..." và "V/v: Kiểm kê..." đều gặp trong thực tế.
    "trich_yeu": (re.compile(r"(?:^|\s)V/v\s*:?\s*(.+)", re.I), 1),
    "kinh_gui": (re.compile(r"^\s*Kính\s+gửi\s*:\s*(.*)", re.I | re.M), 1),
    "noi_nhan": (re.compile(r"^\s*Nơi\s+nhận\s*:", re.I | re.M), None),
    # Chỗ dành sẵn; mẫu thật do build_chu_ky_pattern dựng ở cuối file.
    "chu_ky": (re.compile(r"(?!)"), 1),
    "ten_loai": (
        re.compile(r"^\s*(CÔNG\s*VĂN|QUYẾT\s*ĐỊNH|THÔNG\s*BÁO|BÁO\s*CÁO|TỜ\s*TRÌNH|"
                   r"KẾ\s*HOẠCH|BIÊN\s*BẢN|GIẤY\s*MỜI|CHỈ\s*THỊ|NGHỊ\s*QUYẾT)\s*$", re.I | re.M), 1),
}

# Chức danh người ký khác nhau theo từng nơi: doanh nghiệp ký "TỔNG GIÁM ĐỐC",
# đơn vị sự nghiệp ký "THỦ TRƯỞNG", trường học ký "HIỆU TRƯỞNG". Các dạng
# "TRƯỞNG ..." và ký thay/ký thừa lệnh được nhận bằng mẫu riêng ở dưới.
DEFAULT_CHUC_DANH_KY: tuple[str, ...] = (
    "GIÁM ĐỐC", "TỔNG GIÁM ĐỐC", "PHÓ GIÁM ĐỐC", "PHÓ TỔNG GIÁM ĐỐC",
    "CHÁNH VĂN PHÒNG", "PHÓ CHÁNH VĂN PHÒNG", "HIỆU TRƯỞNG", "CHỦ TỊCH",
    "THỦ TRƯỞNG", "CHỦ NHIỆM",
)

# Hình thức ký thay / ký thừa lệnh / quyền: cố định theo NĐ 30 nên không đưa ra
# cấu hình. "(đã ký)" là bản sao scan hoặc bản điện tử.
_KY_THAY_RE = r"TM\.\s*.+|KT\.\s*.+|TL\.\s*.+|Q\.\s*.+|\(đã\s*ký\)"

# "TRƯỞNG PHÒNG KỸ THUẬT", "TRƯỞNG BAN QUẢN LÝ", "THỦ TRƯỞNG ĐƠN VỊ"...
_TRUONG_RE = r"(?:[A-ZÀ-Ỹ]+\s+)?(?:THỦ\s*)?TRƯỞNG(?:\s+[A-ZÀ-Ỹ]+){0,3}"


def build_chu_ky_pattern(titles: list[str] | tuple[str, ...] | None = None) -> re.Pattern[str]:
    """Mẫu nhận dòng chức danh người ký, dựng từ danh sách chức danh cho trước."""
    names = [re.escape(t.strip()).replace(r"\ ", r"\s*") for t in (titles or DEFAULT_CHUC_DANH_KY) if t.strip()]
    # Chức danh dài đặt trước để "PHÓ GIÁM ĐỐC" không bị "GIÁM ĐỐC" ăn mất.
    names.sort(key=len, reverse=True)
    alternatives = "|".join([*names, _TRUONG_RE, _KY_THAY_RE])
    return re.compile(rf"^\s*({alternatives})\s*$", re.I | re.M)


_PATTERNS["chu_ky"] = (build_chu_ky_pattern(None), 1)
