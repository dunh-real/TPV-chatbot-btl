# Nạp MinIO thông qua API upload

Chạy từ thư mục `backend`, trên máy truy cập được cả MinIO và API chatbot.
Script không tự đọc `.env`; các thông tin xác thực phải được đưa vào môi trường
tiến trình. Không ghi secret vào code hoặc commit lên Git.

```bash
export MINIO_ACCESS_KEY='<access-key>'
export MINIO_SECRET_KEY='<secret-key>'
# Nếu API yêu cầu header, đặt theo cấu hình xác thực đang sử dụng:
export INGEST_API_HEADERS='{"X-User-Id":"<user-id>"}'

uv run python scripts/ingest_minio.py \
  --endpoint http://localhost:9000 \
  --bucket vanbandiaphuong \
  --upload-url http://localhost:8080/api/documents/upload
```

Không truyền `--prefix` nghĩa là nạp toàn bộ bucket, bao gồm thư mục con.
Để giới hạn phạm vi, thêm `--prefix 'folder/'`. Thêm `--type luat` để đặt
`doc_type`; thêm `--force-reingest` để nạp lại file đã tồn tại.

Script liệt kê object và gửi từng file tuần tự, dùng basename của object làm
tên multipart. API chuẩn hóa tên và nhận diện trùng theo tên + bytes.
Hai object khác thư mục nhưng cùng basename và bytes được xem là trùng;
cùng basename nhưng khác bytes có ID khác, nhưng file lưu trên đĩa backend
vẫn có thể ghi đè nhau theo cơ chế storage hiện tại.

Không lưu cả bucket xuống đĩa. Mỗi file được đệm tối đa 8 MiB trong RAM, phần
lớn hơn dùng file tạm tự xóa. Chỉ file `.pdf`, `.doc`, `.docx` được gửi lên API
(không phân biệt chữ hoa/thường). Object dạng folder và các file khác được bỏ
qua trước khi tải từ MinIO, tính vào `skipped`. Không tự retry request lỗi hoặc
timeout vì request trước có thể vẫn đang ingest ở backend.

Mỗi file xuất một dòng JSON trạng thái `ingested`, `already_exists` hoặc
`error`; cuối cùng có `summary`. Exit code 1 khi có lỗi, 0 khi hoàn tất không
lỗi. Có thể chạy lại để API bỏ qua các file đã được nạp.

Backend cần phiên bản API trả trường `already_exists`; response thiếu trường
này được tính là lỗi để tránh báo nhầm kết quả. Timeout mặc định 1800 giây mỗi
thao tác HTTP, có thể chỉnh bằng `--timeout`.

Nếu chạy script trong container, `localhost` là container đó; thay endpoint
và URL upload bằng hostname dịch vụ mà container truy cập được.

Đã khai báo `httpx` trực tiếp trong `pyproject.toml`. Khi chuẩn bị môi trường,
cần cập nhật `uv.lock`/đồng bộ dependencies; chưa thực hiện trong thay đổi này.
