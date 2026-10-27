# Tìm kiếm tài liệu, chi tiết và bản gốc

Cả ba API dùng `QUYEN_KHO_DOC` (`Ai.AiChatbot`) từ cơ chế quyền hiện tại. Không thêm bảng SQL, không thay API chat/search debug.

## Tìm kiếm

`POST /api/documents/search`

```json
{"query":"nghỉ phép hằng năm","limit":10,"use_rerank":true,"filters":{"doc_ids":[],"sources":[],"doc_types":["pdf"]}}
```

`doc_types` hiện là định dạng file, không phải phân loại quyết định/thông tư. API tái sử dụng hybrid/RRF/reranker, gom các chunk theo doc_id, lấy điểm cao nhất và tối đa 3 đoạn/tài liệu. Chỉ tìm các chunk không đánh dấu `latest=false`.

Response có `items`, `returned_count`, `candidate_documents`, `score_kind`. Candidate_documents là số tài liệu trong tập ứng viên truy hồi bị giới hạn, không phải tổng toàn kho; limit không bảo đảm đủ số tài liệu. Khi tắt rerank, score là RRF, không phải xác suất.

Mỗi item gồm metadata, score, snippets (text/section/page), detail_url và original_url. Trường original_available cho biết có liên kết nguồn trong metadata; không gọi MinIO để kiểm tra từng object trong kết quả. Object mất hoặc bucket chưa được phép vẫn có thể làm tải thất bại.

## Chi tiết

`GET /api/documents/{doc_id}/detail`

Đọc metadata và đếm chunk hiện hành, không kéo toàn văn/vector. Nếu tầng pháp luật đang bật và có dữ liệu, bổ sung số hiệu, loại, ngày hiệu lực. Không có hồ sơ phiên bản mới; API pháp luật hiện tại vẫn dùng riêng. Không trả bucket/object_key hoặc đường dẫn local.

## File gốc

`GET /api/documents/{doc_id}/original?download=false`

Backend tự tra bucket/object_key từ Qdrant, kiểm tra bucket được cấu hình cho phép rồi stream MinIO, không tạo file local. PDF mặc định inline; thêm `?download=true` để tải. Các định dạng khác trả attachment. Giao diện có thể gắn `#page=4` cho PDF từ trường page của snippet; chưa tô sáng hoặc tạo PDF từ DOCX. Không hỗ trợ HTTP Range trong bản này.

- 403: thiếu quyền hoặc bucket không nằm trong MINIO_BUCKET/MINIO_INGEST_BUCKETS.
- 404: tài liệu không tồn tại, chưa liên kết bản gốc hoặc object đã mất.
- 502: lỗi đọc MinIO.
- 503: lỗi truy cập chỉ mục/metadata.

Không đoán file gốc từ tên của tài liệu cũ. Luồng MinIO được đóng cả khi hoàn tất, đọc lỗi hoặc client ngắt kết nối. Nếu lỗi xảy ra sau khi đã gửi header, kết nối tải sẽ kết thúc thay vì đổi mã HTTP.

## Môi trường thử

Đồng bộ và test tại `/home/abc/yuu_workspace/TPV-chatbot-btl`. Backend 8082 trong thư mục có `/chatbot/` không tự nhận các thay đổi này; chưa restart hoặc chuyển bản phục vụ giao diện.
