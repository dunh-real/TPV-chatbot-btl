# syntax=docker/dockerfile:1
# Image backend TPV. Build context là GỐC repo (không phải backend/), vì
# app/main.py phục vụ giao diện từ `../frontend` cạnh thư mục backend.
#
#   cd backend && docker compose build backend
#
# Mọi thứ cần apt (driver ODBC cho SQL Server) cài TRONG image, máy chủ chỉ cần Docker.

FROM python:3.12-slim-bookworm

ARG UV_IMAGE=ghcr.io/astral-sh/uv:latest
# 0 = bỏ driver SQL Server (image nhẹ hơn ~20 MB, nhưng ERP_DATABASE_URL kiểu mssql sẽ không chạy)
ARG INSTALL_MSODBC=1
# Trùng UID của user trên máy chủ thì thư mục data/ mount vào ghi được, không vướng quyền
ARG APP_UID=1000

COPY --from=${UV_IMAGE} /uv /uvx /bin/

# unixodbc luôn cài: pyodbc nạp libodbc.so ngay lúc import, thiếu là ImportError
# kể cả khi không dùng ERP.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg unixodbc \
 && if [ "$INSTALL_MSODBC" = "1" ]; then \
      curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
        | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
   && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
        > /etc/apt/sources.list.d/mssql-release.list \
   && apt-get update \
   && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18; \
    fi \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PATH=/srv/backend/.venv/bin:$PATH

WORKDIR /srv/backend

# Tầng thư viện tách khỏi tầng code: sửa code không phải tải lại torch (~3 GB).
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=backend/uv.lock,target=uv.lock \
    --mount=type=bind,source=backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=backend/.python-version,target=.python-version \
    uv sync --locked --no-install-project --no-dev

COPY backend/ /srv/backend/
COPY frontend/ /srv/frontend/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Thư mục HF cache tạo sẵn với chủ là app: named volume mount vào sẽ kế thừa
# quyền này, nếu không thì volume thuộc root và tải model lần đầu bị từ chối.
RUN useradd --uid "$APP_UID" --create-home app \
 && mkdir -p /srv/backend/data /home/app/.cache/huggingface \
 && chown -R app:app /srv/backend/data /home/app/.cache

USER app
EXPOSE 8081

# /health trả 200 cả khi "degraded" (vLLM chưa lên): container vẫn coi là khoẻ,
# còn từng thành phần xem trong nội dung JSON.
HEALTHCHECK --interval=15s --timeout=10s --start-period=180s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/health', timeout=8)"

# Một worker: embedding + reranker nằm trong RAM/VRAM của tiến trình, nhân bản
# worker là nhân bản luôn model.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8081"]
