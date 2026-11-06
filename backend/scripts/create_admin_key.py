"""Sinh shared key vào .env, không in secret ra terminal và không ghi đè key đang dùng."""
import argparse
import os
import secrets
from pathlib import Path
from dotenv import dotenv_values, set_key

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--env", default=".env")
args = parser.parse_args()
path = Path(args.env)
if dotenv_values(path).get("ADMIN_API_KEY"):
    raise SystemExit("ADMIN_API_KEY đã tồn tại; không thay key đang dùng")
set_key(path, "ADMIN_API_KEY", secrets.token_urlsafe(48))
os.chmod(path, 0o600)
print(f"Đã lưu ADMIN_API_KEY vào {path}; chuyển key cho BE qua kênh bảo mật")
