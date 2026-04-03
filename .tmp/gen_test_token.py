import base64
import hashlib
import hmac
import json
import time

SECRET = "7b4c9e2a8f1d6c3b5e9a2f8c7b4d9e6a3f1b8c7d5e9a2f8c7b4d9e6a3f1b8c".encode("utf-8")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


now = int(time.time())
header = {"alg": "HS256", "typ": "JWT"}
payload = {
    "username": "cascade-test",
    "is_admin": True,
    "department_ids": ["1"],
    "collection_names": ["default"],
    "iss": "gov-backend",
    "aud": "gov-platform",
    "iat": now,
    "exp": now + 3600,
}

header_part = b64url(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
payload_part = b64url(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
signing_input = f"{header_part}.{payload_part}".encode("ascii")
signature = hmac.new(SECRET, signing_input, hashlib.sha256).digest()
print(f"{header_part}.{payload_part}.{b64url(signature)}")
