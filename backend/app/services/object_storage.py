"""File gốc trên MinIO; caller quản lý vòng đời file tạm."""
import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlsplit

from minio import Minio
from urllib3 import PoolManager, Timeout

from app.core.config import get_settings


def parse_reference(value):
    """Nhận minio://bucket/key, minio:bucket/key và s3://bucket/key."""
    url = urlsplit(value or "")
    if url.scheme not in {"s3", "minio"}:
        raise ValueError("Tham chiếu file phải thuộc MinIO")
    # BE cũ lưu minio:<object-key>, bucket nằm trong cấu hình BE.
    # Chỉ bật cách hiểu này khi đã khai đúng một bucket BE và prefix knowledge/.
    cfg = get_settings()
    if value.startswith("minio:knowledge/") and len(cfg.be_minio_buckets) == 1 and cfg.be_minio_endpoint:
        if url.query or url.fragment:
            raise ValueError("Tham chiếu MinIO có tham số lạ")
        return cfg.be_minio_buckets[0], url.path
    bucket, key = (url.netloc, url.path.lstrip("/")) if url.netloc else (
        url.path.lstrip("/").partition("/")[0], url.path.lstrip("/").partition("/")[2])
    if not bucket or not key or url.query or url.fragment:
        raise ValueError("Tham chiếu MinIO thiếu bucket/object hoặc có tham số lạ")
    return bucket, key


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

    def source_client(self, bucket):
        if self.cfg.be_minio_endpoint and bucket in self.cfg.be_minio_buckets:
            url = urlsplit(self.cfg.be_minio_endpoint if "://" in self.cfg.be_minio_endpoint else
                ("https://" if self.cfg.be_minio_secure else "http://") + self.cfg.be_minio_endpoint)
            if url.scheme not in {"http", "https"} or url.path not in {"", "/"} or url.username or url.password:
                raise ValueError("BE_MINIO_ENDPOINT không hợp lệ")
            return Minio(url.netloc, access_key=self.cfg.be_minio_access_key,
                secret_key=self.cfg.be_minio_secret_key, secure=url.scheme == "https",
                http_client=PoolManager(timeout=Timeout(connect=5, read=120), retries=False))
        return self.client

    def validate_bucket(self, bucket):
        if bucket not in {self.cfg.minio_bucket, *self.cfg.minio_ingest_buckets,
                          *(self.cfg.be_minio_buckets if self.cfg.be_minio_endpoint else [])}:
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
        # Đọc trực tiếp: một số S3 proxy cho GET nhưng từ chối HEAD mà fget_object cần.
        response = self.source_client(bucket).get_object(bucket, key)
        try:
            with path.open("wb") as target:
                for chunk in response.stream(1024 * 1024):
                    target.write(chunk)
        finally:
            response.close()
            response.release_conn()
        return self.metadata(path, filename, bucket, key, self.checksum(path))

    def open(self, bucket: str, key: str):
        self.validate_bucket(bucket)
        return self.source_client(bucket).get_object(bucket, key)

    @staticmethod
    def checksum(path):
        with path.open("rb") as content:
            return hashlib.file_digest(content, "sha256").hexdigest()

    @staticmethod
    def metadata(path, filename, bucket, key, checksum):
        return {"storage_bucket": bucket, "storage_object_key": key,
                "original_filename": filename, "checksum_sha256": checksum,
                "file_size_bytes": path.stat().st_size}
