#!/bin/sh
# sqlcmd đăng nhập tpv_reader (chỉ đọc); mật khẩu nạp từ secrets/mssql.env.
. "$(dirname "$0")/../secrets/mssql.env"
export SQLCMDPASSWORD="$MSSQL_RO_PASSWORD"
exec /opt/mssql-tools18/bin/sqlcmd -S "${MSSQL_HOST:-127.0.0.1,1433}" -U tpv_reader -C -b "$@"
