import logging
import os
from contextlib import asynccontextmanager

import httpx
import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app import db

log = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # 기동 때마다 스키마를 멱등 적용해 코드와 DB를 수렴시킨다 (수동 psql 단계 제거).
    # 실패해도 기동은 계속한다 — 위키 조회 같은 읽기 경로까지 함께 죽이지 않기 위해서.
    # 대신 조용히 넘기지 않고 예외를 그대로 남긴다.
    try:
        applied = db.apply_schema()
        log.info("DB 스키마 적용: %s", ", ".join(applied) or "(대상 없음)")
    except Exception:
        log.exception("DB 스키마 적용 실패 — 코드와 DB 스키마가 어긋난 채 기동합니다")
    yield


app = FastAPI(title="nst-wiki API", lifespan=lifespan)


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
from app.query_api import router as query_router
from app.read_api import router as read_router

app.include_router(ingest_router)
app.include_router(query_router)
app.include_router(read_router)
