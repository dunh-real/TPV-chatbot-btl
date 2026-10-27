# Phân quyền chatbot theo database ERP

Quyền hiệu lực lấy từ `AbpUsers → AbpUserRoles → AbpRoles → AbpPermissions`.
Chỉ nhận tài khoản hoạt động, role chưa xóa và liên kết/permission cùng tenant.
Username/email trùng nhiều tài khoản không được chọn tùy ý; dùng UserId cụ thể.
Các permission `IsGranted=1` được hợp từ các role hợp lệ. Dòng `IsGranted=0`
không cấp quyền và không trừ quyền được cấp bởi role khác, giữ cách hợp quyền
của repository hiện tại. Static Admin (`Name=Admin`, `IsStatic=1`) giữ cơ chế
bỏ qua kiểm tra permission hiện có. Không suy Admin từ DisplayName.

## Quyền chức năng

| Chức năng | Permission |
| --- | --- |
| Chat/tra cứu tài liệu | `Ai.AiChatbot` |
| Soát tài liệu/giao việc | `Ai.DocumentReview` |
| Soạn báo cáo | `Ai.ReportDrafting` |
| Tổng hợp báo cáo | `Ai.ReportSummary.Create` |
| Tạo slide | `Ai.Slide.Create` |
| OCR | `Ai.Ocr` |
| Sơ đồ tư duy | `Ai.MindMap` |

Không tra được tài khoản/quyền thì các chức năng được bảo vệ từ chối, kể cả
khi ERP tạm mất kết nối. Health/UI vẫn có thể được truy cập. Quyền có cache
60 giây; thay đổi role/permission có hiệu lực sau TTL hoặc xóa cache.

## Quyền dữ liệu SQL

Không dùng `groups`, `tenant_roles`, `priority`, `default_group` để cấp quyền.
Role không ánh xạ JSON vẫn được đọc nếu có permission phù hợp trong DB.

`erp_sql_policy.json` còn giữ hợp đồng bảng/cột/ý nghĩa nghiệp vụ và ngoại lệ
test. Các cột ngoài hợp đồng không tự mở khi database thêm schema mới.

- `Hrm.EmployeeProfile.View`: đọc các cột nhân sự an toàn trong tenant.
- Chỉ có `Hrm.EmployeeProfile.ViewMine`: đọc hồ sơ có `EmployeeId` khớp nhân
  viên liên kết với tài khoản; danh mục phòng ban giới hạn phòng ban hiện tại.
- `Asm.Asset.View`: đọc các cột tài sản an toàn và danh mục liên quan trong tenant.
- Không suy giới hạn phòng ban từ tên role. Với quyền `.View`, cách ánh xạ của
  chatbot là toàn tenant; đây là quy tắc của chatbot, chưa chứng minh tương
  đương mọi bộ lọc nghiệp vụ của ứng dụng ERP gốc.
- Thiếu quyền, liên kết nhân viên bị thiếu/trùng khi cần ViewMine, hoặc tenant
  không hợp lệ thì từ chối. Luôn chèn tenant filter vào từng bảng nguồn SQL.
- SQL chỉ đọc, cột nhạy cảm ngoài hợp đồng bị chặn, giới hạn số dòng vẫn giữ.

## Ngoại lệ test

`account_read_grants["64:153"]` giữ nguyên danh sách 91 bảng/cột riêng, chỉ áp
dụng nếu tài khoản vẫn được xác minh là static Admin trong tenant 64. Không mở
cho Admin khác, tenant khác hoặc khi tài khoản bị thu hồi role Admin.

## Kiểm chứng

Chạy unit/regression test trong backend:

```bash
DEBUG=false .venv/bin/python -m pytest -q tests/test_db_permissions.py tests/test_erp_groups.py tests/test_erp_discovery.py
```

Audit DB thật (chỉ SELECT, không LLM, không thay đổi permission):

```bash
DEBUG=false .venv/bin/python scripts/audit_db_permissions.py
```

Audit toàn bộ role chưa xóa và tài khoản hoạt động; kiểm tra SQL tenant binding
trên mọi bảng được phép. Role audit dùng tài khoản giả nên ViewMine không có
liên kết nhân viên sẽ bị từ chối; account audit dùng tài khoản thật.

Header danh tính vẫn là cơ chế test hiện có; thay đổi này không bổ sung đăng nhập
hoặc xác thực token. Không sửa permission trong database, không tự cấp quyền AI.
