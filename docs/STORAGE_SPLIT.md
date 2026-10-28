# Tách SQLite pháp lý và MSSQL nghiệp vụ

## Kết nối

| Cấu hình | Trách nhiệm |
|---|---|
| `DATABASE_URL` | Chỉ SQLite pháp lý; mặc định `./legal_data/legal.db` |
| `AI_DATABASE_URL` | Đọc/ghi bảng AI trên MSSQL; bắt buộc cho mẫu, văn bản, đăng ký đầu ra |
| `ERP_DATABASE_URL` | MSSQL chỉ đọc cho tài khoản, quyền và dữ liệu nguồn |

Trên Mac đã thêm `AI_DATABASE_URL` từ kết nối ERP hiện có tới
`dbnews.tpvtech.vn:7679`. Không sửa URL ERP hoặc credential của nó.
Do `backend/data` trên Mac là một file, `DATABASE_URL` local được đặt riêng tại
`sqlite+aiosqlite:///./legal_data/legal.db`. SQLite được tạo khi phần pháp lý cần
khởi tạo; cờ `LEGAL_VERSIONING_ENABLED` không tự bật trong đợt này.

Không chuyển dữ liệu mẫu hoặc báo cáo từ SQLite cũ; không xóa các bảng/bản ghi cũ.
Snapshot `artifacts/db-analysis/.../demo.db` giữ nguyên. Không chạy migration/DDL MSSQL.

## Thay đổi chính

- `db/models.py`: chỉ còn tám bảng pháp lý; không còn model `TemplateBaoCao`, `VanBan`.
- `db/session.py`: chỉ chấp nhận SQLite, chỉ tạo/cập nhật metadata pháp lý.
- `db/business_types.py`: thông tin mẫu/tài liệu thuần Python, không tạo bảng.
- `db/repository.py`: mẫu và tài liệu đọc từ MSSQL; không nhận session SQLite,
  không có nhánh fallback đọc/ghi sổ demo.
- `tools/templates.py`, `tools/rag.py`, `api/reports.py`, các node soạn/tổng hợp:
  bỏ session SQLite khỏi điểm gọi nghiệp vụ.
- `services/report_register.py`: thống kê báo cáo đã gửi từ tác vụ Draft hoàn thành,
  liên kết `AI_DocumentDrafts`; khớp chính xác mã đơn vị/kỳ trong kết quả,
  không suy từ tên file hoặc ngày ký. File MinIO được tải tạm khi tổng hợp từ báo cáo.
- Bỏ các node đăng ký sổ SQLite cũ. Decorator workflow ghi sản phẩm và task MSSQL.
- Công cụ xuất Word/slide trực tiếp và endpoint giao việc cũng đăng ký đầu ra
  MSSQL/MinIO; file Word chung dùng task + `AI_OutputVersions`, không giả tạo mẫu Draft.
- `seed_demo.py` ngừng seed nghiệp vụ SQLite; `gen_schema.py` chỉ sinh DDL SQLite pháp lý.
- `/health`: `database` là kết nối nghiệp vụ MSSQL; `details.legal_database` phản ánh
  SQLite khi bật pháp lý, `null` khi không bật. Thiếu cấu hình nghiệp vụ trả 503.

## Số văn bản

Không dùng bộ đếm demo hoặc tự cấp lại `01/...`. Người dùng nhập số ký hiệu được cấp
qua `inputs.so_ky_hieu` (Word trực tiếp dùng `content.meta.so_ky_hieu`).

Kiểm tra số trùng trên tài liệu MSSQL và task Processing/Completed trong cùng tenant.
Khóa `sp_getapplock` theo tenant/số trong transaction khi tạo task để tránh hai yêu cầu
đồng thời giữ cùng số. Node xuất loại task hiện tại khỏi phép kiểm tra để không tự
chặn chính nó. Trùng số trả HTTP 409. Task Failed/Cancelled không giữ số như task hoàn thành.

Đây là kiểm tra/giữ số, không thay thế hệ thống cấp số văn bản chính thức.

## Kiểm chứng và phạm vi

Test mới kiểm tra SQLite chỉ tạo tám bảng, không fallback khi thiếu MSSQL,
workflow không chạy khi chưa cấu hình, chặn số trùng và đăng ký file/phiên bản.
Các fixture nghiệp vụ trước đây tạo bảng SQLite được đổi sang mock tại biên MSSQL;
test pháp lý vẫn dùng SQLite thật trong thư mục tạm.

`scripts/check_ai_database.py --write-rollback --user-id 407` kiểm tra schema và
thao tác trên MSSQL thật, luôn rollback. Bao gồm kiểm tra số văn bản và bỏ qua task
hiện tại; không chuyển dữ liệu SQLite cũ. Báo cáo tại
`artifacts/storage-split/mssql-rollback.json` trên Mac (không commit artifacts).

Mẫu và phiên bản SQL vẫn cần Published; không tự công bố mẫu Retired.
Không thêm API UC37, retry/notification/worker ở đợt này. Chưa đồng bộ code của
đợt tách này sang yuu, `/chatbot/` hoặc kien.

Báo cáo `BAO-CAO-THAY-DOI-MAP-DB-2026-10-28.md` là snapshot của đợt trước,
không phải mô tả code sau bước tách lưu trữ này.
