# Triển khai TPV offline bằng systemd

Ngày lập: 13/10/2026. Đây là hướng dẫn chuẩn bị và mẫu cấu hình, **chưa phải bộ
cài đã nghiệm thu trên máy khách**. Danh sách việc cần làm: [OFFLINE-TODO.md](OFFLINE-TODO.md).

## 1. Phạm vi và kiến trúc

Giả định máy Linux có systemd, GPU NVIDIA tương thích model và vẫn có mạng LAN
đến SQL Server. Offline nghĩa là không cần Internet, không phải cắt mạng nội bộ.
Chốt bản Linux/CPU/GPU/Python của khách trước khi tải bộ cài.

```text
Trình duyệt trong LAN → backend systemd → vLLM systemd (Qwen local)
                         ├─ embedding/reranker local
                         ├─ Qdrant + Redis: container đã nạp sẵn hoặc service native
                         ├─ SQL Server ERP trong LAN
                         └─ file local; Presenton/MinIO nội bộ nếu thực sự dùng
```

- Backend và vLLM chạy trực tiếp bằng systemd, không dùng tmux/supervisor vòng lặp.
- Có thể giữ Docker cho Qdrant/Redis/Presenton/MinIO; offline không bắt buộc bỏ Docker.
- Nếu khách cấm Docker hoàn toàn, phải đóng gói và kiểm thử thêm binary/service
  Qdrant, Redis, MinIO và môi trường Presenton riêng. Hai service mẫu dưới đây
  **chưa thay thế** các thành phần đó.
- Repo hiện lưu upload/output bằng `app/services/storage.py` trên filesystem;
  mới có cấu hình MinIO, chưa thấy lời gọi SDK MinIO trong `backend/app`.
  Xác nhận bản tích hợp của khách trước khi coi MinIO là bắt buộc.

## 2. Những khác biệt cần xử lý trong repo

| Hiện trạng | Cần làm khi chuyển offline |
|---|---|
| `scripts/start_services.sh` có thể pull/build image và chạy tmux | Không dùng làm service production offline |
| `scripts/run_backend.sh` dùng `uv run --locked` | Service gọi thẳng Python đã cài dependency, không resolve/download lúc boot |
| Compose dùng tên DNS `qdrant`, `redis`, `vllm` | Backend trên host dùng địa chỉ host/LAN và cổng publish thực tế |
| Compose chưa publish cổng Redis ra host | Bổ sung mapping loopback, ví dụ `127.0.0.1:6382:6379` |
| `serve_vllm.sh` và script vận hành có model/parser mặc định khác nhau | Chốt một model + served name + parser đã thử thành công |
| `uv.lock` chỉ quản lý backend; không có dependency vLLM trong `pyproject.toml` | Tạo lock/wheelhouse vLLM riêng |
| Embedding gọi thêm `hf_hub_download` cho sparse head | Mang theo cache `BAAI/bge-m3/sparse_linear.pt`, không chỉ hai model tiếng Việt |
| `.doc` mặc định chuyển bằng Docker LibreOffice | Cài LibreOffice native và đặt `DOC_CONVERT_IMAGE=` nếu bỏ container này |
| Quyền mở rộng LH gắn `64:153`; nhóm gắn role Id của DB thử | Rà soát lại tenant/user/role trên DB khách; không sao chép ngoại lệ LH mặc định |

## 3. Bộ bàn giao phải tải trước

Tải/build trên máy Linux cùng kiến trúc, Python và nền hệ điều hành với máy khách;
không chép `.venv` từ Mac sang Linux. Lưu version, model revision và SHA256.

```text
tpv-offline/
  source/                         # backend + frontend + config + uv.lock, commit đã chốt
  requirements-backend.txt
  requirements-vllm.txt
  wheels/backend/                 # tất cả wheel backend, kể cả Torch/CUDA phụ thuộc
  wheels/vllm/                    # tất cả wheel của stack vLLM đã nghiệm thu
  wheels/bootstrap/              # pip, setuptools, wheel
  models/qwen/                   # trọng số, index, tokenizer, config, processor/chat template
  models/embedding/
  models/reranker/
  hf-cache/                      # cache sparse_linear.pt, refs/snapshots/blobs đầy đủ
  packages/                      # gói OS/driver + dependency, uv binary nếu dùng
  images/                        # image tar nếu giữ Docker
  assets/                        # font, mẫu DOCX/PPTX và tài nguyên render
  manifest.json                  # commit, versions, model revisions, GPU/OS tham chiếu
  SHA256SUMS
```

### Gói hệ thống và công cụ

- Python 3.12 + venv, pip; uv binary dùng ở bước chuẩn bị nếu cần.
- Driver NVIDIA phù hợp GPU và stack Torch/vLLM đã chọn; không suy ra CUDA runtime
  chỉ từ dòng `CUDA Version` trong `nvidia-smi`.
- `unixodbc`, **Microsoft ODBC Driver 18 for SQL Server**, CA/certificate nội bộ.
- `curl`, `rsync`, `tar`, `unzip`, `fontconfig`, công cụ SHA256; `git` nếu cần quản lý source.
- LibreOffice nếu xử lý `.doc`; font DejaVu/Noto và font theo mẫu khách có quyền sử dụng.
- Docker Engine + Compose plugin nếu giữ container; NVIDIA Container Toolkit
  chỉ cần nếu thành phần GPU vẫn chạy trong container.
- Compiler/build tools khi dependency cần build wheel hoặc CUDA kernel JIT;
  thử trên GPU cùng loại để phát hiện nhu cầu tải thêm kernel/toolchain.

Trên Ubuntu/Debian: chuẩn bị `.deb` và **toàn bộ dependency** bằng máy/VM sạch
cùng bản OS; cài thử khi ngắt Internet. Không coi `apt download` vài gói chính
là đủ. Driver phải phù hợp kernel/Secure Boot của khách. Windows/RHEL cần bộ cài
khác; mẫu này không áp dụng trực tiếp.

Nguồn tải chính thức, chọn đúng version/platform đã chốt rồi lưu file vào bundle:

| Thành phần | Nguồn |
|---|---|
| uv binary | [Release assets](https://github.com/astral-sh/uv/releases) |
| Driver GPU | [NVIDIA Drivers](https://www.nvidia.com/en-us/drivers/) |
| ODBC SQL Server | [Microsoft packages và hướng dẫn Linux](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server) |
| Qdrant native | [Release assets](https://github.com/qdrant/qdrant/releases) |
| Redis native | [Hướng dẫn tải/build chính thức](https://redis.io/docs/latest/operate/oss_and_stack/install/archive/install-redis/install-redis-from-source/) |
| MinIO nếu dùng | [Repository chính thức](https://github.com/minio/minio); xác nhận phương thức phân phối/build và license của bản đã chọn |

Link dành cho máy chuẩn bị có Internet; máy khách dùng các file đã tải, không chạy
installer qua `curl | sh` hoặc tải `latest` tại chỗ.

### Python wheelhouse

Thực hiện trên máy chuẩn bị có Internet, trong thư mục `backend/`:

```bash
uv export --locked --no-dev --no-emit-project --no-hashes \
  --format requirements-txt --output-file /mnt/tpv-offline/requirements-backend.txt

python3.12 -m venv /tmp/tpv-wheel-builder
/tmp/tpv-wheel-builder/bin/python -m pip install --upgrade pip wheel
/tmp/tpv-wheel-builder/bin/python -m pip wheel \
  --extra-index-url https://download.pytorch.org/whl/cu128 \
  --wheel-dir /mnt/tpv-offline/wheels/backend \
  -r /mnt/tpv-offline/requirements-backend.txt

/tmp/tpv-wheel-builder/bin/python -m pip download --only-binary=:all: \
  --dest /mnt/tpv-offline/wheels/bootstrap pip setuptools wheel
```

Backend hiện dùng nguồn PyTorch cu128 trong `pyproject.toml`. Phải kiểm tra wheel
đã tải khớp lock và cài được trên máy đích; không tự đổi CUDA để hết lỗi download.
Nếu build từ source, giữ wheel đã build, không mang source tarball rồi build offline.

vLLM dùng **venv riêng**: cài đúng phiên bản đang chạy thành công với Qwen FP8 trên
máy staging, chạy tool-calling/OCR rồi xuất lock của môi trường đó:

```bash
# Thay đường dẫn bằng venv vLLM đã nghiệm thu, không dùng venv backend.
/opt/tpv/venvs/vllm/bin/python -m pip freeze \
  > /mnt/tpv-offline/requirements-vllm.txt
/opt/tpv/venvs/vllm/bin/python -m pip wheel \
  --wheel-dir /mnt/tpv-offline/wheels/vllm \
  -r /mnt/tpv-offline/requirements-vllm.txt
```

Ghi lại custom index/direct wheel của Torch/vLLM nếu có; `pip freeze` không lưu
cấu hình index. Loại đường dẫn editable cục bộ khỏi lock hoặc đóng wheel cho chúng.
Tải thêm wheel từ đúng nguồn đã dùng nếu lệnh trên không tìm được bản CUDA tương ứng.
Sau đó thử cài mới bằng `--no-index`, không chỉ kiểm tra môi trường đang có.

### Model và sparse head

Chốt Qwen `Qwen/Qwen3.6-35B-A3B-FP8` nếu tiếp tục dùng model đã thử;
không trộn với các model mặc định khác trong cấu hình repo.
Ví dụ tải bằng `huggingface_hub` trong môi trường chuẩn bị đã cài thư viện:

```python
import json
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download, hf_hub_download

root = Path("/mnt/tpv-offline")
api = HfApi()
revisions = {}
for name, repo in {
    "qwen": "Qwen/Qwen3.6-35B-A3B-FP8",
    "embedding": "AITeamVN/Vietnamese_Embedding",
    "reranker": "AITeamVN/Vietnamese_Reranker",
}.items():
    revision = api.model_info(repo).sha
    snapshot_download(repo_id=repo, revision=revision,
                      local_dir=root / "models" / name)
    revisions[repo] = revision

# Code hiện gọi repo mặc định, cần cache có refs/main; giữ nguyên cấu trúc cache.
sparse = hf_hub_download(repo_id="BAAI/bge-m3", filename="sparse_linear.pt",
                        cache_dir=root / "hf-cache" / "hub")
revisions["BAAI/bge-m3:sparse_linear.pt"] = Path(sparse).parent.name
(root / "model-revisions.json").write_text(json.dumps(revisions, indent=2))
```

Nếu OCR dùng server/model riêng, tải thêm model OCR đầy đủ và tạo thêm service;
chỉ dùng chung Qwen khi bản vLLM/model đã thử cả ảnh và tool-calling.
Giữ tokenizer, processor, config, index và mọi shard; không chỉ chép `.safetensors`.

### Nếu tiếp tục giữ Docker

Build/pull trên máy chuẩn bị, đóng tag/digest; không dùng `latest` khi bàn giao.
Với image đã kiểm thử, thay `IMAGE:TAG` bằng tên thật:

```bash
docker image save -o /mnt/tpv-offline/images/service.tar IMAGE:TAG
# Máy khách:
docker image load -i /mnt/tpv-offline/images/service.tar
```

Chuẩn bị Qdrant, Redis, Presenton tùy chọn và MinIO nếu có dùng thật. Dùng
`docker compose up -d --no-build --pull never` với **compose riêng cho hạ tầng**,
không bật backend/vLLM Docker trùng với systemd. Image tar không chứa volume/data:
snapshot Qdrant, dữ liệu Redis, MinIO và Presenton cần backup/restore riêng.

Sau khi bundle hoàn tất, tạo và kiểm tra checksum trên Linux:

```bash
cd /mnt/tpv-offline
find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS
sha256sum -c SHA256SUMS
```

Chạy lại bước kiểm tra sau khi chép sang máy khách. Manifest cần ghi commit source,
OS/CPU/GPU, Python/driver, phiên bản dependency/image, model revisions, parser và
tham số context/concurrency đã nghiệm thu. Symlink trong HF cache phải được giữ
đúng, không tạo liên kết trỏ về máy chuẩn bị.

## 4. Bố trí máy khách

```text
/opt/tpv/app/                    # source, chứa backend/ và frontend/
/opt/tpv/venvs/backend/
/opt/tpv/venvs/vllm/
/opt/tpv/models/{qwen,embedding,reranker}/
/opt/tpv/hf-cache/               # cache sparse đã tải
/opt/tpv/cache/                  # cache vLLM/matplotlib, user tpv có quyền ghi
/opt/tpv/app/backend/data/       # uploads/output, app DB, schema snapshot
/etc/tpv/backend.env            # cấu hình và secrets thật, không commit
/etc/tpv/model.env
```

Tạo user hệ thống `tpv`, cấp quyền đọc source/model, quyền ghi `data/`, cache và
HF cache; cấp quyền thiết bị GPU phù hợp OS. Secrets đặt `root:tpv`, mode `640`.
Giữ đủ dung lượng cho model, wheelhouse, cache biên dịch, dữ liệu và một bản rollback.
VRAM phải đo trên GPU khách với context/concurrency dự kiến, không lấy số tham số
hoạt động của MoE làm dung lượng trọng số cần nạp.

Cài Python offline sau khi đã cài đủ gói OS, ví dụ bundle ở `/mnt/tpv-offline`:

```bash
python3.12 -m venv /opt/tpv/venvs/backend
python3.12 -m venv /opt/tpv/venvs/vllm
/opt/tpv/venvs/backend/bin/python -m pip install --no-index \
  --find-links=/mnt/tpv-offline/wheels/bootstrap pip setuptools wheel
/opt/tpv/venvs/vllm/bin/python -m pip install --no-index \
  --find-links=/mnt/tpv-offline/wheels/bootstrap pip setuptools wheel
/opt/tpv/venvs/backend/bin/python -m pip install --no-index \
  --find-links=/mnt/tpv-offline/wheels/backend \
  -r /mnt/tpv-offline/requirements-backend.txt
/opt/tpv/venvs/vllm/bin/python -m pip install --no-index \
  --find-links=/mnt/tpv-offline/wheels/vllm \
  -r /mnt/tpv-offline/requirements-vllm.txt
/opt/tpv/venvs/backend/bin/python -m pip check
/opt/tpv/venvs/vllm/bin/python -m pip check
odbcinst -q -d
nvidia-smi
```

## 5. Cấu hình offline

Ví dụ `/etc/tpv/backend.env`, bổ sung các cấu hình nghiệp vụ từ `.env.example`:

```dotenv
LLM_BASE_URL=http://127.0.0.1:15000/v1
LLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
LLM_UTILITY_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
OCR_BASE_URL=http://127.0.0.1:15000/v1
OCR_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
ERP_TEXT2SQL_BASE_URL=http://127.0.0.1:15000/v1
ERP_TEXT2SQL_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
EMBEDDING_MODEL=/opt/tpv/models/embedding
RERANKER_MODEL=/opt/tpv/models/reranker
EMBEDDING_DEVICE=cpu
EMBEDDING_FP16=false
RERANKER_DEVICE=cpu
WARMUP_MODELS=true
QDRANT_URL=http://127.0.0.1:6333
REDIS_URL=redis://127.0.0.1:6382/0
PRESENTON_URL=http://127.0.0.1:5002
DATABASE_URL=sqlite+aiosqlite:///./data/demo.db
ERP_SCHEMA_FILE=data/erp_schema.json
ERP_SQL_POLICY_FILE=config/erp_sql_policy.json
DOC_CONVERT_IMAGE=
HF_HOME=/opt/tpv/hf-cache
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1
MPLCONFIGDIR=/opt/tpv/cache/matplotlib
PYTHONUNBUFFERED=1
```

Điền `ERP_DATABASE_URL`, tenant, các API key và CORS theo LAN thật trong file này;
không dùng DB/tunnel trên Mac của đội phát triển. Nếu endpoint vLLM có key, điền
cùng key vào `LLM_API_KEY`, `OCR_API_KEY`, `ERP_TEXT2SQL_API_KEY`.
Các cờ HF offline không phải firewall; vẫn cần thử chặn Internet thật.

CPU cho embedding/reranker là cấu hình xuất phát để tránh tranh VRAM với vLLM;
nếu có GPU riêng thì đổi thiết bị và đo tải lại. Thiếu sparse head có thể làm mất
nhánh lexical dù backend vẫn lên, phải kiểm tra log `sparse_linear=có`.

`/etc/tpv/model.env`:

```dotenv
CUDA_VISIBLE_DEVICES=0
HF_HOME=/opt/tpv/hf-cache
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HF_HUB_DISABLE_TELEMETRY=1
VLLM_NO_USAGE_STATS=1
DO_NOT_TRACK=1
VLLM_WORKER_MULTIPROC_METHOD=spawn
VLLM_CACHE_ROOT=/opt/tpv/cache/vllm
PYTHONUNBUFFERED=1
```

Thêm `VLLM_API_KEY` nếu dùng xác thực. Presenton phải trỏ model nội bộ, có assets/font
local, tắt tạo/tìm ảnh ngoài; kiểm tra cả browser/renderer trong container khi cắt mạng.
Nếu không dùng Presenton, nghiệm thu đường PowerPoint local của backend.

## 6. Hai service systemd mẫu

`/etc/systemd/system/tpv-model.service`:

```ini
[Unit]
Description=TPV local Qwen vLLM
Wants=network-online.target
After=network-online.target

[Service]
Type=exec
User=tpv
Group=tpv
WorkingDirectory=/opt/tpv
EnvironmentFile=/etc/tpv/model.env
ExecStart=/opt/tpv/venvs/vllm/bin/vllm serve /opt/tpv/models/qwen --served-model-name Qwen/Qwen3.6-35B-A3B-FP8 --host 127.0.0.1 --port 15000 --enable-auto-tool-choice --tool-call-parser qwen3_coder --gpu-memory-utilization 0.70 --max-model-len 32768 --max-num-seqs 4
Restart=on-failure
RestartSec=10
TimeoutStopSec=120
LimitNOFILE=65536
UMask=0027

[Install]
WantedBy=multi-user.target
```

Parser `qwen3_coder` là cấu hình đã dùng trong bản discovery thử. Kiểm tra lại với
đúng phiên bản vLLM/model đóng gói. Context 32768/concurrency 4 là **giá trị mẫu**,
không cam kết đủ cho schema 91 bảng hoặc tài liệu dài: đo token thực tế, nâng context
trong khả năng GPU hoặc thiết kế giảm ngữ cảnh rồi đánh giá lại. Chưa bật MTP/DeepGEMM
theo script cũ cho đến khi nghiệm thu khả năng chạy offline và VRAM trên máy khách.

`/etc/systemd/system/tpv-backend.service`:

```ini
[Unit]
Description=TPV AI backend
Wants=network-online.target tpv-model.service
After=network-online.target tpv-model.service

[Service]
Type=exec
User=tpv
Group=tpv
WorkingDirectory=/opt/tpv/app/backend
EnvironmentFile=/etc/tpv/backend.env
ExecStart=/opt/tpv/venvs/backend/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8082 --workers 1
Restart=on-failure
RestartSec=10
TimeoutStopSec=120
LimitNOFILE=65536
UMask=0027

[Install]
WantedBy=multi-user.target
```

`After=` chỉ sắp thứ tự, không đợi Qwen load xong. Khi nghiệm thu phải chờ model
`/health` và `/v1/models` sẵn sàng trước khi thử nghiệp vụ. Nếu cần backend chỉ khởi
động sau khi model sẵn sàng, bổ sung readiness helper có timeout. Một worker để tránh
nhân bản embedding/reranker trong RAM/VRAM; tăng worker phải đo tài nguyên lại.
Nếu dùng Docker cho hạ tầng, bật Docker lúc boot, đặt restart policy cho container
và kiểm tra readiness; hai unit trên chưa quản lý vòng đời container.

## 7. Khởi động và vận hành

Sau khi đã chép unit, cài venv, model, hạ tầng và cấu hình đúng:

```bash
sudo systemd-analyze verify /etc/systemd/system/tpv-model.service \
  /etc/systemd/system/tpv-backend.service
sudo systemctl daemon-reload
sudo systemctl enable --now tpv-model.service tpv-backend.service
systemctl status tpv-model.service tpv-backend.service
journalctl -u tpv-model.service -n 100 --no-pager
journalctl -u tpv-backend.service -f
systemctl show tpv-model.service tpv-backend.service -p MainPID -p MemoryCurrent
nvidia-smi
free -h
df -h
```

```bash
# Nếu bật API key, thêm Authorization từ cấu hình an toàn vào lệnh kiểm tra.
curl -fsS http://127.0.0.1:15000/health
curl -fsS http://127.0.0.1:15000/v1/models
curl -fsS http://127.0.0.1:8082/health
curl -fsS http://127.0.0.1:6333/healthz
sudo systemctl restart tpv-backend.service
```

UI trong LAN: `http://<IP-LAN-máy-khách>:8082/ui/`. Chỉ mở cổng ứng dụng cho LAN
được phép; giữ Qdrant/Redis/model trên loopback hoặc mạng nội bộ có kiểm soát.
Tắt service/tmux/Docker backend cũ trước khi chuyển để không chạy trùng cổng/GPU.

## 8. Nghiệm thu bắt buộc khi không có Internet

1. Máy staging sạch, chỉ dùng bundle; cài lại thành công bằng offline wheelhouse/gói OS.
2. Chặn Internet nhưng giữ LAN; reboot; các service tự lên, không phát sinh download.
3. Chat thường, tool-calling discovery, truy vấn ERP đúng tenant và các truy vấn bị chặn.
4. Ingest + hỏi đáp tài liệu; embedding, sparse head, reranker đều có dữ liệu local.
5. OCR ảnh/PDF scan; kiểm tra model processor và xử lý tiếng Việt.
6. Đọc `.doc`, `.docx`, PDF; xuất Word và PowerPoint, biểu đồ/font không lỗi trên máy khách.
7. Chạy cả Presenton và fallback nếu bàn giao cả hai; không dựa font/CDN/ảnh Internet.
8. Đo concurrency, RAM/VRAM, context lớn, timeout, dung lượng ổ đĩa và tốc độ đáp ứng.
9. Backup/restore dữ liệu thật, cập nhật bằng bundle mới và rollback được.

Health `ok` không chứng minh toàn bộ mục trên, đặc biệt OCR, sparse head, Redis,
MinIO, Presenton và kết nối ERP. Lưu biên bản test theo từng chức năng.

## Tài liệu nguồn

- [vLLM tool calling](https://docs.vllm.ai/en/latest/features/tool_calling/)
- [Hugging Face tải model](https://huggingface.co/docs/huggingface_hub/guides/download)
- [Hugging Face offline và biến môi trường](https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables)
- [pip chuẩn bị package offline](https://pip.pypa.io/en/stable/cli/pip_download/)
- [uv export](https://docs.astral.sh/uv/reference/cli/#uv-export)
- [Microsoft ODBC Linux](https://learn.microsoft.com/en-us/sql/connect/odbc/linux-mac/installing-the-microsoft-odbc-driver-for-sql-server)
- [Docker image save](https://docs.docker.com/reference/cli/docker/image/save/)
- [systemd.service manual](https://man7.org/linux/man-pages/man5/systemd.service.5.html)
