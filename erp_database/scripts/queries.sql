-- Truy vấn mẫu cho bqp-private-ai-assistant. Mọi truy vấn đều lọc IsDeleted = 0 và TenantId.
-- Chạy: sqlcmd -S localhost -E -C -d bqp-private-ai-assistant -i queries.sql
SET NOCOUNT ON;
DECLARE @tenant int = 63;   -- TPV

PRINT '-- Q1. Tenant + số người dùng / nhân viên còn hiệu lực';
SELECT t.Id, t.TenancyName, t.IsActive,
       (SELECT COUNT(*) FROM dbo.AbpUsers u WHERE u.TenantId = t.Id AND u.IsDeleted = 0) AS users,
       (SELECT COUNT(*) FROM dbo.Dms_Employee e WHERE e.TenantId = t.Id AND e.IsDeleted = 0) AS employees
FROM dbo.AbpTenants t WHERE t.IsDeleted = 0 ORDER BY t.Id;

PRINT '-- Q2. Cây phòng ban của một tenant (đệ quy theo ParentId) + số nhân viên';
WITH tree AS (
    SELECT Id, ParentId, DisplayName, 0 AS depth, CAST(DisplayName AS nvarchar(1000)) AS path
    FROM dbo.Dms_WorkDepartment WHERE TenantId = @tenant AND IsDeleted = 0 AND ParentId IS NULL
    UNION ALL
    SELECT d.Id, d.ParentId, d.DisplayName, t.depth + 1, CAST(t.path + N' > ' + d.DisplayName AS nvarchar(1000))
    FROM dbo.Dms_WorkDepartment d JOIN tree t ON d.ParentId = t.Id WHERE d.IsDeleted = 0)
SELECT tr.depth, tr.path,
       (SELECT COUNT(*) FROM dbo.Dms_Employee e WHERE e.WorkDepartmentId = tr.Id AND e.IsDeleted = 0) AS employees
FROM tree tr ORDER BY tr.path;

PRINT '-- Q3. Nhân viên + phòng ban + chức danh + tài khoản (không lấy thông tin liên hệ)';
SELECT TOP 5 e.Id, e.Code, d.DisplayName AS department, p.Name AS position, u.UserName, u.IsActive
FROM dbo.Dms_Employee e
JOIN dbo.Dms_WorkDepartment d ON d.Id = e.WorkDepartmentId
JOIN dbo.Dms_WorkPosition p ON p.Id = e.WorkPositionId
LEFT JOIN dbo.AbpUsers u ON u.Id = e.UserId
WHERE e.TenantId = @tenant AND e.IsDeleted = 0 ORDER BY e.Id;

PRINT '-- Q4. Chấm công theo tháng: số lượt check-in, số nhân viên';
SELECT FORMAT(a.[Date], 'yyyy-MM') AS month, COUNT(*) AS records, COUNT(DISTINCT a.EmployeeId) AS employees
FROM dbo.Hrm_Attendancel a WHERE a.TenantId = @tenant AND a.IsDeleted = 0
GROUP BY FORMAT(a.[Date], 'yyyy-MM') ORDER BY month;

PRINT '-- Q5. Bảng công tổng hợp: đi muộn / vắng theo kỳ công';
SELECT p.Id AS period_id, CONCAT(p.PeriodMonth, '/', p.PeriodYear) AS period, p.Status, COUNT(*) AS day_rows,
       SUM(CASE WHEN d.IsLate = 1 THEN 1 ELSE 0 END) AS late, SUM(CASE WHEN d.IsAbsent = 1 THEN 1 ELSE 0 END) AS absent,
       SUM(CASE WHEN d.IsBusinessTrip = 1 THEN 1 ELSE 0 END) AS business_trip
FROM dbo.Hrm_TimesheetSummaryDetail d JOIN dbo.Hrm_TimesheetPeriod p ON p.Id = d.PeriodId
WHERE d.TenantId = @tenant AND d.IsDeleted = 0 GROUP BY p.Id, p.PeriodMonth, p.PeriodYear, p.Status ORDER BY p.Id;

PRINT '-- Q6. Bảng lương: phiếu lương CÒN HIỆU LỰC theo kỳ lương (98% phiếu đã xoá mềm)';
SELECT pr.Id AS payroll_id, pr.TenantId, pr.Code, CONCAT(pr.PayrollMonth, '/', pr.PayrollYear) AS period, pr.Status, COUNT(ps.Id) AS payslips,
       CAST(SUM(ps.GrossSalary) AS decimal(20,0)) AS gross, CAST(SUM(ps.NetSalary) AS decimal(20,0)) AS net
FROM dbo.Att_Payroll pr JOIN dbo.Att_Payslip ps ON ps.PayrollId = pr.Id AND ps.IsDeleted = 0
WHERE pr.IsDeleted = 0 GROUP BY pr.Id, pr.TenantId, pr.Code, pr.PayrollMonth, pr.PayrollYear, pr.Status ORDER BY pr.Id;

PRINT '-- Q7. Chi tiết một phiếu lương theo thành phần lương';
SELECT TOP 10 c.Name AS component, c.ComponentType, d.Amount
FROM dbo.Att_PayslipDetail d JOIN dbo.Att_Component c ON c.Id = d.ComponentId
WHERE d.PayslipId = (SELECT TOP 1 Id FROM dbo.Att_Payslip WHERE IsDeleted = 0 ORDER BY Id DESC) AND d.IsDeleted = 0;

PRINT '-- Q8. Nghỉ phép: số đơn theo trạng thái và loại';
SELECT r.Status, r.LeaveType, COUNT(*) AS requests, SUM(ISNULL(r.DurationMinutes, 0)) / 60 AS hours
FROM dbo.Hrm_LeaveRequest r WHERE r.TenantId = @tenant AND r.IsDeleted = 0
GROUP BY r.Status, r.LeaveType ORDER BY r.Status, requests DESC;

PRINT '-- Q9. Phê duyệt: yêu cầu đang chờ theo loại chứng từ (liên kết đa hình EntityFullName + EntityId)';
SELECT ar.EntityFullName, ar.Status, COUNT(*) AS requests
FROM dbo.App_ApprovalRequests ar WHERE ar.IsDeleted = 0 GROUP BY ar.EntityFullName, ar.Status ORDER BY ar.EntityFullName, ar.Status;

PRINT '-- Q10. Đề nghị thanh toán + chứng từ chi gắn kèm (tenant 64)';
SELECT TOP 5 r.Id, r.Code, r.Status, r.PaymentDueDate, COUNT(v.Id) AS vouchers, CAST(SUM(v.Amount) AS decimal(20,0)) AS amount
FROM dbo.Fin_PaymentRequests r LEFT JOIN dbo.Fin_ExpenseVouchers v ON v.PaymentRequestId = r.Id AND v.IsDeleted = 0
WHERE r.TenantId = 64 AND r.IsDeleted = 0 GROUP BY r.Id, r.Code, r.Status, r.PaymentDueDate ORDER BY r.Id DESC;

PRINT '-- Q11. Dòng tiền theo ví (thu - chi), tenant 64';
SELECT w.Id, w.Name, w.WalletType,
       CAST(ISNULL((SELECT SUM(Amount) FROM dbo.Fin_RevenueVouchers x WHERE x.WalletId = w.Id AND x.IsDeleted = 0), 0) AS decimal(20,0)) AS revenue,
       CAST(ISNULL((SELECT SUM(Amount) FROM dbo.Fin_ExpenseVouchers x WHERE x.WalletId = w.Id AND x.IsDeleted = 0), 0) AS decimal(20,0)) AS expense
FROM dbo.Fin_Wallets w WHERE w.TenantId = 64 AND w.IsDeleted = 0 ORDER BY w.Id;

PRINT '-- Q12. Tài sản theo loại và phòng ban';
SELECT TOP 10 c.Name AS category, d.DisplayName AS department, COUNT(*) AS assets, SUM(ISNULL(a.Quantity, 1)) AS quantity
FROM dbo.Asm_Assets a LEFT JOIN dbo.Asm_AssetCategories c ON c.Id = a.AssetCategoryId
LEFT JOIN dbo.Dms_WorkDepartment d ON d.Id = a.WorkDepartmentId
WHERE a.IsDeleted = 0 GROUP BY c.Name, d.DisplayName ORDER BY assets DESC;

PRINT '-- Q13. Quyền của một vai trò (AbpPermissions theo RoleId)';
SELECT TOP 10 r.Name AS role, p.Name AS permission, p.IsGranted
FROM dbo.AbpPermissions p JOIN dbo.AbpRoles r ON r.Id = p.RoleId
WHERE r.TenantId = @tenant AND r.IsDeleted = 0 ORDER BY r.Name, p.Name;

PRINT '-- Q14. Audit: số request và lỗi theo ngày của một service';
SELECT TOP 7 CAST(ExecutionTime AS date) AS day, COUNT(*) AS calls,
       SUM(CASE WHEN Exception IS NOT NULL THEN 1 ELSE 0 END) AS errors, AVG(ExecutionDuration) AS avg_ms
FROM dbo.AbpAuditLogs WHERE TenantId = @tenant AND ExecutionTime >= '2026-09-01'
GROUP BY CAST(ExecutionTime AS date) ORDER BY day DESC;

PRINT '-- Q15. AI: tài liệu chatbot theo tính năng';
SELECT AiFeatureType, TenantId, COUNT(*) AS docs, MIN(CreatedAt) AS first, MAX(CreatedAt) AS last
FROM dbo.DocumentsChatBot GROUP BY AiFeatureType, TenantId ORDER BY docs DESC;
