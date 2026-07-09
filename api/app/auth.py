"""관리자 키 검증 — ingest·read 라우터가 공유하는 인증 단일 출처."""
import hmac
import os

from fastapi import Header, HTTPException


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(status_code=401, detail="invalid admin key")
