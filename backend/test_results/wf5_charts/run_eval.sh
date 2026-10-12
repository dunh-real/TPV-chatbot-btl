#!/bin/sh
# Chạy eval_charts.py trên server, trên BẢN SAO mã nguồn (không đụng backend production đang chạy).
#   WORK=~/loi_workspace/wf5_chart_test ./run_eval.sh --out runs/r1 [--only Q01,Q02]
# Chuỗi kết nối tpv_reader dựng từ secrets (không in, không ghi log), như report_pipeline/run.sh.
set -eu
WORK="${WORK:-$HOME/loi_workspace/wf5_chart_test}"
cd "$WORK/backend"
. "$HOME/loi_workspace/mssql/secrets/mssql.env"
export T2S_ODBC="Driver={ODBC Driver 18 for SQL Server};Server=127.0.0.1,1433;Database=bqp-private-ai-assistant;Uid=$MSSQL_RO_USER;Pwd={$MSSQL_RO_PASSWORD};Encrypt=yes;TrustServerCertificate=yes"
unset MSSQL_SA_PASSWORD MSSQL_RO_PASSWORD
# Không có .env trong bản sao: cấu hình chỉ lấy từ đây. Không cần vLLM (phần nhận xét do code viết).
export PRESENTON_URL=http://127.0.0.1:5002 PRESENTON_TEMPLATE="${PRESENTON_TEMPLATE:-momentum}" \
       PRESENTON_RENDER_MODE=direct PRESENTON_SUMMARY=false OUTPUT_DIR="$WORK/output" MPLBACKEND=Agg
export PYTHONPATH="$WORK/backend:$WORK/pydeps"
PY="$HOME/loi_workspace/TPV-chatbot-btl/backend/.venv/bin/python"
# CHECK=nprefix: chỉ kiểm chuỗi N'' trên SQL Server (check_nprefix.py), không dựng slide.
if [ "${CHECK:-}" = nprefix ]; then exec "$PY" test_results/wf5_charts/check_nprefix.py; fi
exec "$PY" test_results/wf5_charts/eval_charts.py --questions "$HOME/loi_workspace/report_pipeline" "$@"
