"""Kiểm trên SQL Server thật (chỉ đọc, không đụng bảng nào): chuỗi có dấu thiếu N'' có mất dấu không,
và _chuoi_unicode của erp_sql_policy có sửa được không. Chạy qua run_eval.sh-style env (T2S_ODBC)."""

import os

import pyodbc
import sqlglot

from app.services.erp_sql_policy import _chuoi_unicode

SQL = ("SELECT 'Tồn cuối kỳ' AS khong_n, N'Tồn cuối kỳ' AS co_n, "
       "CASE WHEN N'đ' LIKE '%đ%' THEN 1 ELSE 0 END AS like_khong_n, "
       "CAST(DATABASEPROPERTYEX(DB_NAME(), 'Collation') AS nvarchar(100)) AS collation")

conn = pyodbc.connect(os.environ["T2S_ODBC"], readonly=True, timeout=30)
cur = conn.cursor()
row = cur.execute(SQL).fetchone()
print("SQL gốc     :", dict(zip([d[0] for d in cur.description], row)))
fixed = sqlglot.parse_one(SQL, read="tsql").transform(_chuoi_unicode).sql(dialect="tsql")
row = cur.execute(fixed).fetchone()
print("Sau sửa     :", dict(zip([d[0] for d in cur.description], row)))
print("SQL sau sửa :", fixed)
conn.close()
