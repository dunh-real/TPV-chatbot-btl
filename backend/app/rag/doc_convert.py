"""Chuyển Word 97-2003 (.doc) sang .docx trước khi đưa cho MarkItDown.

MarkItDown không có bộ đọc .doc: mọi file .doc tải lên đều chết ở
`UnsupportedFormatException` dù `.doc` có trong danh sách định dạng hỗ trợ - đo được
trên Luật 23/2026 và Bộ luật Hình sự (rag_testing). LibreOffice đọc được .doc tốt nhất;
host không có LibreOffice và không có quyền cài, nên chạy nó trong container
(`backend/docker/libreoffice/Dockerfile`). `doc_convert_image` rỗng thì gọi `soffice`
trên host.

Kết quả được cache theo sha256 của file gốc: cùng một file nạp lại không chạy lại
LibreOffice (mỗi lần mất vài giây tới vài chục giây với văn bản dài).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


class DocConvertError(RuntimeError):
    """Không chuyển được .doc - báo rõ thay vì để MarkItDown ném lỗi khó hiểu."""


def _cache_dir(settings: Settings) -> Path:
    path = Path(settings.upload_dir).parent / "doc_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def doc_to_docx(path: str | Path, settings: Settings | None = None) -> Path:
    """Đường dẫn bản .docx của `path` (.doc); lấy từ cache nếu đã chuyển."""
    cfg = settings or get_settings()
    src = Path(path)
    digest = hashlib.sha256(src.read_bytes()).hexdigest()
    cached = _cache_dir(cfg) / f"{digest}.docx"
    if cached.exists() and cached.stat().st_size > 0:
        return cached

    with tempfile.TemporaryDirectory(prefix="docconv_") as tmp:
        tmp_dir = Path(tmp)
        # Tên ASCII cố định: tên file tiếng Việt có dấu cách vẫn chạy được, nhưng
        # tránh hẳn chuyện LibreOffice đổi tên đầu ra theo locale của container.
        staged = tmp_dir / "in.doc"
        shutil.copyfile(src, staged)
        out_dir = tmp_dir / "out"
        out_dir.mkdir()
        if cfg.doc_convert_image:
            cmd = ["docker", "run", "--rm", "--network", "none",
                   "--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp",
                   "-v", f"{tmp_dir}:/work", cfg.doc_convert_image,
                   # Chạy bằng uid của backend (không có trong /etc/passwd của image) thì
                   # LibreOffice không tạo được hồ sơ mặc định và thoát mã 77 - chỉ định
                   # thẳng thư mục hồ sơ.
                   "soffice", "-env:UserInstallation=file:///tmp/lo_profile", "--headless",
                   "--convert-to", "docx", "--outdir", "/work/out", "/work/in.doc"]
        else:
            cmd = ["soffice", "--headless", "--convert-to", "docx", "--outdir", str(out_dir), str(staged)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=cfg.doc_convert_timeout)
        except FileNotFoundError as exc:
            raise DocConvertError(f"Không tìm thấy công cụ chuyển .doc ({cmd[0]})") from exc
        except subprocess.TimeoutExpired as exc:
            raise DocConvertError(f"Chuyển .doc quá {cfg.doc_convert_timeout:.0f}s: {src.name}") from exc
        result = out_dir / "in.docx"
        if proc.returncode != 0 or not result.exists():
            raise DocConvertError(
                f"LibreOffice không chuyển được {src.name} (mã {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:300]}")
        shutil.move(str(result), cached)
    logger.info("Đã chuyển %s -> .docx (%d byte)", src.name, cached.stat().st_size)
    return cached
