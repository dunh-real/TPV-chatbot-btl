import os
from minio import Minio
from io import BytesIO
from datetime import timedelta

# MinIO configuration via environment variables (defaults kept for local/dev)
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "s3.lamhai.net")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "sipm-user")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "Y9CQtFqPARVX7Zyg")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "sipm-smart-industry-management")
MINIO_SECURE = os.getenv("MINIO_SECURE", "true").lower() == "true"
PRESIGN_EXPIRY = int(os.getenv("PRESIGN_EXPIRY", "3600"))


def get_minio_client() -> Minio:
    """Create and return a MinIO client."""
    return Minio(
        endpoint=MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE,
    )


def upload_text(object_name: str, text: str, content_type: str = "text/markdown") -> dict:
    """Upload a text/string content to MinIO as an object.

    Returns a dict with object_name and size.
    """
    client = get_minio_client()
    data = text.encode("utf-8")
    length = len(data)
    bytes_io = BytesIO(data)

    # Ensure bucket exists (MinIO will raise if not)
    # Note: don't create bucket here to avoid accidental perms changes; assume bucket exists
    client.put_object(
        bucket_name=MINIO_BUCKET,
        object_name=object_name,
        data=bytes_io,
        length=length,
        content_type=content_type,
    )

    return {"bucket": MINIO_BUCKET, "object_name": object_name, "size": length}


def generate_presigned_get_url(object_name: str, expiry_seconds: int = PRESIGN_EXPIRY) -> str:
    client = get_minio_client()
    return client.presigned_get_object(bucket_name=MINIO_BUCKET, object_name=object_name, expires=timedelta(seconds=expiry_seconds))
