"""File gốc trên MinIO; caller quản lý vòng đời file tạm."""
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from minio import Minio
from urllib3 import PoolManager, Timeout

from app.core.config import get_settings


class ObjectStorage:
    def __init__(self):
        self.cfg = get_settings()
        endpoint = self.cfg.minio_endpoint
        url = urlsplit(endpoint if "://" in endpoint else
                       ("https://" if self.cfg.minio_secure else "http://") + endpoint)
        if url.scheme not in {"http", "https"} or url.path not in {"", "/"} or url.username or url.password:
            raise ValueError("MINIO_ENDPOINT không hợp lệ")
        self.client = Minio(url.netloc, access_key=self.cfg.minio_access_key,
                            secret_key=self.cfg.minio_secret_key, secure=url.scheme == "https",
                            http_client=PoolManager(timeout=Timeout(connect=5, read=120), retries=False))

    def validate_bucket(self, bucket):
        if bucket not in {self.cfg.minio_bucket, *self.cfg.minio_ingest_buckets}:
            raise PermissionError("Bucket không được phép nạp")

    def upload(self, path: Path, filename: str):
        bucket = self.cfg.minio_bucket
        checksum = self.checksum(path)
        key = f"uploads/{checksum}{path.suffix.lower()}"
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)
        self.client.fput_object(bucket, key, str(path),
                                content_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")
        return self.metadata(path, filename, bucket, key, checksum)

    def download(self, bucket: str, key: str, path: Path, filename: str):
        self.validate_bucket(bucket)
        self.client.fget_object(bucket, key, str(path))
        return self.metadata(path, filename, bucket, key, self.checksum(path))

    @staticmethod
    def checksum(path):
        with path.open("rb") as content:
            return hashlib.file_digest(content, "sha256").hexdigest()

    @staticmethod
    def metadata(path, filename, bucket, key, checksum):
        return {"storage_bucket": bucket, "storage_object_key": key,
                "original_filename": filename, "checksum_sha256": checksum,
                "file_size_bytes": path.stat().st_size}
