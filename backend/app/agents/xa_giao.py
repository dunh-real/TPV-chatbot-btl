"""Nhận ra lượt nói KHÔNG có gì để tra cứu, trước khi đem nó đi tra cứu.

"Hi" cũng được đem đi truy hồi như mọi câu khác, và truy hồi thì luôn trả về thứ
gì đó: điểm rerank của chunk đứng đầu chỉ cần vượt `rerank_score_threshold` là
đủ. Thế là một lời chào nhận về một câu trả lời có trích dẫn, dựng từ tài liệu
ngẫu nhiên nào đang nằm trong kho - kèm cả ô "Nguồn tham khảo" phía dưới.

Ngưỡng điểm không chữa được chỗ này. Điểm rerank đo mức GIỐNG NHAU giữa câu hỏi
và đoạn văn; "Hi" không giống đoạn nào, nhưng cũng không giống đoạn nào theo kiểu
đều đều, nên kéo ngưỡng lên đủ cao để gạt nó thì gạt luôn cả những câu hỏi thật
diễn đạt vụng. Việc cần làm là nhận ra lượt này KHÔNG PHẢI câu hỏi, và đó là
chuyện của câu chữ chứ không phải của điểm số.

Lọc ở đây phải chặt hơn mức cần thiết: bỏ sót một lời chào thì chỉ tốn một lượt
truy hồi vô ích, còn nhận nhầm một câu hỏi thật thành lời chào thì người dùng bị
từ chối tra cứu. Vì vậy chỉ nhận khi TOÀN BỘ câu đều là chữ xã giao - còn sót
một chữ mang nội dung là trả về đường tra cứu bình thường.
"""

from __future__ import annotations

import re

from app.core.text import khong_dau

__all__ = ["la_xa_giao"]

# Chữ xã giao thuần tuý, viết KHÔNG DẤU vì câu vào cũng được bỏ dấu trước khi so.
# Mỗi chữ ở đây phải vô nghĩa khi đứng một mình giữa một câu hỏi nghiệp vụ -
# "sao", "nao", "gi", "ai", "dau" tuyệt đối không được có mặt.
CHU_XA_GIAO: frozenset[str] = frozenset("""
hi hey hello helo hallo alo
xin chao tam biet bye byebye
good morning afternoon evening night
ban anh chi em minh oi a
ok oke okay okie oki kk uk uh um vang da
cam on thanks thank you tks thx
nhe nha nhieu lam roi
buoi sang chieu toi
test thu
""".split())

# Hỏi về chính trợ lý: không có gì để tra trong kho, nhưng cũng không được đáp
# bằng "không tìm thấy trong tài liệu". `NO_CONTEXT_SYSTEM` đã có sẵn lời đáp
# đúng cho nhóm này. So KHỚP CẢ CÂU chứ không so từng chữ, để không vơ nhầm.
CAU_HOI_VE_MINH: frozenset[str] = frozenset((
    "ban la ai",
    "ban ten gi",
    "ban ten la gi",
    "gioi thieu ve ban",
    "gioi thieu ban",
    "ban lam duoc gi",
    "ban lam duoc nhung gi",
    "ban co the lam gi",
    "ban co the lam nhung gi",
    "ban giup duoc gi",
    "ban giup toi duoc gi",
    "ban co the giup gi",
    "ban co the giup toi gi",
    "ban ho tro gi",
    "ban ho tro nhung gi",
    "ban co nhung chuc nang gi",
    "he thong lam duoc gi",
    "he thong nay lam duoc gi",
    "tro ly lam duoc gi",
    "toi co the hoi gi",
    "co the lam gi",
))

# Bỏ mọi thứ không phải chữ hoặc số: dấu câu, emoji, ký hiệu.
_KHONG_PHAI_CHU = re.compile(r"[^\w\s]", re.UNICODE)

# Dài hơn mức này thì có nội dung thật, dù mở đầu bằng lời chào.
SO_CHU_TOI_DA = 8


def la_xa_giao(cau: str) -> bool:
    """Lượt này chỉ là chào hỏi / cảm ơn / hỏi về chính trợ lý?"""
    phang = khong_dau(_KHONG_PHAI_CHU.sub(" ", cau or ""))
    if not phang:
        return True
    if phang in CAU_HOI_VE_MINH:
        return True

    chu = phang.split()
    if len(chu) > SO_CHU_TOI_DA:
        return False
    # Còn sót một chữ mang nội dung -> đây là yêu cầu thật, đi tra cứu.
    return all(c in CHU_XA_GIAO for c in chu)
