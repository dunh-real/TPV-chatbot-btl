# Hướng dẫn triển khai TPV Chatbot bằng systemd (không dùng Docker)

> Tài liệu ghi nhận cấu hình triển khai đã kiểm chứng trên Ubuntu 22.04.5, user `abc`, tại `/home/abc/kien_workspace/TPV-chatbot-btl`. Các đường dẫn là **đặc thù máy hiện tại**; cần điều chỉnh khi triển khai sang máy khác. Không đưa mật khẩu, token hoặc nội dung `.env` vào Git.

## 1. Kiến trúc dịch vụ

| Service | Vai trò | Cổng / địa chỉ |
| --- | --- | --- |
| `tpv-backend.service` | FastAPI backend | `0.0.0.0:8080` |
| `tpv-vllm.service` | LLM Qwen/Qwen3.6-35B-A3B-FP8 | `0.0.0.0:15000` |
| `tpv-qdrant.service` | Vector database | `127.0.0.1:6333` (HTTP), 6334 (gRPC nếu bật) |
| `tpv-redis.service` | Cache | `127.0.0.1:6382` |
| `tpv-minio.service` | Object storage | `9000` (API), `9001` (console) |
| `tpv-presenton-api.service` | Presenton FastAPI | `18000` |
| `tpv-presenton-mcp.service` | Presenton MCP | `18001` |
| `tpv-presenton-nextjs.service` | Presenton Next.js | `13000` |
| `tpv-presenton-nginx.service` | Reverse proxy Presenton | `5002` |
| `mssql-server.service` | Microsoft SQL Server / ERP | `127.0.0.1:1433` |

Backend dùng **SQLite** (`DATABASE_URL`, driver `sqlite+aiosqlite`) cho database chính và **MSSQL** (`ERP_DATABASE_URL`, driver `mssql+aioodbc`) cho dữ liệu ERP. MSSQL là dịch vụ native có sẵn, không chạy bằng Docker. `watch_schema()` thử lại theo chu kỳ nếu ERP tạm mất kết nối.

## 2. Điều kiện tiên quyết

- Ubuntu 22.04.5; systemd; user `abc` có quyền đọc/ghi các thư mục ứng dụng tương ứng.
- Mã nguồn tại `/home/abc/kien_workspace/TPV-chatbot-btl`.
- Backend Python virtualenv: `backend/.venv`, chứa FastAPI, SQLAlchemy, `aiosqlite`, `aioodbc`, `pyodbc`, các dependency của ứng dụng.
- Model vLLM và driver GPU/CUDA phù hợp. Binary `vllm` hiện ở `/home/abc/.local/bin/vllm`.
- Qdrant 1.19.2 ở `/home/abc/opt/qdrant/1.19.2`; Redis 7.4.9 ở `/home/abc/opt/redis/7.4.9`.
- Presenton native ở `/home/abc/opt/presenton`, gồm Python venv, Next.js build và file cấu hình riêng `presenton.env`.
- MinIO binary và unit `tpv-minio.service` đã được cài đặt trên máy; **unit MinIO hiện chưa được lưu trong `deploy/systemd/`**, cần sao lưu unit đang chạy trước khi tái triển khai máy mới.
- SQL Server cài native ở `/opt/mssql/bin/sqlservr`, unit `mssql-server.service`.
- LibreOffice CLI: `/usr/bin/soffice`, dùng chuyển `.doc` sang `.docx` không cần Docker.
- Dữ liệu phải được khôi phục/cấp quyền trước khi khởi động service: Qdrant, MinIO, Redis (nếu cần), SQLite, MSSQL, dữ liệu Presenton.

**Không** đưa `backend/.env`, `/home/abc/opt/presenton/presenton.env`, khóa truy cập MinIO, mật khẩu MSSQL hay bản sao database vào repository.

## 3. Cấu hình ứng dụng

Trong `backend/.env`, thiết lập endpoint, database URL, credential và cấu hình của môi trường thực tế. Những biến quan trọng gồm `DATABASE_URL`, `ERP_DATABASE_URL`, các biến kết nối Qdrant, Redis, MinIO, LLM và Presenton theo `backend/app/core/config.py`.

Để chuyển `.doc` bằng LibreOffice native, sử dụng:

```dotenv
DOC_CONVERT_IMAGE=
DOC_CONVERT_TIMEOUT=180
```

`DOC_CONVERT_IMAGE` rỗng để chọn nhánh chuyển đổi native; không đặt lại image Docker nếu muốn vận hành hoàn toàn không Docker. Kiểm tra LibreOffice:

```bash
command -v soffice
soffice --version
```

Backend chạy bằng `scripts/run_backend_systemd.sh`, dùng `backend/.venv/bin/uvicorn`, đồng thời đặt `EMBEDDING_DEVICE=cuda:0` và `RERANKER_DEVICE=cuda:0`. Không đổi GPU hoặc model nếu chưa kiểm tra tài nguyên.

## 4. Cài đặt / đồng bộ các unit systemd

Các unit đã lưu trong repository nằm tại `deploy/systemd/`. Kiểm tra trước khi sao chép vì đường dẫn tuyệt đối và user `abc` được hard-code:

```bash
cd /home/abc/kien_workspace/TPV-chatbot-btl
find deploy/systemd -maxdepth 1 -type f -name '*.service' -print
```

**Chỉ thực hiện bước sau trong đợt triển khai hoặc bảo trì đã được phê duyệt**, không cần chạy lại trên máy đang vận hành ổn định:

```bash
sudo install -m 0644 deploy/systemd/tpv-*.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Lệnh trên cài các unit TPV có trong repository. `tpv-minio.service` và `mssql-server.service` là các unit cần quản lý riêng theo cài đặt thực tế, không được tự tạo unit giả hoặc ghi đè MSSQL của hệ thống.

Để lưu lại cấu hình MinIO đang chạy phục vụ khôi phục máy khác, kiểm tra bằng:

```bash
systemctl cat tpv-minio.service
systemctl show tpv-minio.service -p ExecStart -p EnvironmentFiles -p WorkingDirectory
```

**Chú ý:** đầu ra unit có thể chứa thông tin nhạy cảm nếu credentials được nhúng trong cấu hình. Không commit đầu ra chưa được kiểm duyệt.

### Quan hệ backend ↔ MSSQL

Trên máy hiện tại, đã thêm drop-in `/etc/systemd/system/tpv-backend.service.d/mssql.conf`:

```ini
[Unit]
Wants=mssql-server.service
After=mssql-server.service
```

Nếu triển khai máy mới và cần cấu hình tương tự:

```bash
sudo mkdir -p /etc/systemd/system/tpv-backend.service.d
printf '%s\n' '[Unit]' 'Wants=mssql-server.service' 'After=mssql-server.service' | sudo tee /etc/systemd/system/tpv-backend.service.d/mssql.conf
sudo systemctl daemon-reload
```

`Wants=`/`After=` sắp xếp thứ tự bắt đầu khởi động, **không bảo đảm SQL Server đã sẵn sàng nhận truy vấn**. Backend không chặn startup khi tác vụ `watch_schema()` lỗi; tác vụ này tự thử lại theo chu kỳ. Các request ERP vẫn có thể lỗi tạm thời nếu MSSQL chưa sẵn sàng.

### Bật tự khởi động

Sau khi kiểm tra đầy đủ dữ liệu và cấu hình:

```bash
sudo systemctl enable tpv-backend.service tpv-vllm.service tpv-qdrant.service tpv-redis.service tpv-minio.service \
  tpv-presenton-api.service tpv-presenton-mcp.service tpv-presenton-nextjs.service tpv-presenton-nginx.service mssql-server.service
```

`enable` chỉ cấu hình tự khởi động; **không** tự restart service đang chạy. Với máy mới, khởi động các service theo thứ tự phụ thuộc và kiểm tra từng service trước khi tiếp tục. Tránh dùng `restart` hàng loạt trên môi trường production, đặc biệt vLLM/Qdrant.

## 5. Kiểm tra sau triển khai

```bash
SERVICES='tpv-backend tpv-vllm tpv-qdrant tpv-redis tpv-minio tpv-presenton-api tpv-presenton-mcp tpv-presenton-nextjs tpv-presenton-nginx mssql-server'
for s in $SERVICES; do
  printf '%-30s ' "$s"
  systemctl is-active "$s.service"
done

systemctl show tpv-backend.service -p ActiveState -p MainPID -p NRestarts -p After -p Wants
curl -fsS http://127.0.0.1:8080/health
sudo ss -lntp | grep -E ':(8080|15000|6333|6382|9000|9001|18000|18001|13000|5002|1433)\b' || true
```

`/health` cần báo `status=ok`, và các trường `qdrant`, `llm`, `database` đúng kỳ vọng. Kiểm tra trực tiếp MSSQL/SQLite bằng SQLAlchemy **async**; không dùng `create_engine()` đồng bộ với URL `sqlite+aiosqlite` hoặc `mssql+aioodbc`, vì có thể gây lỗi `MissingGreenlet` dù database vẫn hoạt động.

Xem log không làm gián đoạn dịch vụ:

```bash
journalctl -u tpv-backend.service -n 100 --no-pager
journalctl -u mssql-server.service -n 100 --no-pager
journalctl -u tpv-minio.service -n 100 --no-pager
```

## 6. Dữ liệu, backup và phục hồi

- **Qdrant:** dữ liệu nằm tại `backend/qdrant_storage` theo unit hiện tại. Sao lưu nhất quán bằng snapshot Qdrant hoặc quy trình bảo trì phù hợp, không tùy tiện sao chép file đang ghi.
- **MinIO:** dữ liệu thực tế được đặt tại `/home/abc/kien_workspace/law-documents`, với các bucket `vanbantrunguong` và `vanbandiaphuong`. Giữ nguyên dữ liệu và credentials; xác minh đường dẫn qua unit MinIO thực tế.
- **SQLite:** xác định đường dẫn thực từ `DATABASE_URL` mà không in credentials; sao lưu bằng cơ chế SQLite backup khi database đang hoạt động.
- **MSSQL:** dùng quy trình backup/restore SQL Server phù hợp, không thay thế bằng cách copy nóng các file database.
- **Redis:** cấu hình trong `deploy/redis/redis.conf`, port `6382`, lưu `dump.rdb` tại `/home/abc/opt/redis/data`.
- **Presenton:** lưu giữ cấu hình, dữ liệu và build tại `/home/abc/opt/presenton`; không commit file secrets.

Đừng xóa Docker images/volumes hoặc các container đã dừng chỉ vì service native hoạt động: chúng đang được giữ để rollback, và việc dọn dẹp cần được quyết định riêng.

## 7. Xử lý sự cố và rollback

```bash
systemctl status tpv-backend.service --no-pager
journalctl -u tpv-backend.service -n 150 --no-pager
systemctl status mssql-server.service --no-pager
systemctl show tpv-backend.service -p NRestarts -p MainPID
```

- **Backend lỗi khi reboot:** kiểm tra `.env`, Python venv, quyền thư mục, các dependency và log. MSSQL có thể chưa sẵn sàng; kiểm tra `SELECT 1` qua async engine và log `watch_schema()`.
- **Không truy cập được ERP:** kiểm tra `mssql-server.service`, `127.0.0.1:1433`, credentials và quyền chỉ đọc; tránh thay đổi cơ chế chặn ghi trong `app/db/erp_session.py`.
- **Không truy cập Qdrant:** kiểm tra `tpv-qdrant.service`, storage và collection trước khi thử khởi tạo lại.
- **vLLM lỗi:** kiểm tra GPU, bộ nhớ GPU, model và journal; restart vLLM có thể mất nhiều thời gian tải model.
- **Chuyển `.doc` lỗi:** kiểm tra `soffice`, `DOC_CONVERT_IMAGE` rỗng và `DOC_CONVERT_TIMEOUT`.

Khi cần rollback thay đổi unit, khôi phục **bản sao unit/drop-in trước khi thay đổi**, chạy `sudo systemctl daemon-reload`, rồi chỉ restart service bị ảnh hưởng trong cửa sổ bảo trì. Không rollback dữ liệu bằng thao tác xóa thư mục.

## 8. Quy tắc Git cho triển khai

Chỉ stage các file mã nguồn, unit, script và tài liệu đã được kiểm tra. Không dùng `git add .` khi có `backend/data` hoặc dữ liệu chưa rõ nội dung. Trước commit:

```bash
git status --short
git diff --check
git diff --cached --stat
git diff --cached --check
```

Không commit `.env`, token, dữ liệu Qdrant/MinIO/SQLite/MSSQL, model weights, log, cache hoặc backup. Các thay đổi API/ingestion/test không liên quan trực tiếp tới chuyển đổi systemd nên được review và commit riêng.

## 9. Trạng thái đã kiểm chứng (16/10/2026)

- 9 service TPV và `mssql-server.service` đều `active` và `enabled` trong đợt kiểm tra.
- Backend `/health` trả về `status=ok`; Qdrant, LLM và database báo sẵn sàng.
- SQLite và MSSQL đều chạy thành công `SELECT 1` qua SQLAlchemy async.
- Drop-in backend `Wants=mssql-server.service` và `After=mssql-server.service` đã được systemd nhận; không cần restart để nạp lại quan hệ cấu hình.
- Chưa thực hiện reboot kiểm chứng end-to-end. Việc reboot cần lên lịch bảo trì vì có thể làm gián đoạn vLLM, Qdrant và các dịch vụ khác.
