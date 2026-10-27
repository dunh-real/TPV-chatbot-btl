# Liên quan, lưu danh mục và xuất CSV

Tất cả endpoint dùng QUYEN_KHO_DOC (Ai.AiChatbot) hiện có. Không thêm bảng SQL.

## Tài liệu liên quan

`GET /api/documents/{doc_id}/related?limit=5` (1–20 mỗi nhóm).

- same_topic: truy hồi ngữ nghĩa từ tiêu đề + đoạn đại diện (tối đa 1.500 ký tự), loại doc_id đang xem trước khi tìm. Điểm/snippet là kết quả tìm kiếm, không phải kết luận quan hệ pháp lý.
- same_number: khớp số hiệu đã đăng ký trong bảng LegalDocument; không đoán từ tên file.
- legal_relations: lấy LegalAction có status=applied, trả đúng relation/op và direction incoming/outgoing. Không suy đoán thay thế; het_hieu_luc chỉ được gọi là hết hiệu lực. Quarantined/pending không được coi là quan hệ đã xác nhận.
- legal_status: disabled, not_registered, unavailable hoặc ok. Hai nhóm pháp lý rỗng khi tầng pháp luật tắt/chưa có dữ liệu. Nhóm cùng chủ đề vẫn có thể trả kết quả.

Đọc metadata bằng ID có index; không quét toàn kho để so sánh tên. Quan hệ pháp luật đọc từ SQL app hiện có, không ghi DB/ERP. Chỉ trả tài liệu còn có metadata hiện hành trong Qdrant.

## Danh mục cá nhân

`POST /api/documents/saved`

```json
{"name":"Hồ sơ nghỉ phép","doc_ids":["doc_id_1","doc_id_2"]}
```

Response 201 có saved_id, name, doc_ids (bỏ trùng, giữ thứ tự), created_at UTC. Mỗi danh mục tối đa 200 ID. Có thể lưu ID chưa tồn tại; mở lại sẽ báo missing_doc_ids.

- GET /api/documents/saved: danh mục của tài khoản/tenant hiện tại.
- GET /api/documents/saved/{saved_id}: đọc metadata hiện tại, items và missing_doc_ids.
- DELETE /api/documents/saved/{saved_id}: xóa danh mục của chính người gọi, trả 204, không xóa tài liệu.

JSON nằm tại SAVED_DOCUMENTS_DIR (mặc định ./data/saved_documents)/<tenant hoặc common>/<SHA256 user_id>/<saved_id>.json. Hash user để tránh dùng chuỗi danh tính làm đường dẫn. Chưa có UI hoặc chia sẻ danh mục. Danh sách là tập ID cố định, không phải truy vấn động, không phải snapshot phiên bản nội dung.

Ghi qua file tạm và os.replace; quyền file 0600. Đọc danh mục không tự tạo thư mục. Truy cập danh mục của người khác trả 404 giống không tồn tại. Cần sao lưu thư mục này nếu muốn giữ danh mục qua việc chuyển máy/container.

## Xuất

`POST /api/documents/export`

```json
{"doc_ids":["doc_id_1","doc_id_2"]}
```

Trả attachment documents.csv, UTF-8 BOM; tối đa 200 ID. Metadata đọc từ server. Cột gồm ID, tên, số hiệu, loại, ngày hiệu lực, nguồn, URL API bản gốc và trạng thái available/missing. Ô có nguy cơ công thức Excel được thêm dấu nháy đơn. Không tạo file tồn trong data/output.

URL bản gốc là đường dẫn tương đối tới backend và vẫn yêu cầu danh tính/quyền; CSV không chứa token hoặc URL MinIO tạm. Quyền hiện tại là quyền chức năng kho, chưa bổ sung ACL từng tài liệu.

## Thử nghiệm

Code sửa trên Mac, áp dụng thử ở /home/abc/yuu_workspace/TPV-chatbot-btl. Chưa đồng bộ bước 6 sang bản /home/abc/yuu_workspace/chatbot/TPV-chatbot-btl, chưa restart 8082 hay chuyển sang kien_workspace.
