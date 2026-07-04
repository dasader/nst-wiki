from llm import resolve_config


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
