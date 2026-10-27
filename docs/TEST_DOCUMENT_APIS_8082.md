# Test MinIO và tìm kiếm trên backend 8082

Bản phục vụ 8082: `/home/abc/yuu_workspace/chatbot/TPV-chatbot-btl`.
Đã đồng bộ mã chính ngày 27/10/2026, giữ `.env`; chưa restart tiến trình. Các route đã được xác nhận có trong OpenAPI bằng tiến trình kiểm tra riêng. Credentials MinIO của bản này đọc được bucket nguồn.

## 1. Trên server: cấu hình và restart

```bash
cd /home/abc/yuu_workspace/chatbot/TPV-chatbot-btl/backend
```

Để thử upload riêng, giữ collection test hiện tại:

```dotenv
QDRANT_COLLECTION=tpv_discovery_test_documents
```

Nếu cần nạp/đọc bản gốc trong hai bucket nguồn, bổ sung vào `.env` (không thay key/secret):

```dotenv
MINIO_INGEST_BUCKETS=["vanbantrunguong","vanbandiaphuong"]
```

Upload mới dùng MINIO_BUCKET, không cần thêm bucket này vào MINIO_INGEST_BUCKETS. Tìm trên kho chính cần đổi QDRANT_COLLECTION thành tpv_documents rồi restart; phần lớn tài liệu cũ có thể chưa liên kết bản gốc. Test dưới đây nên dùng collection test.

Restart đúng phiên thử (không đụng dịch vụ kien):

```bash
tmux kill-session -t tpv-discovery-test
tmux new-session -d -s tpv-discovery-test -c /home/abc/yuu_workspace/chatbot/TPV-chatbot-btl/backend '.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8082 >data/server.log 2>&1'
```

Nếu backend không lên, xem `tail -n 80 data/server.log`. Không chạy thêm uvicorn khác trên cùng cổng.

## 2. Trên Mac: tunnel và tài khoản

Giữ tunnel ERP đang có. Nếu chưa mở tunnel giao diện, chạy riêng terminal:

```bash
ssh -N -L 127.0.0.1:8082:127.0.0.1:8082 abc@100.105.11.124
```

Mở http://127.0.0.1:8082/docs để kiểm tra có ba API mới. Giao diện tìm kiếm hiện tại chưa được nối sang API mới. Các lệnh curl bên dưới gửi X-User-Id vì header danh tính đang được bật cho bản thử; đây không phải cơ chế đăng nhập production.

User 153 đã được xác minh quyền trong lần test ERP trước; có thể thay bằng tài khoản được cấp quyền phù hợp. Đọc cần Ai.AiChatbot; nạp cần Ai.ReportSummary.Create.

## 3. Trên Mac: upload mẫu

```bash
cat > /tmp/tpv-minio-test.md <<'DOC'
# Quy chế nghỉ phép - tài liệu thử
Cán bộ được nghỉ phép hằng năm theo quy định của đơn vị.
Đơn xin nghỉ phép cần được trưởng bộ phận phê duyệt.
DOC
curl -sS -H 'X-User-Id: 153' \
  -F 'file=@/tmp/tpv-minio-test.md' \
  http://127.0.0.1:8082/api/documents/upload
```

Kỳ vọng HTTP 200, có doc_id, chunk_count > 0. Bản gốc ở MINIO_BUCKET, chunk ở collection test. Lần đầu nạp model có thể chậm. Gửi lại cùng file: already_exists=true, không embedding lại.

## 4. Tìm, xem chi tiết, tải lại

```bash
curl -sS -H 'X-User-Id: 153' -H 'Content-Type: application/json' \
  -d '{"query":"nghỉ phép hằng năm","limit":5,"use_rerank":true}' \
  http://127.0.0.1:8082/api/documents/search
```

Kỳ vọng có tài liệu thử trong items, kèm snippets, original_available=true. returned_count là số tài liệu trả về, không phải số chunk/tổng toàn kho. Nếu đang debug, thử use_rerank=false để so sánh RRF.

Dán doc_id nhận từ bước upload:

```bash
DOC_ID='dan_doc_id_vao_day'
curl -sS -H 'X-User-Id: 153' \
  "http://127.0.0.1:8082/api/documents/$DOC_ID/detail"
curl --fail-with-body -sS -H 'X-User-Id: 153' \
  "http://127.0.0.1:8082/api/documents/$DOC_ID/original" \
  -o /tmp/tpv-minio-downloaded.md
shasum -a 256 /tmp/tpv-minio-test.md /tmp/tpv-minio-downloaded.md
```

Hai checksum phải giống nhau. Với PDF, original mặc định inline; ?download=true chuyển sang attachment. Khi gọi từ trình duyệt cần mang danh tính như các API khác; mở trực tiếp link trần có thể nhận 403 vì không có X-User-Id.

## 5. Nạp object nguồn có sẵn (tùy chọn)

Sau khi cấu hình MINIO_INGEST_BUCKETS và restart:

```bash
curl -sS -H 'X-User-Id: 153' -H 'Content-Type: application/json' \
  -d '{"bucket":"vanbantrunguong","object_key":"dan_object_key_day_du.pdf"}' \
  http://127.0.0.1:8082/api/documents/ingest-minio
```

Dùng đúng object key lấy từ Console, giữ cả prefix. Thử một file trước, chưa chạy cả bucket. Nguồn khác đã liên kết cùng nội dung trả 409; không ép ghi đè bằng force.

403 thường là thiếu quyền hoặc bucket chưa được phép; 404 là không có tài liệu/object/liên kết; 502 là lỗi MinIO/ingest; 503 là lỗi truy cập kho tìm kiếm. Xem log server để phân biệt.
