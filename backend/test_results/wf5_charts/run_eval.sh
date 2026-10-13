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
# Không có .env trong bản sao: cấu hình chỉ lấy từ đây.
export PRESENTON_URL=http://127.0.0.1:5002 PRESENTON_TEMPLATE="${PRESENTON_TEMPLATE:-momentum}" \
       PRESENTON_RENDER_MODE=direct PRESENTON_SUMMARY="${PRESENTON_SUMMARY:-true}" OUTPUT_DIR="$WORK/output" MPLBACKEND=Agg
# Qwen trên vLLM của máy (nhãn cột + nhận xét). Khoá API (nếu bật) lấy từ secrets, không in ra.
export LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:15000/v1}" LLM_MODEL="${LLM_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}"
if [ -f "$HOME/.config/tpv/secrets.env" ]; then
  LLM_API_KEY=$(set -a; . "$HOME/.config/tpv/secrets.env"; printf '%s' "${VLLM_API_KEY:-}")
  if [ -n "$LLM_API_KEY" ]; then export LLM_API_KEY; fi
fi
export PYTHONPATH="$WORK/backend:$WORK/pydeps"
PY="$HOME/loi_workspace/TPV-chatbot-btl/backend/.venv/bin/python"
# CHECK=nprefix: chỉ kiểm chuỗi N'' trên SQL Server (check_nprefix.py), không dựng slide.
if [ "${CHECK:-}" = nprefix ]; then exec "$PY" test_results/wf5_charts/check_nprefix.py; fi
# CHECK=stress: bộ thử áp lực đặt nhãn/vai trò cột (label_stress.py <out_dir> <so_lan>).
if [ "${CHECK:-}" = stress ]; then exec "$PY" test_results/wf5_charts/label_stress.py "$@"; fi
# CHECK=stress_deck: dựng slide cho các ca của một lượt stress (stress_deck.py <runs/stressN> [ids]).
if [ "${CHECK:-}" = stress_deck ]; then exec "$PY" test_results/wf5_charts/stress_deck.py "$@"; fi
exec "$PY" test_results/wf5_charts/eval_charts.py --questions "$HOME/loi_workspace/report_pipeline" "$@"
