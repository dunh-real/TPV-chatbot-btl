# Presenton kèm một bản vá duy nhất.
#
# Nhánh `slides_markdown` - thứ cho phép MÌNH soạn nội dung từng slide còn
# Presenton chỉ render - chết ngay ở image gốc:
#
#   LLM API error: PresentationLayoutModel.to_string()
#   got an unexpected keyword argument 'with_schema'
#
# Đó là lỗi của chính image (ngày 27/05/2026), không phải do cách gọi: hàm
# `to_string` thiếu tham số mà nhánh kia truyền vào. Vá đúng một file, giữ
# nguyên mọi thứ còn lại.
#
# Khi nâng image gốc: thử BỎ dòng COPY trước. Upstream sửa rồi thì bản vá này
# thành thừa, và giữ lại một bản sao cũ của file đè lên bản mới là cách tự tạo
# ra lỗi khó tìm.
FROM ghcr.io/presenton/presenton:latest

COPY presentation_layout.py /app/servers/fastapi/templates/presentation_layout.py
