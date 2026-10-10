SET NOCOUNT ON;
PRINT '=== 1. Database';
SELECT name, state_desc, recovery_model_desc, compatibility_level, collation_name, user_access_desc, is_read_only, create_date
FROM sys.databases WHERE name = N'bqp-private-ai-assistant';
USE [bqp-private-ai-assistant];
PRINT '=== 2. Kich thuoc file';
SELECT name AS logical_name, type_desc, physical_name, CAST(size * 8.0 / 1024 AS decimal(12,2)) AS size_mb,
       CAST(FILEPROPERTY(name, 'SpaceUsed') * 8.0 / 1024 AS decimal(12,2)) AS used_mb
FROM sys.database_files;
EXEC sp_spaceused;
PRINT '=== 3. Schema';
SELECT s.name AS schema_name, COUNT(t.object_id) AS tables
FROM sys.schemas s LEFT JOIN sys.tables t ON t.schema_id = s.schema_id AND t.is_ms_shipped = 0
WHERE s.name NOT IN ('sys','INFORMATION_SCHEMA','guest') AND s.name NOT LIKE 'db[_]%'
GROUP BY s.name ORDER BY s.name;
PRINT '=== 4. Tong so doi tuong';
SELECT (SELECT COUNT(*) FROM sys.tables WHERE is_ms_shipped = 0) AS user_tables,
       (SELECT COUNT(*) FROM sys.views WHERE is_ms_shipped = 0) AS views,
       (SELECT COUNT(*) FROM sys.procedures WHERE is_ms_shipped = 0) AS procedures,
       (SELECT COUNT(*) FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id WHERE t.is_ms_shipped = 0) AS columns,
       (SELECT COUNT(*) FROM sys.key_constraints WHERE type = 'PK') AS primary_keys,
       (SELECT COUNT(*) FROM sys.foreign_keys) AS foreign_keys,
       (SELECT COUNT(*) FROM sys.indexes i JOIN sys.tables t ON t.object_id = i.object_id WHERE i.index_id > 0 AND t.is_ms_shipped = 0) AS indexes;
PRINT '=== 5. Bang + so dong dem chinh xac (COUNT_BIG)';
DECLARE @sql nvarchar(max) = N'';
SELECT @sql = @sql + N'SELECT N''' + REPLACE(s.name, '''', '''''') + N''' AS schema_name, N''' + REPLACE(t.name, '''', '''''')
       + N''' AS table_name, COUNT_BIG(*) AS row_count FROM ' + QUOTENAME(s.name) + N'.' + QUOTENAME(t.name) + N' UNION ALL '
FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE t.is_ms_shipped = 0;
IF LEN(@sql) > 0
BEGIN
  SET @sql = N'SELECT * INTO #rc FROM (' + LEFT(@sql, LEN(@sql) - LEN(N' UNION ALL')) + N') x; '
           + N'SELECT schema_name, table_name, row_count FROM #rc ORDER BY row_count DESC, schema_name, table_name; '
           + N'PRINT ''=== 5b. Tong ket so dong''; '
           + N'SELECT COUNT(*) AS tables, SUM(CASE WHEN row_count > 0 THEN 1 ELSE 0 END) AS tables_with_rows, '
           + N'SUM(CASE WHEN row_count = 0 THEN 1 ELSE 0 END) AS empty_tables, SUM(row_count) AS total_rows FROM #rc;';
  EXEC sp_executesql @sql;
END
PRINT '=== 6. Khoa chinh';
SELECT s.name AS schema_name, t.name AS table_name, k.name AS pk_name,
       STRING_AGG(c.name, ',') WITHIN GROUP (ORDER BY ic.key_ordinal) AS pk_columns
FROM sys.key_constraints k JOIN sys.tables t ON t.object_id = k.parent_object_id JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = k.parent_object_id AND ic.index_id = k.unique_index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE k.type = 'PK' GROUP BY s.name, t.name, k.name ORDER BY s.name, t.name;
PRINT '=== 6b. Bang khong co khoa chinh';
SELECT s.name AS schema_name, t.name AS table_name FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE t.is_ms_shipped = 0 AND NOT EXISTS (SELECT 1 FROM sys.key_constraints k WHERE k.parent_object_id = t.object_id AND k.type = 'PK')
ORDER BY s.name, t.name;
PRINT '=== 7. Khoa ngoai';
SELECT fk.name AS fk_name, SCHEMA_NAME(tp.schema_id) + '.' + tp.name AS from_table, cp.name AS from_column,
       SCHEMA_NAME(tr.schema_id) + '.' + tr.name AS to_table, cr.name AS to_column
FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc ON fkc.constraint_object_id = fk.object_id
JOIN sys.tables tp ON tp.object_id = fkc.parent_object_id
JOIN sys.columns cp ON cp.object_id = fkc.parent_object_id AND cp.column_id = fkc.parent_column_id
JOIN sys.tables tr ON tr.object_id = fkc.referenced_object_id
JOIN sys.columns cr ON cr.object_id = fkc.referenced_object_id AND cr.column_id = fkc.referenced_column_id
ORDER BY from_table, fk.name;
PRINT '=== 8. Cot + kieu du lieu cua tung bang';
SELECT s.name AS schema_name, t.name AS table_name, c.column_id, c.name AS column_name, ty.name AS data_type,
       CASE WHEN c.max_length = -1 THEN 'max'
            WHEN ty.name IN ('nvarchar','nchar') THEN CAST(c.max_length / 2 AS varchar(10))
            WHEN ty.name IN ('varchar','char','varbinary','binary') THEN CAST(c.max_length AS varchar(10))
            WHEN ty.name IN ('decimal','numeric') THEN CAST(c.precision AS varchar(5)) + ',' + CAST(c.scale AS varchar(5))
            ELSE '' END AS length,
       c.is_nullable, c.is_identity
FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;
