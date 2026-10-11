# Bàn giao ERP discovery

Đọc lần lượt: tài liệu này → [Ảnh hưởng workflow](WORKFLOWS.md) →
[Hướng dẫn phát triển](DEVELOPMENT.md). Chi tiết engine: [DISCOVERY-ERP](../../backend/DISCOVERY-ERP.md).

## Thay đổi chính

- Thay truy vấn thống kê ERP cố định bằng pipeline Text-to-SQL discovery nội bộ.
- Mã chạy độc lập trong dự án, không import/cài đặt thư mục `text_to_sql` bên ngoài.
- Giữ toàn bộ metadata DB trong file; model chỉ nhận schema đã lọc quyền.
- Mọi SQL qua kiểm tra AST, quyền bảng/cột, tenant, phạm vi dòng và binding trước khi chạy.
- Chat, Word và PowerPoint dùng `QueryResult`, không phụ thuộc các alias cố định
  như `total_personnel`, `new_hires`, `total_equipment` của repository cũ.

## Luồng xử lý

```text
API → IdentityMiddleware → Planner → workflow/tool query_erp(question)
    → schema snapshot + quyền ERP + policy nhóm
    → Qwen discovery → validate → sửa lỗi có giới hạn
    → SELECT có timeout → QueryResult → chat / bảng Word / bảng PowerPoint
```

Planner chọn workflow; discovery sinh SQL. Model không quyết định quyền truy cập.
Không có endpoint cho người dùng gửi SQL tùy ý vào executor.

## Mã nguồn cần biết

| File trong `backend/` | Vai trò |
|---|---|
| `app/tools/data.py` | Tool công khai `query_erp(question)` |
| `app/services/text2sql.py` | Vòng discovery/tool-calling/validate/repair |
| `app/services/erp_discovery.py` | Kết nối DB/model, executor và tác vụ cập nhật schema |
| `app/services/erp_sql_catalog.py` | Metadata, snapshot, schema/danh mục được cấp |
| `app/services/erp_sql_policy.py` | Quyền nhóm, cột, dòng và bảo vệ SQL |
| `config/erp_sql_policy.json` | Whitelist và mapping vai trò ERP sang nhóm |
| `app/schemas/erp.py` | Hợp đồng `QueryResult` |
| `app/documents/query_result.py` | Chuyển bảng động thành Word/PowerPoint |
| `app/services/report_register.py` | Sổ báo cáo của ứng dụng; tách khỏi số liệu ERP |

`erp_repository.py` chỉ giữ danh mục phòng ban cho định tuyến tài liệu.
Các repository thống kê và bộ chuyển đổi về chỉ tiêu cố định không còn được dùng.

## Quyền hiện tại

| Nhóm | Phạm vi tối đa |
|---|---|
| Admin / Giám đốc | Các bảng mở trong tenant tài khoản |
| Nhân sự | Nhân sự trong tenant; chưa gán role ERP thực tế |
| Trưởng phòng | Nhân sự/tài sản trong phòng ban hiện tại |
| Nhân viên / nhóm mặc định | Hồ sơ bản thân và danh mục liên quan được cấp |

Quyền hiệu lực = quyền module ERP ∩ bảng/cột của policy ∩ phạm vi nhóm ∩ giới hạn user.
Admin cũng chịu whitelist. Họ tên/mã nhân viên đã được mở; giấy tờ, liên hệ cá nhân,
ngân hàng và lương chưa được cấp. Mapping role Id kèm tenant nằm trong JSON.

## Giới hạn cần biết

- Validate chứng minh SQL được phép và có thể chạy, chưa chứng minh đúng nghiệp vụ.
- Phạm vi phòng ban dùng liên kết nhân viên hiện tại, chưa gồm phòng ban con/lịch sử quản lý.
- Chọn tài khoản qua header chỉ phục vụ demo, chưa phải đăng nhập xác thực.
- Bộ kiểm thử phân quyền không thay thế đánh giá độ chính xác Qwen bằng câu hỏi/gold SQL.
