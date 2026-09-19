"""Lưu trữ file cục bộ: nơi duy nhất biết file nằm ở đâu và chỗ nào được đọc.

Hai thư mục có vai trò khác hẳn nhau nên không trộn: `upload_dir` giữ file người
dùng gửi lên (đầu vào, không tin được), `output_dir` giữ file hệ thống sinh ra
(đầu ra, cho tải về).

Mọi định danh file đến từ bên ngoài - tên file trong URL tải về, `file_id` mà LLM
nhắc lại trong một lượt hội thoại - đều phải đi qua `resolve()`. Đó là chỗ duy
nhất chặn được đường dẫn kiểu `../../etc/passwd`, nên tool không được tự ghép
đường dẫn lấy.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Literal

from app.core.config import get_settings
from app.core.context import current_tenant_id

logger = logging.getLogger(__name__)

Kind = Literal["upload", "output"]
KINDS: tuple[Kind, ...] = ("upload", "output")

_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.\-]")


class StorageError(ValueError):
    """Định danh file không hợp lệ hoặc trỏ ra ngoài vùng được phép."""


@dataclass(slots=True)
class FileRef:
    """Một file đã được xác thực là nằm trong vùng cho phép."""

    file_id: str
    path: Path
    kind: Kind

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def suffix(self) -> str:
        return self.path.suffix.lower()

    @property
    def size(self) -> int:
        return self.path.stat().st_size if self.path.is_file() else 0

    def as_dict(self) -> dict[str, Any]:
        stat = self.path.stat() if self.path.is_file() else None
        return {
            "file_id": self.file_id,
            "name": self.name,
            "kind": self.kind,
            "size": stat.st_size if stat else 0,
            "modified": (
                datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
                if stat else None
            ),
        }


def _roots() -> dict[str, Path]:
    cfg = get_settings()
    return {"upload": Path(cfg.upload_dir), "output": Path(cfg.output_dir)}


def root(kind: Kind) -> Path:
    """Thư mục gốc của một loại file, tạo sẵn nếu chưa có."""
    roots = _roots()
    if kind not in roots:
        raise StorageError(f"Loại lưu trữ không hợp lệ: {kind!r}")
    path = roots[kind]
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(filename: str) -> str:
    """Bỏ mọi thành phần thư mục và ký tự lạ, chỉ giữ lại tên file."""
    name = _UNSAFE_RE.sub("_", Path(filename or "").name).strip("._") or "file"
    return name[:180]


# Dấu tenant gắn vào tên file đầu ra: "SLIDE_2026-08__t64.pptx".
#
# Không có nó thì hai thuê bao cùng xin báo cáo một kỳ sẽ ghi đè lên nhau - cùng
# một đường dẫn, cùng một cái tên đoán được - và người tải sau nhận nguyên bộ số
# liệu của người trước. Thư mục output là kho dùng chung, nên chỗ tách thuê bao
# phải nằm ngay ở tên file.
# Dấu tenant không còn đứng cuối (sau nó còn mốc thời gian), nên tìm ở bất kỳ đâu.
_TENANT_SUFFIX_RE = re.compile(r"__t(\d+)(?:__|$)")


def tenant_stem(stem: str) -> str:
    """Gắn tenant của request hiện tại vào phần tên file (chưa có đuôi)."""
    tenant = current_tenant_id()
    return stem if tenant is None else f"{stem}__t{tenant}"


def versioned_stem(stem: str) -> str:
    """Tên file đầu ra: kèm tenant VÀ mốc thời gian, nên mỗi lần tạo là một file mới.

    Tên cố định theo kỳ ("SLIDE_2026-08__t64.pptx") nghe thì gọn, nhưng tạo lại
    lần hai là ghi đè lần một trên cùng một URL và cùng một tên. Hệ quả ở phía
    người dùng: trình duyệt đã có file trùng tên trong thư mục tải về nên lưu bản
    mới thành "... (1).pptx", còn người dùng mở lại bản đầu và kết luận hệ thống
    chưa sửa gì. Chuyện này đã xảy ra ba lần trong một buổi.

    Đổi lại, thư mục output dồn file theo thời gian và cần dọn định kỳ. Đó là cái
    giá rẻ hơn nhiều so với việc người dùng đọc nhầm một bản báo cáo cũ.
    """
    return f"{tenant_stem(stem)}__{datetime.now():%Y%m%d-%H%M%S}"


def tenant_of(filename: str) -> int | None:
    match = _TENANT_SUFFIX_RE.search(Path(filename).stem)
    return int(match.group(1)) if match else None


def readable_by_current_tenant(filename: str) -> bool:
    """File này có thuộc về thuê bao đang gọi không.

    File không mang dấu tenant chỉ đọc được khi request cũng không có tenant. Điều
    đó khoá luôn những file sinh ra trước khi có cơ chế này - chúng chứa số liệu
    của một thuê bao không xác định, nên để đọc được mới là sai.
    """
    return tenant_of(filename) == current_tenant_id()


def resolve_output(filename: str) -> FileRef:
    """File trong thư mục output, đã chặn cả vượt thư mục lẫn đọc chéo thuê bao."""
    ref = resolve(filename, kinds=("output",))
    if not readable_by_current_tenant(ref.name):
        # Cùng một câu trả lời với file không tồn tại: nói "file này của thuê bao
        # khác" là đã xác nhận nó có thật.
        raise StorageError(f"Không tìm thấy file {filename!r}")
    return ref


def make_file_id(kind: Kind, name: str) -> str:
    return f"{kind}:{name}"


def save_upload(source: bytes | bytearray | BinaryIO, filename: str, kind: Kind = "upload") -> FileRef:
    """Ghi file vào thư mục tương ứng và trả về định danh dùng lại được.

    Trùng tên thì ghi đè: người dùng gửi lại bản sửa của cùng một văn bản là tình
    huống thường gặp hơn nhiều so với hai văn bản khác nhau trùng tên.
    """
    target = root(kind) / safe_name(filename)
    if isinstance(source, (bytes, bytearray)):
        target.write_bytes(bytes(source))
    else:
        with target.open("wb") as buffer:
            shutil.copyfileobj(source, buffer)
    return FileRef(file_id=make_file_id(kind, target.name), path=target, kind=kind)


def resolve(file_id: str, kinds: tuple[Kind, ...] = KINDS) -> FileRef:
    """Biến định danh file thành đường dẫn thật, chặn đường dẫn vượt ra ngoài.

    Chấp nhận ba dạng, theo thứ tự ưu tiên:
      - "upload:ban_thao.docx"        - có nói rõ thư mục
      - "data/uploads/ban_thao.docx"  - đường dẫn (tương đối hoặc tuyệt đối)
      - "ban_thao.docx"               - tên trần, tìm lần lượt trong `kinds`
    """
    raw = (file_id or "").strip()
    if not raw:
        raise StorageError("Thiếu định danh file")

    prefix, _, rest = raw.partition(":")
    if prefix in _roots() and rest:
        candidate = root(prefix).resolve() / safe_name(rest)  # type: ignore[arg-type]
        if candidate.is_file():
            return FileRef(file_id=make_file_id(prefix, candidate.name),  # type: ignore[arg-type]
                           path=candidate, kind=prefix)  # type: ignore[arg-type]
        raise StorageError(f"Không tìm thấy file {raw!r}")

    # Dạng đường dẫn: chỉ nhận nếu nằm trong một thư mục được phép.
    as_path = Path(raw).expanduser()
    if as_path.is_file():
        resolved = as_path.resolve()
        for kind in kinds:
            base = root(kind).resolve()
            if resolved.is_relative_to(base):
                return FileRef(file_id=make_file_id(kind, resolved.name), path=resolved, kind=kind)
        raise StorageError(f"File {raw!r} nằm ngoài thư mục được phép truy cập")

    # Dạng tên trần.
    name = safe_name(raw)
    for kind in kinds:
        candidate = root(kind).resolve() / name
        if candidate.is_file():
            return FileRef(file_id=make_file_id(kind, candidate.name), path=candidate, kind=kind)

    raise StorageError(f"Không tìm thấy file {raw!r}")


def new_output(stem: str, suffix: str) -> Path:
    """Đường dẫn file sắp sinh ra trong thư mục output, tên đã được làm sạch."""
    clean = safe_name(stem).removesuffix(suffix)
    return root("output") / f"{clean}{suffix}"


def list_files(kind: Kind = "output") -> list[FileRef]:
    return [
        FileRef(file_id=make_file_id(kind, path.name), path=path, kind=kind)
        for path in sorted(root(kind).glob("*"), key=lambda p: p.stat().st_mtime, reverse=True)
        if path.is_file()
    ]
