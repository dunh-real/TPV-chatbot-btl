SET NOCOUNT ON;
USE [bqp-private-ai-assistant];
PRINT '=== 10. Doi chieu COUNT_BIG voi metadata phan vung (sys.partitions) va so trang da cap';
DECLARE @sql nvarchar(max) = N'';
SELECT @sql = @sql + N'SELECT ' + CAST(t.object_id AS nvarchar(20)) + N' AS object_id, COUNT_BIG(*) AS cnt FROM '
       + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N' UNION ALL '
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE t.is_ms_shipped = 0;
SET @sql = N'SELECT * INTO #c FROM (' + LEFT(@sql, LEN(@sql) - LEN(N' UNION ALL')) + N') x;
SELECT SUM(CASE WHEN m.meta_rows = c.cnt THEN 1 ELSE 0 END) AS tables_count_match_metadata,
       SUM(CASE WHEN m.meta_rows <> c.cnt THEN 1 ELSE 0 END) AS tables_mismatch,
       SUM(CASE WHEN c.cnt = 0 AND m.used_pages = 0 THEN 1 ELSE 0 END) AS empty_tables_without_pages,
       SUM(CASE WHEN c.cnt = 0 AND m.used_pages > 0 THEN 1 ELSE 0 END) AS empty_tables_with_pages
FROM #c c JOIN (SELECT p.object_id, SUM(CASE WHEN p.index_id IN (0,1) THEN p.rows ELSE 0 END) AS meta_rows,
                       SUM(a.used_pages) AS used_pages
                FROM sys.partitions p JOIN sys.allocation_units a ON a.container_id = p.partition_id
                GROUP BY p.object_id) m ON m.object_id = c.object_id;
SELECT OBJECT_SCHEMA_NAME(c.object_id) + ''.'' + OBJECT_NAME(c.object_id) AS table_name, m.meta_rows, c.cnt
FROM #c c JOIN (SELECT object_id, SUM(rows) AS meta_rows FROM sys.partitions WHERE index_id IN (0,1) GROUP BY object_id) m
  ON m.object_id = c.object_id WHERE m.meta_rows <> c.cnt;';
EXEC sp_executesql @sql;

PRINT '=== 11. Mau du lieu (cot khong nhay cam)';
PRINT '-- AbpAuditLogs: khoang thoi gian va 3 ban ghi moi nhat';
SELECT COUNT_BIG(*) AS rows, MIN(ExecutionTime) AS first_time, MAX(ExecutionTime) AS last_time FROM dbo.AbpAuditLogs;
SELECT TOP 3 Id, TenantId, ExecutionTime, ExecutionDuration, LEFT(ServiceName, 60) AS service_name, LEFT(MethodName, 40) AS method_name
FROM dbo.AbpAuditLogs ORDER BY Id DESC;
PRINT '-- AbpTenants';
SELECT Id, TenancyName, LEFT(Name, 60) AS name, IsActive, CreationTime FROM dbo.AbpTenants ORDER BY Id;
PRINT '-- AbpUsers: so nguoi dung theo tenant (khong lay thong tin ca nhan)';
SELECT TenantId, COUNT(*) AS users, SUM(CASE WHEN IsActive = 1 THEN 1 ELSE 0 END) AS active_users,
       MIN(CreationTime) AS first_created, MAX(CreationTime) AS last_created
FROM dbo.AbpUsers GROUP BY TenantId ORDER BY TenantId;
PRINT '-- Att_PayslipDetail / Att_Payslip';
SELECT (SELECT COUNT_BIG(*) FROM dbo.Att_Payslip) AS payslips, (SELECT COUNT_BIG(*) FROM dbo.Att_PayslipDetail) AS payslip_details;
PRINT '-- Dms_Ward / Dms_City (danh muc dia ly)';
SELECT TOP 3 * FROM dbo.Dms_City ORDER BY 1;
PRINT '-- __EFMigrationsHistory: migration moi nhat';
SELECT TOP 5 MigrationId, ProductVersion FROM dbo.__EFMigrationsHistory ORDER BY MigrationId DESC;
PRINT '-- HangFire.Job: trang thai job';
SELECT StateName, COUNT(*) AS jobs FROM HangFire.Job GROUP BY StateName ORDER BY jobs DESC;

PRINT '=== 12. User trong database (tu ban goc)';
SELECT name, type_desc, authentication_type_desc, default_schema_name,
       CASE WHEN type IN ('S','U') AND sid IS NOT NULL AND SUSER_SNAME(sid) IS NULL AND name NOT IN ('guest','INFORMATION_SCHEMA','sys','dbo') THEN 'orphan' ELSE '' END AS note
FROM sys.database_principals WHERE type IN ('S','U','G','R') AND is_fixed_role = 0 AND name NOT IN ('public')
ORDER BY type_desc, name;
