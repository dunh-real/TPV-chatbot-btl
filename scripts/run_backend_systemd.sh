#!/usr/bin/env bash
set -euo pipefail

cd /home/abc/kien_workspace/TPV-chatbot-btl/backend

export HOME=/home/abc
export PATH="/home/abc/kien_workspace/TPV-chatbot-btl/backend/.venv/bin:/home/abc/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Máy hiện chỉ nhận diện GPU cuda:0.
export EMBEDDING_DEVICE=cuda:0
export RERANKER_DEVICE=cuda:0

# Các cấu hình LLM, Qdrant, Redis, Presenton và secrets
# được Pydantic Settings đọc từ backend/.env.
exec /home/abc/kien_workspace/TPV-chatbot-btl/backend/.venv/bin/uvicorn \
    app.main:app \
    --host 0.0.0.0 \
    --port 8080
