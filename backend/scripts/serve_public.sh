#!/usr/bin/env bash
# Mở backend + giao diện ra Internet qua Cloudflare named tunnel (domain cố định).
#
# Khác quick tunnel ở chỗ: URL KHÔNG đổi khi chạy lại, và bật được Cloudflare
# Access để chặn người lạ. Đổi lại phải khai báo route bên dashboard trước.
#
#   ./scripts/serve_public.sh            # chạy nền, thoát terminal vẫn sống
#   ./scripts/serve_public.sh --fg       # chạy trước mặt, Ctrl+C để đóng
#   ./scripts/serve_public.sh --stop     # đóng đường public (backend chạy tiếp)
#
# Token đọc từ backend/.env (CLOUDFLARE_TUNNEL_TOKEN) - .env đã nằm trong
# .gitignore nên token không đi theo repo. Token này là CREDENTIAL: ai có nó thì
# chạy được tunnel của bạn, đừng dán vào chat/issue/ảnh chụp màn hình.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-8081}"
LOG="${LOG:-/tmp/cloudflared-tpv.log}"
PIDFILE="${PIDFILE:-/tmp/cloudflared-tpv.pid}"
HOSTNAME_PUBLIC="${PUBLIC_HOSTNAME:-}"

# --- dừng ------------------------------------------------------------------ #
if [ "${1:-}" = "--stop" ]; then
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    kill "$(cat "$PIDFILE")" && rm -f "$PIDFILE"
    echo "Đã đóng đường public."
  else
    echo "Không thấy tunnel nào đang chạy từ script này."
  fi
  exit 0
fi

# --- cấu hình -------------------------------------------------------------- #
# Chỉ đọc hai khoá cần dùng, không `source .env`: file đó có mật khẩu CSDL và
# những dòng có dấu cách sẽ làm `source` nổ.
read_env() {
  [ -f "$ROOT/.env" ] || return 0
  sed -n "s/^[[:space:]]*$1=//p" "$ROOT/.env" | tail -1 | tr -d '"'"'"'\r'
}

TOKEN="${CLOUDFLARE_TUNNEL_TOKEN:-$(read_env CLOUDFLARE_TUNNEL_TOKEN)}"
HOSTNAME_PUBLIC="${HOSTNAME_PUBLIC:-$(read_env PUBLIC_HOSTNAME)}"

if [ -z "$TOKEN" ]; then
  cat <<'EOF'
Chưa có CLOUDFLARE_TUNNEL_TOKEN trong backend/.env.

Lấy token: Cloudflare Zero Trust → Networks → Tunnels → chọn tunnel → Configure
→ tab Docker/Linux, token là chuỗi sau `--token`. Rồi thêm vào backend/.env:

  CLOUDFLARE_TUNNEL_TOKEN=eyJhIjoi...
  PUBLIC_HOSTNAME=chatbot-demo.tpvtech.vn
EOF
  exit 1
fi

command -v cloudflared >/dev/null 2>&1 || {
  echo "Chưa có cloudflared. Cài:"
  echo "  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \\"
  echo "    -o /tmp/cloudflared && sudo install /tmp/cloudflared /usr/local/bin/cloudflared"
  exit 1
}

if ! curl -sf -m 5 "http://localhost:${PORT}/health" >/dev/null; then
  echo "Backend chưa trả lời ở cổng ${PORT}. Mở một terminal khác và chạy:"
  echo "  cd backend && uv run uvicorn app.main:app --port ${PORT} --host 127.0.0.1"
  exit 1
fi

# Một tunnel là đủ: chạy hai tiến trình cùng token thì Cloudflare chia tải ngẫu
# nhiên giữa chúng, gỡ lỗi lúc đó rất khó chịu.
if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Tunnel đã chạy sẵn (PID $(cat "$PIDFILE")). Dừng bằng: $0 --stop"
  exit 0
fi

echo "Backend ở cổng ${PORT} đang sống. Đang nối tunnel…"

# --- chạy ------------------------------------------------------------------ #
if [ "${1:-}" = "--fg" ]; then
  exec cloudflared tunnel --no-autoupdate run --token "$TOKEN"
fi

: > "$LOG"
setsid nohup cloudflared tunnel --no-autoupdate run --token "$TOKEN" \
  >>"$LOG" 2>&1 < /dev/null &
echo $! > "$PIDFILE"

# Chờ đăng ký xong ít nhất một kết nối tới biên Cloudflare.
for _ in $(seq 1 30); do
  grep -q "Registered tunnel connection" "$LOG" && break
  sleep 1
done

if ! grep -q "Registered tunnel connection" "$LOG"; then
  echo "Chưa nối được sau 30 giây. Xem log: $LOG"
  exit 1
fi

URL="https://${HOSTNAME_PUBLIC:-<hostname đã khai trong dashboard>}"
cat <<EOF

  ┌──────────────────────────────────────────────────────────────┐
     Giao diện :  ${URL}/ui/
     API       :  ${URL}/api/...
     Health    :  ${URL}/health
     Swagger   :  ${URL}/docs
  └──────────────────────────────────────────────────────────────┘

  URL cố định, không đổi khi chạy lại. PID $(cat "$PIDFILE") · log ${LOG}
  Đóng bằng: $0 --stop

EOF

# ---------------------------------------------------------------------------
# PHẢI KHỚP VỚI PUBLIC HOSTNAME BÊN DASHBOARD
#
#   Path    : ĐỂ TRỐNG. Điền gì vào đây là chỉ những path khớp mới đi qua -
#             `^/blog` thì /ui/, /api/, /health đều 404.
#   Service : HTTP → localhost:8081   (đúng cổng uvicorn đang nghe; cổng 80/81
#             là của dự án khác trên cùng máy - nginx giữ 80, và 81 là cổng
#             đặc quyền nên user thường không bind được)
#
# Sửa hai ô này rồi Save là ăn ngay, không phải khởi động lại tunnel.
#
# CHẠY BẰNG DOCKER thì `localhost` là chính container, không phải máy host:
#
#   docker run -d --name tpv-tunnel --restart unless-stopped --network host \
#     cloudflare/cloudflared:latest tunnel --no-autoupdate run --token <token>
#
# Thiếu `--network host` thì tunnel nối được lên Cloudflare nhưng mọi request
# trả 502: container không thấy 127.0.0.1:8081 của host.
#
# CHẠY NỀN VĨNH VIỄN (tự bật lại sau reboot):
#
#   sudo cloudflared service install <token>
#   sudo systemctl status cloudflared
#
# CHẶN NGƯỜI LẠ: domain cố định thì ai biết URL cũng gọi được API, kể cả
# DELETE /api/documents/{id}. Hai cách, dùng một là đủ:
#   - Cloudflare Access (Zero Trust → Access → Applications): chặn ngay ở biên,
#     không tốn request nào tới máy mình.
#   - PUBLIC_ACCESS_TOKEN trong .env: chốt ở tầng ứng dụng, vào bằng
#     https://.../ui/?token=<token> một lần rồi cookie lo phần còn lại.
# ---------------------------------------------------------------------------
