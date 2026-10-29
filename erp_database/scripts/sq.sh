#!/bin/sh
# sqlcmd đăng nhập sa; mật khẩu nạp từ secrets/mssql.env vào SQLCMDPASSWORD, không nằm trên dòng lệnh.
. "$(dirname "$0")/../secrets/mssql.env"
export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"
exec /opt/mssql-tools18/bin/sqlcmd -S 127.0.0.1,1433 -U sa -C -b "$@"
