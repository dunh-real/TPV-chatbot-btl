"""Chuẩn hoá chữ tiếng Việt để so khớp câu chữ người dùng gõ.

Gõ không dấu là chuyện thường ngày - máy không bật bộ gõ, gõ vội trên điện
thoại, hoặc chép lại từ một chỗ đã mất dấu. Mọi chỗ so khớp TỪ KHOÁ trong câu
của người dùng đều phải chịu được điều đó, nếu không thì cùng một yêu cầu lúc
chạy đúng lúc chạy sai tuỳ vào người gõ có bật Unikey hay không - và nó hỏng
lặng lẽ, vì câu vẫn được hiểu, chỉ hiểu sang thứ khác.

Đây KHÔNG phải nơi chuẩn hoá tên riêng để tra cứu: so tên đơn vị với danh mục
ERP có luật riêng (`app.tools.data._khoa_ten`) vì nó còn phải khớp cả mã.
"""

from __future__ import annotations

import unicodedata

__all__ = ["khong_dau"]


def khong_dau(text: str) -> str:
    """Dạng phẳng để so khớp: bỏ dấu, về chữ thường, gộp khoảng trắng thừa.

    "Tài sản", "TÀI SẢN" và "tai  san" cho ra cùng một chuỗi. `đ` phải thay tay
    vì NFD không tách nó thành chữ cái + dấu như các nguyên âm có dấu.
    """
    phang = unicodedata.normalize("NFD", str(text).lower())
    phang = "".join(c for c in phang if unicodedata.category(c) != "Mn")
    return " ".join(phang.replace("đ", "d").split())
