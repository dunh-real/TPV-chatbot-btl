#!/usr/bin/env bash
# Khởi động lại toàn bộ dịch vụ TPV trên máy đã cài (sau reboot, sau khi tmux bị tắt...).
# Dịch vụ nào đang chạy thì giữ nguyên; chỉ bật những gì chưa chạy.
#
#   ./scripts/start_services.sh                  # docker -> sqlserver -> vllm -> backend
#   ./scripts/start_services.sh sqlserver       # bật SQL Server và chờ cổng sẵn sàng
#   ./scripts/start_services.sh docker vllm      # chỉ các bước được nêu
#   ./scripts/start_services.sh check            # chỉ kiểm tra, không bật gì
#
#   docker   container Qdrant, Redis, Presenton: đang chạy -> bỏ qua; đã có nhưng dừng -> docker start;
#            chưa có -> pull image (Presenton: build từ backend/docker/presenton_vllm) rồi docker run.
#   vllm     tmux `vllm_vision`: vllm serve, tool call qua parser qwen3_coder.
#   backend  tmux `backend`: uvicorn có giám sát (scripts/run_backend.sh), chết thì tự chạy lại.
#            Giao diện (frontend/) do chính backend phục vụ ở http://127.0.0.1:8080/ui/.
#
# Cấu hình: đè bằng biến môi trường, hoặc ghi vào ~/.config/tpv/tpv.env (cùng file install.sh dùng).
# Khoá vLLM (VLLM_API_KEY) đọc từ ~/.config/tpv/secrets.env nếu có - không ghi vào script hay dòng lệnh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
TPV_CONFIG="${TPV_CONFIG:-$HOME/.config/tpv/tpv.env}"
TPV_SECRETS="${TPV_SECRETS:-$HOME/.config/tpv/secrets.env}"
# shellcheck disable=SC1090
[ -f "$TPV_CONFIG" ] && . "$TPV_CONFIG"
TPV_STATE_DIR="${TPV_STATE_DIR:-$HOME/.local/state/tpv}"

VLLM_TMUX="${VLLM_TMUX:-vllm_vision}"
BACKEND_TMUX="${BACKEND_TMUX:-backend}"

VLLM_MODEL="${VLLM_MODEL:-Qwen/Qwen3.6-35B-A3B-FP8}"
VLLM_SERVED_NAME="${VLLM_SERVED_NAME:-$VLLM_MODEL}"
VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-15000}"
VLLM_GPU_UTIL="${VLLM_GPU_UTIL:-0.7}"
VLLM_MAX_LEN="${VLLM_MAX_LEN:-262144}"
VLLM_WAIT_SECONDS="${VLLM_WAIT_SECONDS:-3600}"

BACKEND_PORT="${BACKEND_PORT:-8080}"
MSSQL_SERVICE="${MSSQL_SERVICE:-mssql-server}"
MSSQL_CHECK_HOST="${MSSQL_CHECK_HOST:-127.0.0.1}"
MSSQL_PORT="${MSSQL_PORT:-1433}"
MSSQL_WAIT_SECONDS="${MSSQL_WAIT_SECONDS:-60}"
# Supervisor của repo nếu có; máy production (~/loi_workspace) dùng script riêng chạy embedding trên CPU.
# Đặt BACKEND_RUN_SCRIPT để chọn script khác.
if [ -z "${BACKEND_RUN_SCRIPT:-}" ]; then
  for candidate in "$REPO_DIR/scripts/run_backend.sh" "$REPO_DIR/../run_tpv_backend.sh"; do
    if [ -f "$candidate" ]; then BACKEND_RUN_SCRIPT="$candidate"; break; fi
  done
fi
BACKEND_RUN_SCRIPT="${BACKEND_RUN_SCRIPT:-$REPO_DIR/scripts/run_backend.sh}"

QDRANT_PORT="${QDRANT_PORT:-6333}"
DOCKER_BIND_HOST="${DOCKER_BIND_HOST:-0.0.0.0}"
REDIS_PORT="${REDIS_PORT:-6382}"
PRESENTON_PORT="${PRESENTON_PORT:-5002}"
QDRANT_CONTAINER="${QDRANT_CONTAINER:-tpv-qdrant}"
REDIS_CONTAINER="${REDIS_CONTAINER:-tpv-redis}"
PRESENTON_CONTAINER="${PRESENTON_CONTAINER:-tpv-btl-presenton}"
QDRANT_IMAGE="${QDRANT_IMAGE:-qdrant/qdrant:v1.19.2}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
PRESENTON_IMAGE="${PRESENTON_IMAGE:-tpv-btl-presenton:qwen}"

ALL_STEPS=(docker sqlserver vllm backend check)
STEP=init
log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$STEP" "$*"; }
warn() { printf '\033[1;33m[%s] CẢNH BÁO:\033[0m %s\n' "$STEP" "$*" >&2; }
die()  { printf '\033[1;31m[%s] LỖI:\033[0m %s\n' "$STEP" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

secret() {   # secret TÊN - đọc trong subshell để bí mật không lọt ra môi trường của script
  [ -f "$TPV_SECRETS" ] || return 0
  (set -a; . "$TPV_SECRETS"; eval "printf '%s' \"\${$1:-}\"")
}
vllm_key() { printf '%s' "${VLLM_API_KEY:-$(secret VLLM_API_KEY)}"; }

port_busy() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }
tmux_running() { tmux has-session -t "=$1" 2>/dev/null; }

wait_http() {   # wait_http URL GIÂY [header]
  local url=$1 limit=$2 hdr=${3:-} t=0
  while (( t < limit )); do
    if curl -fsS -m 5 ${hdr:+-H "$hdr"} "$url" >/dev/null 2>&1; then return 0; fi
    sleep 10; t=$((t + 10))
  done
  return 1
}

DOCKER=(docker)
pick_docker() {
  have docker || die "chưa cài docker"
  if docker info >/dev/null 2>&1; then DOCKER=(docker); else DOCKER=(sudo docker); fi
}
container_exists()  { "${DOCKER[@]}" container inspect "$1" >/dev/null 2>&1; }
container_running() { [ "$("${DOCKER[@]}" container inspect -f '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
image_exists()      { "${DOCKER[@]}" image inspect "$1" >/dev/null 2>&1; }

# ensure_container TÊN CỔNG HÀM_TẠO
#   đang chạy -> bỏ qua | có nhưng dừng -> start | chưa có -> HÀM_TẠO (pull/build + run).
# Cổng đã bị dịch vụ khác chiếm thì không tạo container mới: backend sẽ dùng dịch vụ đang có.
ensure_container() {
  local name=$1 port=$2 create=$3
  if container_running "$name"; then
    log "$name đang chạy"
  elif container_exists "$name"; then
    "${DOCKER[@]}" start "$name" >/dev/null
    log "$name: đã start lại container có sẵn"
  elif port_busy "$port"; then
    warn "$name chưa có container nhưng cổng $port đã có dịch vụ khác - giữ nguyên"
  else
    "$create"
    log "$name: đã tạo container mới"
  fi
}

create_qdrant() {
  image_exists "$QDRANT_IMAGE" || "${DOCKER[@]}" pull "$QDRANT_IMAGE"
  mkdir -p "$BACKEND_DIR/qdrant_storage"
  # Bind theo cấu hình; mặc định nhận kết nối từ các máy khác.
  "${DOCKER[@]}" run -d --name "$QDRANT_CONTAINER" --restart unless-stopped \
    -p "$DOCKER_BIND_HOST:$QDRANT_PORT:6333" -v "$BACKEND_DIR/qdrant_storage:/qdrant/storage" \
    "$QDRANT_IMAGE" >/dev/null
}

create_redis() {
  image_exists "$REDIS_IMAGE" || "${DOCKER[@]}" pull "$REDIS_IMAGE"
  "${DOCKER[@]}" run -d --name "$REDIS_CONTAINER" --restart unless-stopped \
    -p "$DOCKER_BIND_HOST:$REDIS_PORT:6379" -v tpv-redis-data:/data \
    "$REDIS_IMAGE" redis-server --save 60 1 >/dev/null
}

create_presenton() {
  # Image Presenton là bản đã vá của dự án, không có trên registry: thiếu thì build.
  if ! image_exists "$PRESENTON_IMAGE"; then
    log "Build $PRESENTON_IMAGE (lần đầu kéo image gốc vài GB) ..."
    "${DOCKER[@]}" build -t "$PRESENTON_IMAGE" "$BACKEND_DIR/docker/presenton_vllm"
  fi
  # Publish theo DOCKER_BIND_HOST; Presenton hiện dùng DISABLE_AUTH=true.
  "${DOCKER[@]}" run -d --name "$PRESENTON_CONTAINER" --restart unless-stopped \
    -p "$DOCKER_BIND_HOST:$PRESENTON_PORT:80" --add-host host.docker.internal:host-gateway \
    -v tpv-btl-presenton-data:/app_data \
    -e LLM=custom -e "CUSTOM_LLM_URL=http://host.docker.internal:$VLLM_PORT/v1" \
    -e "CUSTOM_LLM_API_KEY=$(vllm_key)" -e "CUSTOM_MODEL=$VLLM_SERVED_NAME" \
    -e DISABLE_THINKING=true -e DISABLE_IMAGE_GENERATION=true -e CAN_CHANGE_KEYS=false -e DISABLE_AUTH=true \
    "$PRESENTON_IMAGE" >/dev/null
}

# -------------------------------------------------------------------- bước --
sqlserver_ready() {
  systemctl is-active --quiet "$MSSQL_SERVICE" &&
    timeout 3 bash -c 'exec 3<>/dev/tcp/"$1"/"$2"' _ "$MSSQL_CHECK_HOST" "$MSSQL_PORT"
}

step_sqlserver() {
  have systemctl || die "không có systemctl để quản lý SQL Server"
  have timeout || die "không có timeout để kiểm tra cổng SQL Server"
  [[ "$MSSQL_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]] || die "MSSQL_WAIT_SECONDS phải là số nguyên dương"
  if systemctl is-active --quiet "$MSSQL_SERVICE"; then
    log "$MSSQL_SERVICE đang chạy"
  else
    log "Khởi động $MSSQL_SERVICE ..."
    if [ "$EUID" -eq 0 ]; then
      systemctl start "$MSSQL_SERVICE" || die "không khởi động được $MSSQL_SERVICE"
    else
      sudo systemctl start "$MSSQL_SERVICE" || die "không khởi động được $MSSQL_SERVICE"
    fi
  fi
  local deadline=$((SECONDS + MSSQL_WAIT_SECONDS))
  log "Chờ SQL Server tại $MSSQL_CHECK_HOST:$MSSQL_PORT (tối đa ${MSSQL_WAIT_SECONDS}s) ..."
  while (( SECONDS < deadline )); do
    if sqlserver_ready >/dev/null 2>&1; then
      log "SQL Server sẵn sàng: service active, TCP $MSSQL_CHECK_HOST:$MSSQL_PORT nhận kết nối"
      return 0
    fi
    if systemctl is-failed --quiet "$MSSQL_SERVICE"; then
      die "$MSSQL_SERVICE bị lỗi - xem: sudo journalctl -u $MSSQL_SERVICE -n 50 --no-pager"
    fi
    sleep 2
  done
  die "SQL Server chưa sẵn sàng - xem: sudo journalctl -u $MSSQL_SERVICE -n 50 --no-pager"
}

step_docker() {
  pick_docker
  ensure_container "$QDRANT_CONTAINER" "$QDRANT_PORT" create_qdrant
  ensure_container "$REDIS_CONTAINER" "$REDIS_PORT" create_redis
  ensure_container "$PRESENTON_CONTAINER" "$PRESENTON_PORT" create_presenton
  wait_http "http://127.0.0.1:$QDRANT_PORT/healthz" 60 || warn "Qdrant chưa phản hồi ở cổng $QDRANT_PORT"
  wait_http "http://127.0.0.1:$PRESENTON_PORT/" 300 \
    || warn "Presenton chưa phản hồi ở cổng $PRESENTON_PORT (docker logs $PRESENTON_CONTAINER)"
}

find_vllm() {
  if [ -n "${VLLM_BIN:-}" ]; then printf '%s' "$VLLM_BIN"
  elif have vllm; then command -v vllm
  elif [ -x "$HOME/.venvs/vllm/bin/vllm" ]; then printf '%s' "$HOME/.venvs/vllm/bin/vllm"
  else return 1
  fi
}

step_vllm() {
  if tmux_running "$VLLM_TMUX"; then
    log "tmux $VLLM_TMUX đang chạy"
  elif port_busy "$VLLM_PORT"; then
    warn "cổng $VLLM_PORT đã có dịch vụ khác (vLLM trong tmux khác?) - không khởi động thêm"
  else
    local bin; bin=$(find_vllm) || die "không tìm thấy lệnh vllm - đặt VLLM_BIN=/duong/dan/vllm"
    mkdir -p "$TPV_STATE_DIR"
    local log_file="$TPV_STATE_DIR/vllm_$(date +%Y%m%d_%H%M%S).log"
    # Lệnh viết ra file riêng (quyền 700) để tmux chạy được mà khoá API không nằm trên dòng lệnh:
    # vllm tự đọc biến môi trường VLLM_API_KEY.
    # qwen3_coder: Qwen3.6 gọi tool bằng XML (<function=...>), parser hermes trả tool_calls rỗng.
    # VLLM_USE_DEEP_GEMM=0: tránh kernel DeepGEMM lỗi trên Blackwell.
    cat > "$TPV_STATE_DIR/run_vllm_vision.sh" <<EOF
#!/usr/bin/env bash
[ -f "$TPV_SECRETS" ] && { set -a; . "$TPV_SECRETS"; set +a; }
export VLLM_USE_DEEP_GEMM=0
exec "$bin" serve "$VLLM_MODEL" \\
  --served-model-name "$VLLM_SERVED_NAME" \\
  --host "$VLLM_HOST" --port "$VLLM_PORT" \\
  --max-model-len "$VLLM_MAX_LEN" --gpu-memory-utilization "$VLLM_GPU_UTIL" \\
  --limit-mm-per-prompt '{"image": 2}' --max-num-batched-tokens 16384 --max-num-seqs 16 \\
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \\
  --speculative-config '{"method": "qwen3_5_mtp", "num_speculative_tokens": 2}'
EOF
    chmod 700 "$TPV_STATE_DIR/run_vllm_vision.sh"
    tmux new-session -d -s "$VLLM_TMUX" "bash $TPV_STATE_DIR/run_vllm_vision.sh 2>&1 | tee $log_file"
    log "vLLM khởi động trong tmux $VLLM_TMUX, log: $log_file"
  fi
  local key; key=$(vllm_key)
  log "Chờ vLLM trả lời /v1/models (tối đa ${VLLM_WAIT_SECONDS}s) ..."
  wait_http "http://127.0.0.1:$VLLM_PORT/v1/models" "$VLLM_WAIT_SECONDS" ${key:+"Authorization: Bearer $key"} \
    || die "vLLM chưa lên - xem: tmux attach -t $VLLM_TMUX"
  log "vLLM :$VLLM_PORT sẵn sàng (model $VLLM_SERVED_NAME)"
}

backend_token() { grep -E '^PUBLIC_ACCESS_TOKEN=' "$BACKEND_DIR/.env" 2>/dev/null | cut -d= -f2- || true; }

step_backend() {
  if tmux_running "$BACKEND_TMUX"; then
    log "tmux $BACKEND_TMUX đang chạy"
  elif port_busy "$BACKEND_PORT"; then
    warn "cổng $BACKEND_PORT đã có backend khác (tmux tpv_backend?) - không khởi động thêm"
  else
    [ -f "$BACKEND_RUN_SCRIPT" ] || die "không có $BACKEND_RUN_SCRIPT"
    tmux new-session -d -s "$BACKEND_TMUX" -c "$BACKEND_DIR" \
      "TPV_CONFIG=$TPV_CONFIG TPV_SECRETS=$TPV_SECRETS bash $BACKEND_RUN_SCRIPT"
    log "backend khởi động trong tmux $BACKEND_TMUX, log: $BACKEND_DIR/backend.log"
  fi
  local token; token=$(backend_token)
  log "Chờ backend (nạp embedding + reranker) ..."
  wait_http "http://127.0.0.1:$BACKEND_PORT/health" 600 ${token:+"X-Access-Token: $token"} \
    || die "backend chưa lên - xem $BACKEND_DIR/backend.log"
  log "backend :$BACKEND_PORT sẵn sàng"

  # Giao diện không có tiến trình riêng: backend mount thư mục frontend/ ở /ui (app/main.py).
  [ -d "$REPO_DIR/frontend" ] || warn "không có thư mục $REPO_DIR/frontend - backend sẽ bỏ qua /ui"
  wait_http "http://127.0.0.1:$BACKEND_PORT/ui/" 60 ${token:+"X-Access-Token: $token"} \
    || die "backend chạy nhưng /ui/ không trả về giao diện - xem $BACKEND_DIR/backend.log"
  log "giao diện local: http://127.0.0.1:$BACKEND_PORT/ui/ ; máy khác: http://<IP-máy-chủ>:$BACKEND_PORT/ui/ (khi backend bind 0.0.0.0)"
}

step_check() {
  local ok=0 fail=0 key token
  key=$(vllm_key); token=$(backend_token)
  chk() {
    if "${@:2}" >/dev/null 2>&1; then log "OK    $1"; ok=$((ok + 1)); else warn "LỖI  $1"; fail=$((fail + 1)); fi
  }
  chk "vLLM :$VLLM_PORT"           curl -fsS -m 10 ${key:+-H "Authorization: Bearer $key"} "http://127.0.0.1:$VLLM_PORT/v1/models"
  chk "Qdrant :$QDRANT_PORT"       curl -fsS -m 5 "http://127.0.0.1:$QDRANT_PORT/healthz"
  chk "Redis :$REDIS_PORT"         bash -c "exec 3<>/dev/tcp/127.0.0.1/$REDIS_PORT"
  chk "SQL Server $MSSQL_CHECK_HOST:$MSSQL_PORT (service + TCP)" sqlserver_ready
  chk "Presenton :$PRESENTON_PORT" curl -fsS -m 10 "http://127.0.0.1:$PRESENTON_PORT/"
  chk "Backend :$BACKEND_PORT"     curl -fsS -m 10 ${token:+-H "X-Access-Token: $token"} "http://127.0.0.1:$BACKEND_PORT/health"
  chk "Giao diện /ui/"             curl -fsS -m 10 ${token:+-H "X-Access-Token: $token"} "http://127.0.0.1:$BACKEND_PORT/ui/"
  log "$ok đạt, $fail lỗi. tmux: $(tmux ls -F '#S' 2>/dev/null | tr '\n' ' ')"
  (( fail == 0 ))
}

# ------------------------------------------------------------------- main --
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,16p' "$0"; exit 0
fi
have tmux || die "chưa cài tmux"
have curl || die "chưa cài curl"
steps=("$@"); [ ${#steps[@]} -gt 0 ] || steps=("${ALL_STEPS[@]}")
for s in "${steps[@]}"; do
  [[ " ${ALL_STEPS[*]} " == *" $s "* ]] || die "không có bước '$s' (chọn trong: ${ALL_STEPS[*]})"
done
for s in "${steps[@]}"; do
  STEP=$s
  "step_$s"
done
