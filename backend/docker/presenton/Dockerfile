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

# Bản vá thứ hai: dạng cờ tắt suy luận mà Presenton gửi cho endpoint custom.
#
# `DISABLE_THINKING=true` làm Presenton gửi `{"enable_thinking": false}` ở MỨC
# CAO NHẤT của request body. OpenAI SDK chấp nhận, nhưng vLLM bỏ qua hoàn toàn -
# nó chỉ đọc cờ này trong `chat_template_kwargs`. Kết quả là với model suy luận
# (Qwen3), cờ không có tác dụng gì.
#
# Và hỏng không phải chỉ là chậm. Vì `generate_slide_content` ép `response_format`
# theo JSON schema, model không emit được khối <think>, nên nó đốt hết hạn mức
# vào lý luận rồi trả về JSON RỖNG:
#
#   thinking bật : 1740-2249 token, 11.9-14.6s -> {"bullets": [], "speaker_note": ""}
#   thinking tắt :   101-159 token,   0.7-1.1s -> 2 bullet, "Laptop Dell: 12"
#
# Presenton thấy rỗng thì `validate_schema=True` quay vòng retry (4 vòng × 3 lần
# parse = 12 lời gọi/slide), nên một slide tốn tới ~170s và vẫn ra slide trống.
# Đây chính là cái bẫy `app/services/llm.py` đã ghi lại: "trừ chung một hạn mức
# thì lời gọi 300 token sẽ bị cắt giữa phần lý luận và `content` trả về rỗng".
#
# Vá bằng sed chứ không COPY cả file: `llm_config.py` là file lớn, thay nguyên
# bản là mang theo một bản sao cũ của mọi thứ khác trong đó. `grep -q` ngay sau
# đó để build GÃY nếu upstream đổi câu lệnh - vá câm còn tệ hơn không vá.
RUN sed -i 's/return {"enable_thinking": False}/return {"chat_template_kwargs": {"enable_thinking": False}}/' \
      /app/servers/fastapi/utils/llm_config.py \
 && grep -q 'return {"chat_template_kwargs": {"enable_thinking": False}}' \
      /app/servers/fastapi/utils/llm_config.py
