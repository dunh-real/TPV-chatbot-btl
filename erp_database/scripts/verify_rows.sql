SET NOCOUNT ON;
USE [bqp-private-ai-assistant];
PRINT '=== 10b. Doi chieu COUNT_BIG voi sys.partitions (rows, chi index 0/1), so trang tinh rieng';
DECLARE @sql nvarchar(max) = N'';
SELECT @sql = @sql + N'SELECT ' + CAST(t.object_id AS nvarchar(20)) + N' AS object_id, COUNT_BIG(*) AS cnt FROM '
       + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N' UNION ALL '
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE t.is_ms_shipped = 0;
SET @sql = N'SELECT * INTO #c FROM (' + LEFT(@sql, LEN(@sql) - LEN(N' UNION ALL')) + N') x;
WITH r AS (SELECT object_id, SUM(rows) AS meta_rows FROM sys.partitions WHERE index_id IN (0,1) GROUP BY object_id),
     pg AS (SELECT p.object_id, SUM(a.used_pages) AS used_pages FROM sys.partitions p
            JOIN sys.allocation_units a ON a.container_id = p.partition_id GROUP BY p.object_id)
SELECT COUNT(*) AS tables,
       SUM(CASE WHEN r.meta_rows = c.cnt THEN 1 ELSE 0 END) AS count_match_metadata,
       SUM(CASE WHEN r.meta_rows <> c.cnt THEN 1 ELSE 0 END) AS mismatch,
       SUM(CASE WHEN c.cnt = 0 AND pg.used_pages = 0 THEN 1 ELSE 0 END) AS empty_no_pages,
       SUM(CASE WHEN c.cnt = 0 AND pg.used_pages > 0 THEN 1 ELSE 0 END) AS empty_with_pages
FROM #c c JOIN r ON r.object_id = c.object_id JOIN pg ON pg.object_id = c.object_id;
SELECT OBJECT_SCHEMA_NAME(c.object_id) + ''.'' + OBJECT_NAME(c.object_id) AS empty_table_with_pages, pg.used_pages
FROM #c c JOIN (SELECT p.object_id, SUM(a.used_pages) AS used_pages FROM sys.partitions p
                JOIN sys.allocation_units a ON a.container_id = p.partition_id GROUP BY p.object_id) pg
  ON pg.object_id = c.object_id WHERE c.cnt = 0 AND pg.used_pages > 0;';
EXEC sp_executesql @sql;
