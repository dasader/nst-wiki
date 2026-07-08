import pytest

import llm
from llm import resolve_config


class _Boom(Exception):
    def __init__(self, code):
        self.code = code


def test_retry_succeeds_after_transient(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Boom(503)
        return "ok"

    assert llm._call_with_retry(fn) == "ok"
    assert calls["n"] == 3


def test_retry_gives_up_after_cap(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _Boom(429)

    with pytest.raises(_Boom):
        llm._call_with_retry(fn)
    assert calls["n"] == llm._MAX_ATTEMPTS


def test_retry_does_not_retry_schema_errors(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise ValueError("bad schema")  # 비일시 오류 → 즉시 전파

    with pytest.raises(ValueError):
        llm._call_with_retry(fn)
    assert calls["n"] == 1


def test_resolve_config_default():
    cfg = resolve_config("classify")
    assert cfg["model"] == "gemini-3.1-flash-lite"
    assert cfg["thinking_level"] == "high"


def test_resolve_config_merges_override(tmp_path, monkeypatch):
    import llm
    p = tmp_path / "llm_config.json"
    p.write_text(
        '{"default": {"model": "m1", "thinking_level": "high"}, "merge_page": {"model": "m2"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(llm, "CONFIG_PATH", p)
    llm._config.cache_clear()
    try:
        assert llm.resolve_config("merge_page") == {"model": "m2", "thinking_level": "high"}
        assert llm.resolve_config("classify") == {"model": "m1", "thinking_level": "high"}
    finally:
        llm._config.cache_clear()


def test_pdf_part_builds_pdf_mime(tmp_path):
    import llm
    p = tmp_path / "x.pdf"
    p.write_bytes(b"%PDF-1.4 test")
    part = llm.pdf_part(p)
    assert part.inline_data.mime_type == "application/pdf"
    assert part.inline_data.data == b"%PDF-1.4 test"


def test_parse_purposes_use_low_thinking_and_long_timeout():
    import llm
    for purpose in ("parse_markdown", "parse_tables"):
        cfg = llm.resolve_config(purpose)
        assert cfg["thinking_level"] == "low"
        assert cfg["timeout_ms"] == 180000


def test_generate_records_usage_with_source_context(monkeypatch):
    """호출마다 토큰 사용량을 적재하고, source_context 안이면 문서에 귀속시킨다."""
    rec = {}

    class _U:
        prompt_token_count, candidates_token_count = 120, 30
        thoughts_token_count, cached_content_token_count = 300, 40

    class _R:
        text = "ok"
        usage_metadata = _U()

    monkeypatch.setattr(llm, "_call_with_retry", lambda fn: _R())
    monkeypatch.setattr(llm, "resolve_config",
                        lambda p: {"model": "m1", "thinking_level": "low"})
    from app import db

    monkeypatch.setattr(db, "record_llm_usage", lambda **kw: rec.update(kw))
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")   # Client 생성만, 네트워크는 안 탄다

    with llm.source_context("src-123"):
        assert llm.generate("classify", "hi") == "ok"

    assert rec["purpose"] == "classify" and rec["model"] == "m1"
    assert rec["source_id"] == "src-123"
    assert (rec["prompt_tokens"], rec["cached_tokens"]) == (120, 40)
    assert (rec["output_tokens"], rec["thought_tokens"]) == (30, 300)


def test_generate_survives_usage_recording_failure(monkeypatch):
    """계측 실패가 LLM 호출을 죽이면 안 된다."""
    class _R:
        text = "ok"
        usage_metadata = None   # 접근하면 AttributeError

    monkeypatch.setattr(llm, "_call_with_retry", lambda fn: _R())
    monkeypatch.setattr(llm, "resolve_config",
                        lambda p: {"model": "m1", "thinking_level": "low"})
    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    assert llm.generate("classify", "hi") == "ok"   # 예외 없이 반환


def test_source_context_defaults_to_none():
    assert llm._current_source.get() is None
