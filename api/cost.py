"""토큰 사용량 → USD 환산. 단가는 조회 시점에 적용한다 — 과거 기록도 자동 정정된다.

USD / 1M 토큰, 표준(standard) 티어. 출력 단가에는 thinking 토큰이 포함된다.
출처: https://ai.google.dev/gemini-api/docs/pricing (2026-07-08 확인).
모델을 바꾸면 여기에 단가를 추가할 것 — 미등록 모델은 비용이 0이 아니라 '단가 미등록'으로 표시된다.
"""

PRICING = {
    "gemini-3.1-flash-lite": {"input": 0.25, "cached_input": 0.025, "output": 1.50},
}
_M = 1_000_000


def cost_usd(model: str, prompt_tokens: int, cached_tokens: int,
             output_tokens: int, thought_tokens: int) -> float | None:
    """이 호출(들)의 비용. 단가 미등록 모델은 0이 아니라 None — 조용히 0원으로 세지 않는다.

    - 입력은 prompt_tokens 전체에서 cached_tokens를 뺀 만큼만 정가, 캐시분은 할인가.
    - 사고(thinking) 토큰은 출력 단가로 과금된다 (Gemini 공식 단가표 명시).
    """
    p = PRICING.get(model)
    if p is None:
        return None
    uncached = max(prompt_tokens - cached_tokens, 0)
    return (uncached * p["input"]
            + cached_tokens * p["cached_input"]
            + (output_tokens + thought_tokens) * p["output"]) / _M


def priced(row: dict) -> dict:
    """집계 행 하나에 cost_usd를 붙여 돌려준다 (원본은 건드리지 않는다)."""
    return {**row, "cost_usd": cost_usd(
        row["model"], row["prompt_tokens"], row["cached_tokens"],
        row["output_tokens"], row["thought_tokens"])}


def unpriced_models(rows: list[dict]) -> list[str]:
    return sorted({r["model"] for r in rows if r["model"] not in PRICING})
