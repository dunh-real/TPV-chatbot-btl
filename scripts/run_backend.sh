#!/usr/bin/env bash
# Giám sát backend TPV: chạy uvicorn, chết thì chạy lại sau 5 giây (giống run_tpv_backend.sh trên production).
# install.sh chạy script này trong tmux `tpv_backend`. Dừng hẳn:
#   kill "$(cat "$TPV_STATE_DIR/backend_supervisor.pid")"; rồi dừng uvicorn theo cổng.
#
# Cấu hình không nằm trong script:
#   $TPV_CONFIG  (mặc định ~/.config/tpv/tpv.env)      cổng, thiết bị GPU, model
#   $TPV_SECRETS (mặc định ~/.config/tpv/secrets.env)  mật khẩu SQL Server, khoá vLLM - quyền 600
# Biến môi trường đè lên backend/.env (pydantic-settings ưu tiên env), nên mật khẩu không phải ghi vào .env.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TPV_CONFIG="${TPV_CONFIG:-$HOME/.config/tpv/tpv.env}"
TPV_SECRETS="${TPV_SECRETS:-$HOME/.config/tpv/secrets.env}"
[ -f "$TPV_CONFIG" ] && . "$TPV_CONFIG"
TPV_STATE_DIR="${TPV_STATE_DIR:-$HOME/.local/state/tpv}"
mkdir -p "$TPV_STATE_DIR"

cd "$REPO_DIR/backend" || exit 1
export PATH="$HOME/.local/bin:$PATH"

VLLM_PORT="${VLLM_PORT:-15000}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-Qwen/Qwen3.6-35B-A3B-FP8}"
export LLM_BASE_URL="http://127.0.0.1:${VLLM_PORT}/v1" LLM_MODEL="$VLLM_SERVED_NAME"
export OCR_BASE_URL="$LLM_BASE_URL" OCR_MODEL="$VLLM_SERVED_NAME"
export ERP_TEXT2SQL_BASE_URL="$LLM_BASE_URL" ERP_TEXT2SQL_MODEL="$VLLM_SERVED_NAME"
export QDRANT_URL="http://127.0.0.1:${QDRANT_PORT:-6333}" REDIS_URL="redis://127.0.0.1:${REDIS_PORT:-6382}/0"
export PRESENTON_URL="http://127.0.0.1:${PRESENTON_PORT:-5002}"
export EMBEDDING_DEVICE="${EMBEDDING_DEVICE:-cuda:0}" RERANKER_DEVICE="${RERANKER_DEVICE:-cuda:0}"
export WARMUP_MODELS="${WARMUP_MODELS:-true}"

# Khoá vLLM và chuỗi kết nối ERP dựng trong subshell: mật khẩu không bị export ra ngoài, không in ra log.
if [ -f "$TPV_SECRETS" ]; then
  key=$(set -a; . "$TPV_SECRETS"; printf '%s' "${VLLM_API_KEY:-}")
  if [ -n "$key" ]; then
    export LLM_API_KEY="$key" OCR_API_KEY="$key" ERP_TEXT2SQL_API_KEY="$key"
  fi
  ERP_DATABASE_URL=$(set -a; . "$TPV_SECRETS"; [ -n "${MSSQL_RO_PASSWORD:-}" ] && python3 -c 'import os, urllib.parse as u
print("mssql+aioodbc://%s:%s@%s:%s/%s?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes&Encrypt=yes" % (
    u.quote_plus(os.environ.get("MSSQL_RO_USER", "tpv_reader")), u.quote_plus(os.environ["MSSQL_RO_PASSWORD"]),
    os.environ.get("MSSQL_HOST", "127.0.0.1"), os.environ.get("MSSQL_PORT", "1433"),
    u.quote(os.environ.get("MSSQL_DATABASE", "bqp-private-ai-assistant"))))')
  [ -n "$ERP_DATABASE_URL" ] && export ERP_DATABASE_URL
  unset key
fi

echo $$ > "$TPV_STATE_DIR/backend_supervisor.pid"
while true; do
  echo "$(date "+%F %T") khởi động uvicorn" >> supervisor.log
  uv run --locked uvicorn app.main:app --host "${BACKEND_HOST:-127.0.0.1}" --port "${BACKEND_PORT:-8080}" >> backend.log 2>&1
  code=$?
  echo "$(date "+%F %T") uvicorn thoát, mã $code - chạy lại sau 5 giây" >> supervisor.log
  sleep 5
done
