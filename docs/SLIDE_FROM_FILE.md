# Tạo slide từ tài liệu

Giữ API `POST /api/presentations/create`, quyền `Ai.Slide.Create` và cơ chế lưu MinIO hiện tại. Không thay DB, prefix MinIO, chính sách nạp kho hay giao diện.

## Gọi API

Upload bằng `POST /api/agent/upload`, lấy `file_id`, sau đó:

```json
{
  "request": "Tóm lược kết quả, hạn chế và nhiệm vụ tiếp theo",
  "nguon": "tai_lieu",
  "file_id": "upload:bao-cao.docx",
  "inputs": {}
}
```

Không truyền `nguon` thì giữ luồng ERP cũ. Agent tổng cũng chuyển file đính kèm sang nhánh tài liệu khi chọn nghiệp vụ tạo slide. Lưu ý endpoint upload đính kèm vẫn có bước nạp kho nền như trước; lượt sửa này không thay chính sách đó.

Để chạy nền, gọi `POST /api/tasks` với `kind: "Slide"` và đặt body trên vào `parameters`. File nguồn được lưu MinIO và phục hồi cho lần chạy lại; kết quả xem/tải qua UC37 như cũ.

## Xử lý

1. Resolve file upload, đọc DOCX/PDF bằng parser hiện có; giữ mã khối, chữ và trang PDF. DOCX không tự suy đoán số trang.
2. Đếm token bằng `/tokenize` trên server vLLM của model cấu hình trong `LLM_MODEL`. Endpoint này cần hoạt động; không ước lượng ký tự thay token.
3. LLM trả JSON nội dung và nguồn cho từng ý; slide chữ hoặc bảng, không tự tạo biểu đồ hay tính tỷ lệ. Kiểm schema, mã nguồn và số liệu với đoạn được dẫn; tối đa một lần yêu cầu sửa.
4. Chuyển thành `SlidePlan`, dùng layout Presenton hiện có; dẫn nguồn trong ghi chú và JSON kết quả. Nhánh này không truy vấn số liệu ERP.
5. Presenton xuất PPTX; nếu lỗi dùng renderer local với cùng nội dung. Kiểm số slide thực tế, lưu output/nguồn/phiên bản bằng luồng hiện có.

Nguồn số giúp kiểm tra tự động nhưng không bảo đảm phát hiện mọi sai nghĩa của LLM. Người dùng vẫn cần rà soát nội dung trước khi sử dụng.

## Giới hạn

```dotenv
SLIDE_MAX_SLIDES=15
SLIDE_DOCUMENT_MAX_MB=20
SLIDE_DOCUMENT_MAX_TOKENS=32000
```

- Một file DOCX hoặc PDF có lớp chữ. PDF tối đa 100 trang; file có trang scan không có chữ cần OCR trước.
- DOCX giới hạn tổng dung lượng giải nén 100 MB. Ảnh không được phân tích ở bản đầu.
- Tài liệu vượt ngân sách bị từ chối có thông báo; không âm thầm cắt nội dung.
- Tối đa 15 slide tính cả bìa. LLM được yêu cầu tóm lược trong ngân sách này; không sinh đủ 15 nếu không cần.
- Giới hạn được kiểm tra cả kế hoạch direct, fallback local và file sau xuất. Luồng ERP giữ dữ liệu đầy đủ; nếu cần quá 15 slide thì báo thu hẹp phạm vi thay vì cắt bảng hay bỏ kết luận.
- API trả `sources`, `document_outline`, `brief`, `slide_count`, `validation`, `download_url`, `ai_task_id`. Theo quy ước workflow hiện có, lỗi xử lý có thể trả trong trường `error`; cần kiểm tra trường này trước khi dùng kết quả.

## File thay đổi chính

- `schemas/presentations.py`, `api/presentations.py`: đầu vào tương thích API cũ, đầu ra có nguồn/outline.
- `agents/graph.py`: chọn nhánh file hoặc ERP.
- `agents/nodes/presentation_document.py`: file mới duy nhất cho nghiệp vụ đọc, giới hạn, lập nội dung và kiểm nguồn.
- `documents/slide_deck.py`: chuyển nội dung tài liệu thành layout hiện có.
- `agents/nodes/presentation.py`: dùng chung renderer, fallback cho tài liệu, kiểm giới hạn slide.
- `services/task_queue.py`, `services/tasks.py`: nhận diện slide từ file để lưu/khôi phục nguồn và chạy lại.
- `core/config.py`, `.env.example`: ba giới hạn cấu hình; không sửa `.env` thật.

Kiểm thử: `pytest tests/test_slide_document.py tests/test_workflow5.py tests/test_slide_deck.py tests/test_uc37.py`. Các dịch vụ ngoài được giả lập trong test; chưa thay thế kiểm thử end-to-end trên server với LLM/Presenton/MinIO thật.
