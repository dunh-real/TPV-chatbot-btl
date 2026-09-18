#!/usr/bin/env bash
# Khởi động vLLM cho backend TPV - một GPU, Qwen3.6-35B-A3B.
#
# Vì sao chọn cấu hình này:
#   - Qwen3.6-35B-A3B là MoE 35B nhưng chỉ 3B tham số hoạt động mỗi token
#     => nhanh như model 3B, chất lượng gần model 35B.
#   - Bản FP8 chính thức nặng 37,5 GB (bản BF16 là 71,9 GB) - vừa đủ chỗ trống
#     trên đĩa và chừa nhiều VRAM cho KV cache.
#   - Model có sẵn vision encoder, nên CHÍNH server này cũng làm luôn OCR cho
#     workflow 2, không cần dựng server thứ hai trên card còn lại.
#   - 40 lớp nhưng chỉ 10 lớp dùng full attention (30 lớp còn lại là linear
#     attention), nên KV cache chỉ tốn ~20 KB/token: context dài rất rẻ.

set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen3.6-35B-A3B}"
SERVED_NAME="${SERVED_NAME:-qwen3.6-35b}"
PORT="${PORT:-8000}"
GPU="${GPU:-0}"

# 0.90 × 96 GB ≈ 86 GB: 37,5 GB trọng số + ~5 GB đệm => còn ~44 GB cho KV cache,
# tương đương khoảng 2,2 triệu token. Hạ xuống 0.85 nếu chạy chung việc khác.
UTIL="${UTIL:-0.90}"

# Mở hết context của model. KV cache chỉ tốn ~20 KB/token (chỉ 10/40 lớp dùng
# full attention), nên 44 GB KV vẫn chứa được ~8 hội thoại dài 262K token cùng lúc.
MAX_LEN="${MAX_LEN:-262144}"

# Demo chỉ vài người dùng. Con số này vẫn phải đủ cho phần song song BÊN TRONG
# một request: workflow 2 soát văn bản theo nhiều lô, workflow 3-4 viết nhiều mục
# cùng lúc. 16 là thoải mái; hạ xuống nữa thì các lô phải xếp hàng.
MAX_SEQS="${MAX_SEQS:-16}"

# Mặc định của vLLM là 2048 - với context 262K thì phải chia 128 chunk để nạp
# prompt, rất chậm. Nâng lên cho bước prefill đỡ lê thê.
MAX_BATCHED="${MAX_BATCHED:-16384}"

export CUDA_VISIBLE_DEVICES="${GPU}"
export VLLM_WORKER_MULTIPROC_METHOD=spawn

echo "Model:      ${MODEL}"
echo "GPU:        ${GPU} (1 card)"
echo "Context:    ${MAX_LEN} token | tối đa ${MAX_SEQS} request song song"
echo "Endpoint:   http://localhost:${PORT}/v1  (tên gọi: ${SERVED_NAME})"
echo

exec vllm serve "${MODEL}" \
    --served-model-name "${SERVED_NAME}" \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --tensor-parallel-size 1 \
    --gpu-memory-utilization "${UTIL}" \
    --max-model-len "${MAX_LEN}" \
    --max-num-seqs "${MAX_SEQS}" \
    --max-num-batched-tokens "${MAX_BATCHED}" \
    --limit-mm-per-prompt '{"image": 2}' \
    --speculative-config '{"method": "qwen3_5_mtp", "num_speculative_tokens": 2}' \
    "$@"
