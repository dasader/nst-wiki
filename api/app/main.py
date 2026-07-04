import os

import httpx
import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="nst-wiki API")


def _check(fn) -> str:
    try:
        fn()
        return "ok"
    except Exception as e:  # ponytail: 개인용 헬스체크라 원인 문자열 그대로 노출
        return f"error: {e}"


@app.get("/health")
def health():
    checks = {
        "postgres": _check(lambda: psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3).close()),
        "qdrant": _check(lambda: httpx.get(os.environ["QDRANT_URL"] + "/readyz", timeout=3).raise_for_status()),
        "redis": _check(lambda: redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3).ping()),
    }
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(checks, status_code=200 if ok else 503)


from app.ingest_api import router as ingest_router

app.include_router(ingest_router)
