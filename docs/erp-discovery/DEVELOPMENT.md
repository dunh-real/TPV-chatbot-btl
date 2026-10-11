# Phát triển và kiểm thử

## Cấu hình

Chạy từ `backend/`; sao chép `.env.example` thành `.env` và điền giá trị môi trường.
Không commit `.env`, snapshot DB hoặc dữ liệu chạy thử.

| Biến | Mục đích |
|---|---|
| `ERP_DATABASE_URL` | SQL Server ERP; ưu tiên tài khoản DB chỉ có SELECT |
| `ERP_TEXT2SQL_BASE_URL`, `ERP_TEXT2SQL_MODEL` | Endpoint/model Qwen |
| `ERP_TEXT2SQL_API_KEY` | Khóa endpoint nếu cần |
| `ERP_SCHEMA_FILE`, `ERP_SCHEMA_REFRESH_SECONDS` | File metadata và chu kỳ refresh |
| `ERP_SQL_POLICY_FILE` | Policy bảng/cột/nhóm |
| `ERP_TEXT2SQL_MAX_ROWS`, `ERP_TEXT2SQL_QUERY_TIMEOUT` | Giới hạn dòng và thời gian SQL |
| `ERP_TEXT2SQL_MAX_REPAIRS`, `ERP_TEXT2SQL_MAX_TOOL_CALLS` | Giới hạn sửa lỗi và discovery |

vLLM phải bật native tool calling và parser phù hợp với model. Pipeline discovery
luôn tắt thinking; cấu hình thinking của chat không điều khiển pipeline này.
`ERP_TENANT_ID` chỉ là tenant mặc định, không thay thế tài khoản và quyền.

## Sửa phân quyền

1. Xác nhận role Id, tenant và quyền module thật trong ERP.
2. Thêm mapping vào `groups.<nhóm>.tenant_roles`; không lấy nhóm từ câu hỏi hoặc header tùy ý.
3. Khai bảng/cột trong `tables`, rồi thu hẹp ở `groups.<nhóm>.tables` khi cần.
4. Với phạm vi cá nhân/phòng ban, đặt `scope` và `scope_column` đúng khóa nghiệp vụ.
5. Thêm kiểm thử tài khoản được phép và tài khoản bị chặn trước khi triển khai.

Nhiều nhóm thì chọn nhóm có `priority` lớn nhất; trùng ưu tiên bị từ chối.
Nhóm mặc định là `employee`. Quyền module vẫn bắt buộc; `ViewMine` không tự cấp
quyền đọc cả phòng/công ty. Mục `users` chỉ giới hạn thêm, không mở rộng quyền nhóm.

Liên kết chuẩn đang dùng: tài khoản → `Dms_Employee.UserId` cùng tenant,
`IsDeleted=0` → `EmployeeId` của hồ sơ hoặc `WorkDepartmentId`.
Thiếu/trùng liên kết bị chặn. Muốn quản lý nhiều phòng/phòng ban con phải bổ sung
resolver và kiểm thử; không tự mở phạm vi bằng tên chức vụ.

Policy được đọc mỗi truy vấn; sửa JSON có hiệu lực ở request tiếp theo.
Quyền tài khoản ERP có cache 60 giây. Metadata được refresh theo TTL; bảng/cột mới
không tự được cấp quyền dù đã xuất hiện trong snapshot.

## Kiểm thử

```bash
uv sync --locked --group dev
uv run pytest tests/test_erp_discovery.py tests/test_erp_groups.py \
  tests/test_query_workflows.py tests/test_identity.py tests/test_toolloop.py -q
```

- `test_erp_discovery.py`: SQL read-only, bảng/cột, discovery/repair và refresh schema.
- `test_erp_groups.py`: nhóm, tenant, phạm vi dòng và liên kết nhân viên.
- `test_query_workflows.py`: Word/PowerPoint nhận bảng động và chặn kết quả bị cắt.
- Các kiểm thử trên dùng DB/model giả, không cần DB ERP hoặc endpoint Qwen.

Đợt bàn giao 11/10/2026: bộ hồi quy gồm 13 file kiểm thử ERP, agent và workflow
đạt 300 kiểm thử; 24 kiểm thử ngoài phạm vi lựa chọn bị loại. Chưa đánh giá toàn
bộ test suite hoặc độ chính xác Qwen trên bộ câu hỏi nghiệp vụ.

Khi kiểm tra thật: dùng tài khoản thử có quyền rõ ràng; kiểm tra cả SELECT thành
công và truy vấn bị chặn. Đối chiếu kết quả với SQL chuẩn để đánh giá nghiệp vụ.
Không ghi họ tên, dữ liệu nhạy cảm hoặc connection string vào log/fixture bàn giao.

## Chẩn đoán nhanh

| Hiện tượng | Kiểm tra |
|---|---|
| Thiếu tài khoản/tenant | Danh tính request và kết quả resolve quyền ERP |
| Chưa có bảng/cột phù hợp | Quyền module, nhóm, whitelist và mục `users` |
| Đếm được nhưng không đọc tên | Cột `FullName` và quyền cột của nhóm/tài khoản |
| Thiếu/trùng liên kết nhân viên | `Dms_Employee.UserId`, tenant và hồ sơ đang hoạt động |
| Lỗi `auto` tool choice | Cấu hình tool calling của vLLM |
| SQL chạy nhưng số sai | Định nghĩa chỉ tiêu, mốc thời gian, JOIN nhân bản dòng |

Giữ fail-closed khi sửa lỗi; không fallback sang admin hoặc bỏ bộ lọc tenant.
