import cost


def test_cost_includes_thinking_tokens_as_output():
    """사고 토큰은 출력 단가로 과금된다 (Gemini 공식 단가표). 빠뜨리면 크게 과소 계상된다."""
    # 100만 입력(캐시 없음) + 100만 출력(candidates) → 0.25 + 1.50
    assert cost.cost_usd("gemini-3.1-flash-lite", 1_000_000, 0, 1_000_000, 0) == 1.75
    # 사고 토큰 100만을 더하면 출력 단가만큼 늘어난다
    assert cost.cost_usd("gemini-3.1-flash-lite", 1_000_000, 0, 1_000_000, 1_000_000) == 3.25


def test_cached_input_is_discounted_and_not_double_counted():
    """prompt_tokens는 cached를 포함한다 — 캐시분은 할인가로 한 번만 센다."""
    full = cost.cost_usd("gemini-3.1-flash-lite", 1_000_000, 0, 0, 0)          # 전량 정가
    half = cost.cost_usd("gemini-3.1-flash-lite", 1_000_000, 500_000, 0, 0)    # 절반 캐시
    assert full == 0.25
    assert half == 0.5 * 0.25 + 0.5 * 0.025


def test_unknown_model_returns_none_not_zero():
    """단가 미등록 모델을 0원으로 조용히 세지 않는다 (no silent caps)."""
    assert cost.cost_usd("gemini-9-ultra", 1_000_000, 0, 1_000_000, 0) is None


def test_unpriced_models_are_surfaced():
    rows = [{"model": "gemini-3.1-flash-lite"}, {"model": "gemini-9-ultra"}]
    assert cost.unpriced_models(rows) == ["gemini-9-ultra"]


def test_priced_attaches_cost_without_mutating_row():
    row = {"model": "gemini-3.1-flash-lite", "prompt_tokens": 1_000_000,
           "cached_tokens": 0, "output_tokens": 0, "thought_tokens": 0}
    out = cost.priced(row)
    assert out["cost_usd"] == 0.25
    assert "cost_usd" not in row
