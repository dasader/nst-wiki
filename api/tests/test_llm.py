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
