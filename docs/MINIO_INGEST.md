# Nạp file gốc từ MinIO

Không thêm bảng SQL. Quyền nạp vẫn là `quyen.QUYEN_KHO_GHI`.

- `POST /api/documents/upload`: multipart như cũ; lưu bản gốc trên MinIO rồi nạp từ file tạm.
- `POST /api/documents/ingest-minio`: JSON gồm `bucket`, `object_key`, `doc_type` (tùy chọn), `force_reingest` (mặc định false). Backend tải object, không upload lại.
- Payload Qdrant bổ sung `storage_bucket`, `storage_object_key`, `original_filename`, `checksum_sha256`, `file_size_bytes`.
- Cùng nội dung: bỏ qua embedding, bổ sung metadata còn thiếu. Nếu nguồn hiện có khác, trả 409, kể cả force; không thay nguồn tự động.
- File tạm được dọn cả khi lỗi; bản gốc MinIO giữ lại để thử lại. Luồng này không xóa uploads cũ.
- Force dùng cơ chế reingest hiện có: xóa chunk cũ trước khi ghi mới; chưa bảo đảm giữ chỉ mục cũ nếu ghi thất bại.

## Cấu hình

`MINIO_BUCKET` là bucket nhận upload mới. Bucket ngoài danh sách không được tải qua API nạp:

```dotenv
MINIO_INGEST_BUCKETS=["vanbantrunguong","vanbandiaphuong"]
```

Giữ credentials trong `.env`, không đưa vào lệnh hoặc báo cáo. Bucket upload phải có quyền tạo/ghi; bucket nguồn cần quyền đọc.

## Script

Nạp có chọn lọc trước bằng `--prefix`; không chạy cả kho khi kiểm thử:

```bash
.venv/bin/python scripts/ingest_minio.py --bucket vanbantrunguong \
  --prefix 'ten-object-can-nap.pdf' \
  --upload-url http://127.0.0.1:8082/api/documents/ingest-minio
```

Script đọc MINIO_ACCESS_KEY/SECRET_KEY và INGEST_API_HEADERS từ môi trường đã cấu hình. API kiểm tra quyền ERP như upload thường. Không chỉ định endpoint `/upload` cũ cho script mới.

## Giới hạn

Chưa có API mở bản gốc, backfill toàn kho, phân vùng nhóm/cá nhân hoặc tự đồng bộ. Payload cũ chỉ được bổ sung khi nạp lại đúng tài liệu. Một nội dung hiện giữ một liên kết nguồn chính; chưa quản lý nhiều bản nguồn hay object version MinIO.
