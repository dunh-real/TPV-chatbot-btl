# Quyền ERP và ánh xạ dữ liệu Text2SQL

Cập nhật 30/10/2026. Đọc trực tiếp DB ERP, không yêu cầu BE làm API quyền.

## Cách tính quyền đã triển khai

1. Chỉ nhận user hoạt động, chưa xóa; lấy tenant từ tài khoản.
2. Lấy role qua `AbpUserRoles`, loại role đã xóa hoặc khác tenant.
3. Đọc grant/deny của từng role trong `AbpPermissions`. Lấy hợp grant của các role; role khác từ chối không hủy grant đó.
4. Đọc quyền trực tiếp `UserId`, `RoleId IS NULL` cùng tenant: grant trực tiếp được xét trước, deny trực tiếp chặn grant từ role. Không có override trực tiếp thì dùng kết quả role.
5. Giữ ngoại lệ `Name=Admin AND IsStatic=1` của code hiện tại qua `ERP_STATIC_ADMIN_FULL_ACCESS=true`. Deny trực tiếp của user vẫn được áp dụng. Đây là chế độ tương thích, không phải quy tắc toàn quyền có thể chứng minh chỉ bằng dữ liệu AbpRoles.

Nếu cùng một role có cả grant và deny cho cùng quyền, DB không có thứ tự áp dụng đã xác nhận: code từ chối riêng quyền mâu thuẫn đó thay vì chọn một dòng tùy ý. Nếu nhiều dòng quyền user trực tiếp cùng tên, grant được xét trước deny theo logic tham chiếu ABP.

Nguồn tham chiếu framework: [AbpUserManager](https://github.com/aspnetboilerplate/aspnetboilerplate/blob/dev/src/Abp.ZeroCore/Authorization/Users/AbpUserManager.cs) và [AbpRoleManager](https://github.com/aspnetboilerplate/aspnetboilerplate/blob/dev/src/Abp.ZeroCore/Authorization/Roles/AbpRoleManager.cs). Chưa có mã nguồn PermissionChecker/UserManager/StaticRoles của ERP đang triển khai để kiểm chứng các override riêng, mặc định role và phụ thuộc feature. Không khẳng định kết quả đã khớp hoàn toàn ERP đối với những phần đó.

Quyền có cache tối đa 60 giây; thay cấu hình quy tắc cần restart để bỏ cache cũ.

## Ánh xạ dữ liệu theo quyền

`backend/config/erp_sql_policy.json` không còn ngoại lệ đọc mở rộng theo `64:153` hoặc ID tài khoản khác.

- `tables`: bảng/cột được khai báo và `permissions_any` cần có để đọc.
- `scope_permissions`: quyền nào ứng với phạm vi `tenant`, `self`, `department`; chọn phạm vi rộng nhất mà quyền hiệu lực cho phép.
- `self_column`, `department_column`: cột dùng chèn điều kiện bắt buộc. Giá trị nhân viên/phòng lấy từ `Dms_Employee.UserId` cùng tenant, không nhận từ câu hỏi hoặc header tùy ý.
- `admin_columns`: cột nghiệp vụ bổ sung dùng chung cho Admin, không phụ thuộc tenant/user ID.
- `users` nếu có chỉ được thu hẹp phạm vi, không mở thêm dữ liệu.

Hiện giữ các bảng nghiệp vụ đã có mapping. Admin được đọc thêm `Gender`, `DateOfBirth`, `EducationLevel`; tài khoản thường không tự có thêm ba cột này. Semantic mô tả ý nghĩa, ngày chốt và cảnh báo enum chưa xác nhận.

Không chuyển toàn bộ 91 bảng từ ngoại lệ cũ sang mọi Admin. Bảng/cột mới trong DB không tự mở; mật khẩu, token, giấy tờ và tài khoản ngân hàng không được mở bởi thay đổi này.

## Phạm vi giữ nguyên và giới hạn xác nhận

- `Hrm.EmployeeProfile.View`: đọc hồ sơ trong tenant theo mapping hiện có.
- Chỉ có `Hrm.EmployeeProfile.ViewMine`: đọc hồ sơ của nhân viên liên kết với user; danh mục phòng ban giới hạn phòng hiện tại.
- `Asm.Asset.View`: đọc dữ liệu tài sản được mapping trong tenant.
- Chưa mở rộng xuống cây phòng ban, nhiều phòng quản lý hoặc xem xuyên tenant. Không có dữ liệu/quy tắc ERP xác nhận thì không suy từ chức danh hay tên role.
- Khi phạm vi cá nhân/phòng ban cần liên kết nhân viên, thiếu hoặc trùng liên kết thì từ chối, không bỏ bộ lọc.

Giữ bắt buộc tenant trên từng nguồn SQL, kiểm tra bảng/cột, SELECT/chỉ đọc, hàm/cấu trúc SQL, timeout và số dòng. Admin vẫn chịu các kiểm tra này.

## Trước khi triển khai server

Đối chiếu quy tắc Admin và các default/override của ERP thực tế bằng mã nguồn hoặc xác nhận nghiệp vụ. Test user thường, user có override trực tiếp, Admin, nhiều role, tenant khác và thiếu liên kết nhân viên.

Bản này mới sửa và kiểm thử trên Mac; chưa cập nhật yuu/Kiên, không sửa DB hoặc phân quyền ERP.
