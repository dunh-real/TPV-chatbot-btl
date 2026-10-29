# UC31 — Tóm tắt văn bản độc lập

Quyền: `Ai.Summarize`. Không cần quyền soát tài liệu hoặc quyền module tác vụ khi gọi API trực tiếp.

Upload file qua API hiện có, lấy `file_id`, sau đó gọi `POST /api/documents/summarize`:

```json
{
  "file_id": "upload:bao-cao.docx",
  "length": "medium",
  "structure": "bullets",
  "focus": "Kết quả đạt được và nhiệm vụ tiếp theo"
}
```

- `length`: `short` (100–150 từ), `medium` (250–350), `long` (500–700). Đây là khoảng mục tiêu; không ép sinh thêm nội dung để đủ chữ. Trả `word_count` và cảnh báo nếu ngoài khoảng.
- `structure`: `paragraphs` (đoạn văn), `bullets` (ý chính), `sections` (mục có tiêu đề).
- `focus`: không bắt buộc; trống thì tóm tắt các ý chính toàn văn.
- Hỗ trợ DOCX/PDF/TXT/MD, tối đa 20 MB và 120.000 ký tự sau đọc. OCR dùng cấu hình/parser hiện có; không đọc được nội dung thì báo lỗi.
- Tài liệu dài được xử lý theo lô rồi tổng hợp, không cắt bỏ phần cuối. `coverage.complete` chỉ đúng khi mọi lô đã xử lý thành công; không bảo đảm mọi chi tiết đều được đưa vào bản tóm tắt.
- Kết quả có `summary`, `sections`, `summary_refs`, `coverage`, `warnings`, `error`, `ai_task_id`. Trích dẫn chỉ được dùng mã có thật và số phải đối chiếu với đoạn được dẫn. Kiểm tra này không thay thế việc rà soát nghĩa nghiệp vụ.
- Nguồn giữ trên MinIO; tác vụ, kết quả và phiên bản lưu MSSQL qua UC37 hiện có. Không tạo bảng hoặc đổi chính sách nạp kho.
- Chạy nền: `POST /api/tasks`, `kind: "Summarize"`, đặt JSON trên vào `parameters`; cần thêm quyền module `Ai.AiTask`. Lần chạy lại giữ nguyên các lựa chọn.
- Tác vụ cũ dùng `run_analysis_workflow` vẫn có thể chạy lại theo luồng cũ. WF2 soát/trích nhiệm vụ không thay đổi.

Theo quy ước workflow hiện có, lỗi xử lý được trả trong `error` và `coverage.complete=false`; cần kiểm tra trước khi dùng kết quả. Lỗi schema request/quyền dùng mã HTTP tương ứng.

Kiểm thử: `pytest tests/test_summary.py tests/test_uc37.py tests/test_workflow2.py` — 105 bài đạt trên Mac và bản yuu chính. Ngày 29/10/2026 đã thử API bằng model/MSSQL/MinIO thật tại yuu: tác vụ 537 hoàn thành, lưu được phiên bản/kết quả và tải nguồn khớp file gốc. Kết quả có 4 mục, 17 trích dẫn, đọc đủ 4.985 ký tự; độ dài ngoài khoảng mục tiêu được báo trong `warnings`.
