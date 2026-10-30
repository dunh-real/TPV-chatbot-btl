# UC33 — Gợi ý phân luồng văn bản

AI đề xuất đơn vị; BE/ERP quản lý quyết định cuối, phê duyệt và chuyển xử lý. Bản này lấy riêng ý tưởng/schema UC33 của `longhv` (`1884c71`), không merge lịch sử nhánh hoặc model SQLite cũ.

## API

Tất cả endpoint yêu cầu tài khoản ERP, tenant cụ thể và quyền `Ai.Routing`.

| Endpoint | Dùng để |
|---|---|
| `POST /api/document-routings/suggest` | JSON chứa `ExtractedText`, hoặc `document_id` / `FileUrl` đã đăng ký trên ERP |
| `POST /api/document-routings/suggest-file` | Multipart trường `file`; đính kèm và lưu MinIO, **không nạp kho tri thức** |
| `GET /api/document-routings/{id}` | Đọc gợi ý do AI tạo trực tiếp, giới hạn tenant/chủ sở hữu; Admin được đọc trong tenant |
| `POST /api/document-routings/{id}/rerun` | Tạo lượt gợi ý mới với danh mục hiện tại, giữ lượt cũ |

Ví dụ JSON: `{"ExtractedText":"Đề nghị Phòng Hành chính tổng hợp báo cáo nhân sự…"}`.

DOCX/PDF/TXT/MD tối đa 20 MB; nội dung tối đa 60.000 ký tự, quá giới hạn trả lỗi rõ ràng, không cắt lặng lẽ. Không nhận URL HTTP hoặc đường dẫn cục bộ tùy ý. Tham chiếu `minio://bucket/key` phải khớp `FileUrl` của tài liệu được đọc, hoặc `source_url`/`FileUrl`/`file_url` trong `AI_Tasks.InputJson` của chính tài khoản. Bucket phải nằm trong cấu hình được phép. Đọc tài liệu kho cần thêm `Ai.AiChatbot` và quyền nguồn do repository hiện tại áp dụng.

Danh mục lấy từ MSSQL: `Dms_WorkDepartment` và `AI_Categories` (`CategoryType=Field`). Lọc đúng tenant, chưa xóa và `IsActive` nếu bảng có cột này. Danh mục rỗng/thiếu mã/trùng mã trả 409; không tự dựng danh mục demo. Không nhận `Departments`/`Categories` từ client để thay danh mục ERP.

## Kết quả và lưu trữ

Giữ tên response của nhánh UC33: `Status`, `AiPrimaryUnitId`, `AiPrimaryUnitCode`, `AiCoordinatingUnitsJson`, `AiCoordinatingUnitCodes`, `AiReason`, `AiEvidenceJson`, `AiConfidence`, `Warnings`, cùng `ai_task_id`.

- Mã đơn vị được map sang ID; mã sai, trường thiếu, thiếu bằng chứng hoặc confidence thấp chuyển `NEEDS_REVIEW`.
- Chỉ giữ câu trích dẫn thực sự có trong nguồn. Không công nhận số trang/block do model tự đoán.
- Không có `X-Erp-Task-Id`: tạo `AI_Tasks`, `AI_DocumentRoutings`, phiên bản và nguồn trên MSSQL. Giữ text nguồn trong InputJson để chạy lại; không thêm bảng nghiệp vụ vào SQLite.
- Có `X-Erp-Task-Id`: kiểm tra dòng tác vụ thuộc đúng tenant/tài khoản; trả `ai_task_id` bằng ID đó, lưu phiên bản/nguồn gắn `OwnerType=Task`. Không tạo task/product mới, không ghi Status/ResultJson/RelatedEntityType/RelatedEntityId/TemplateId hoặc quyết định cuối. `RoutingId` để trống; BE lưu kết quả API vào sản phẩm của BE. AI không cung cấp approve/reject.
- Thời điểm UC33 tạo mới dùng giờ Việt Nam. Không chuyển đổi dữ liệu lịch sử.
- Chạy lại tạo lượt mới; không có cache chung kiểu `text-routing`.

## File thay đổi

Thêm `schemas/routing.py`, `services/document_routing.py`, `db/routing_repository.py`, `api/document_routings.py` và test UC33. Đăng ký router trong `main.py`; reflection thêm bảng có sẵn `AI_DocumentRoutings`; UC37 nhận loại tác vụ Routing. Giao diện `/ui/#routing` hỗ trợ file hoặc text, chỉ hiện gợi ý.

## Kiểm tra ngày 30/10/2026

- 125 test local qua: UC33, mapping DB, quyền, ERP discovery/groups/semantic và summary.
- 17 test UC33 qua trên server.
- Bản cô lập: `/home/abc/yuu_workspace/TPV-chatbot-btl/artifacts/uc33-staging`.
- Cấu hình runtime kiểm thử lấy từ `yuu_workspace/chatbot/.../backend/.env` vì bản yuu gốc đang trỏ DB trực tiếp, còn chatbot dùng tunnel `127.0.0.1:17679`. Không sửa `.env`.
- LLM thật đọc 18 đơn vị, 2 lĩnh vực và sinh `AI_SUGGESTED`; test MSSQL trực tiếp/detail/rerun, ERP không tạo trùng/không ghi trạng thái cuối và đọc MinIO thật đều qua. Các lượt kiểm tra DB sau lượt LLM thật dùng lại kết quả model để kiểm tra persistence. Toàn bộ ghi DB rollback; object MinIO test đã xóa.
- Chưa restart hoặc cập nhật dịch vụ đang chạy tại yuu/chatbot và Kiên. Chưa kiểm tra giao diện bằng trình duyệt; đã kiểm tra cú pháp JavaScript.
