"""Gemini 호출 단일 창구. 용도별 모델·thinking_level은 llm_config.json에서 관리.

모든 호출이 여기를 지나므로 토큰 사용량 계측도 여기 한 곳에서 한다 (llm_usage 테이블).
"""
import contextvars
import json
import logging
import time
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "llm_config.json"

log = logging.getLogger(__name__)

# 인제스트 중 호출을 문서에 귀속시킨다. 함수 시그니처를 건드리지 않으려고 contextvar를 쓴다.
# 질의응답처럼 문서 맥락이 없는 호출은 None으로 남는다.
_current_source: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_source_id", default=None)


@contextmanager
def source_context(source_id: str | None):
    token = _current_source.set(source_id)
    try:
        yield
    finally:
        _current_source.reset(token)

# ponytail: 3회·지수백오프(1→2→4s)·60s 타임아웃. Gemini가 더 자주 죽으면 상향.
_MAX_ATTEMPTS = 3
_BACKOFF_BASE = 1.0
_TIMEOUT_MS = 60_000
_TRANSIENT_CODES = {429, 500, 502, 503, 504}  # rate-limit + 5xx


def _is_transient(exc: Exception) -> bool:
    """타임아웃·5xx·429만 재시도 대상. 스키마/검증 오류는 스스로 안 낫는다."""
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return True
    for attr in ("code", "status_code"):
        v = getattr(exc, attr, None)
        if isinstance(v, int) and v in _TRANSIENT_CODES:
            return True
    return False


def _call_with_retry(fn):
    """전송 함수(fn)를 재시도 감싸기. 일시 오류만 재시도, 그 외는 즉시 전파."""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as e:
            if attempt == _MAX_ATTEMPTS or not _is_transient(e):
                raise
            time.sleep(_BACKOFF_BASE * 2 ** (attempt - 1))


@lru_cache
def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_config(purpose: str) -> dict:
    conf = _config()
    return {**conf["default"], **conf.get(purpose, {})}


def pdf_part(path: Path):
    from google.genai import types

    return types.Part.from_bytes(data=path.read_bytes(), mime_type="application/pdf")


def _record_usage(purpose: str, model: str, resp, latency_ms: int) -> None:
    """토큰 사용량 적재. 계측 실패가 LLM 호출을 죽이면 안 된다 — 삼키고 경고만 남긴다."""
    try:
        u = resp.usage_metadata
        from app import db  # 지연 임포트: llm.py는 DB 없이도 임포트 가능해야 한다

        db.record_llm_usage(
            purpose=purpose, model=model, source_id=_current_source.get(),
            prompt_tokens=u.prompt_token_count or 0,
            cached_tokens=getattr(u, "cached_content_token_count", None) or 0,
            output_tokens=u.candidates_token_count or 0,
            thought_tokens=getattr(u, "thoughts_token_count", None) or 0,
            latency_ms=latency_ms,
        )
    except Exception:
        log.warning("LLM 사용량 기록 실패 (purpose=%s)", purpose, exc_info=True)


def generate(purpose: str, contents, schema: dict | None = None) -> str | dict:
    from google import genai
    from google.genai import types

    cfg = resolve_config(purpose)
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=cfg["thinking_level"]),
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
    )
    client = genai.Client(  # GEMINI_API_KEY 환경변수 자동 인식
        http_options=types.HttpOptions(timeout=cfg.get("timeout_ms", _TIMEOUT_MS))
    )
    started = time.monotonic()
    resp = _call_with_retry(lambda: client.models.generate_content(
        model=cfg["model"], contents=contents, config=config
    ))
    _record_usage(purpose, cfg["model"], resp, int((time.monotonic() - started) * 1000))
    return json.loads(resp.text) if schema else resp.text
