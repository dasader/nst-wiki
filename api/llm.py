"""Gemini 호출 단일 창구. 용도별 모델·thinking_level은 llm_config.json에서 관리."""
import json
from functools import lru_cache
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "llm_config.json"


@lru_cache
def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def resolve_config(purpose: str) -> dict:
    conf = _config()
    return {**conf["default"], **conf.get(purpose, {})}


def image_part(path: Path):
    from google.genai import types

    return types.Part.from_bytes(data=path.read_bytes(), mime_type="image/png")


def generate(purpose: str, contents, schema: dict | None = None) -> str | dict:
    from google import genai
    from google.genai import types

    cfg = resolve_config(purpose)
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_level=cfg["thinking_level"]),
        response_mime_type="application/json" if schema else None,
        response_schema=schema,
    )
    client = genai.Client()  # GEMINI_API_KEY 환경변수 자동 인식
    resp = client.models.generate_content(
        model=cfg["model"], contents=contents, config=config
    )
    return json.loads(resp.text) if schema else resp.text
