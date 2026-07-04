# Phase 2b: LLM 컴파일 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** parsed/ 산출물을 Gemini로 분류·해석·매핑하여, 서사는 위키 스테이징 브랜치(`ingest/{source_id}`)에, 정형 표는 PostgreSQL staging 스키마에 적재하고 상태를 `staged`까지 전이시킨다.

**Architecture:** 스펙 4.2~4.5절(Stage 2~5)을 구현한다. `llm.py`가 Gemini 호출을 단일 창구로 감싸고(용도별 모델 config), 분류기가 text 청크를 서사/비서사로 나누고, 그림은 멀티모달 설명으로 서사에 편입되며, 표는 고정 스키마 매핑(신뢰도 0.8 미만은 staging_tables 폴백), 서사는 페이지 계획→병합→모순 기록→브랜치 커밋으로 흐른다. 승인/병합(Stage 7~8)은 Phase 3.

**Tech Stack:** google-genai SDK (`gemini-3.1-flash-lite`, `thinking_level: high`), 기존 스택 (Celery/psycopg/git)

## Global Constraints

- **사전 요구사항: `.env`에 `GEMINI_API_KEY` 설정** (Task 1 검증부터 실 호출 사용. 키는 사용자가 직접 넣는다)
- LLM 모델·thinking_level은 `api/llm_config.json`에서만 지정 (스펙 9절: 하드코딩 금지). 기본값 `gemini-3.1-flash-lite` / `high`
- 모든 LLM 호출은 `llm.generate(purpose, contents, schema=None)` 경유. 테스트는 `llm.generate`를 monkeypatch — 실 API 호출하는 유닛테스트 금지 (실 호출은 검증 단계의 스모크·E2E만)
- 표 매핑 신뢰도 임계값 0.8 (스펙 4.4절). LLM 직접 매핑 대상은 `technologies`, `projects`, `policy_events`, `ministries` 4개 — FK 연결이 필요한 `budget_history`·`tech_project_mapping`은 항상 `staging_tables`로 (사람이 검토)
- 소스 1건당 자동 갱신 위키 페이지 상한 15 (스펙 4.3절). 초과분은 `affected_pages`에 `action: "suggested"`로만 기록
- 위키 쓰기는 `ingest/{source_id}` 브랜치에만, main 직접 커밋 금지 (스펙 4.1절). 브랜치 작업은 flock으로 직렬화 (worker concurrency=2 대비)
- 상태 전이: `parsed → classifying → staged | failed`
- LLM은 DDL을 생성하지 않는다 — staging 적재는 고정 컬럼 목록(CORE_TABLES 상수) 안에서만
- 커밋 메시지·문서·LLM 프롬프트는 한국어
- **범위 밖:** 승인/거부·main 병합·임베딩(Phase 3~4), frontend, HWP
- 스펙 4.2절의 4분류 중 `TABLE_STRUCTURED`/`TABLE_WITH_CONTEXT`는 파서가 이미 표를 분리했으므로 분류기는 text 청크만 `NARRATIVE|METADATA|SKIP`으로 나눈다. METADATA 청크는 버린다(메타데이터는 업로드 시 입력) — 의도된 단순화
- 테스트 명령(전체): `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --no-project python -m pytest tests -v`

---

### Task 1: LLM 클라이언트 + config + 의존성 정리

**Files:**
- Create: `api/llm.py`
- Create: `api/llm_config.json`
- Modify: `api/requirements.txt`, `api/Dockerfile`, `docker-compose.yml`, `.env.example`
- Test: `api/tests/test_llm.py`

**Interfaces:**
- Produces: `llm.generate(purpose: str, contents, schema: dict | None = None) -> str | dict` (schema 지정 시 JSON 파싱된 dict 반환), `llm.image_part(path: Path)` (멀티모달 이미지 파트), `llm.resolve_config(purpose: str) -> dict`. 이후 모든 태스크가 `llm.generate`만 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_llm.py`:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_llm.py -v`
Expected: FAIL — `ModuleNotFoundError` (llm 없음)

- [ ] **Step 3: 구현**

`api/llm_config.json`:

```json
{
  "default": {"model": "gemini-3.1-flash-lite", "thinking_level": "high"},
  "classify": {},
  "describe_figure": {},
  "map_table": {},
  "plan_pages": {},
  "merge_page": {}
}
```

`api/llm.py`:

```python
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
```

참고: google-genai SDK 버전에 따라 `ThinkingConfig(thinking_level=...)` 파라미터명이 다를 수 있다(구버전은 `thinking_budget`). 스모크 테스트(Step 6)에서 TypeError 발생 시 설치된 SDK의 시그니처를 확인해 맞추고 보고서에 기록할 것.

- [ ] **Step 4: 의존성·설정 정리**

`api/requirements.txt`: `uvicorn[standard]` → `uvicorn`으로 교체, `redis` 줄 삭제(celery[redis]가 설치), 마지막에 `google-genai` 추가.
`api/Dockerfile`: `COPY tasks.py .` 다음에 `COPY llm.py llm_config.json ./` 추가.
`docker-compose.yml`: api·ingest-worker 두 서비스의 environment에 `- GEMINI_API_KEY=${GEMINI_API_KEY:-}` 추가. api 서비스의 volumes에서 `- model-cache:/root/.cache` 제거 (파싱은 worker 전용 — 이월 항목 정리).
`.env.example`에 `GEMINI_API_KEY=your-key-here` 추가.

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_llm.py -v`
Expected: 2 passed

- [ ] **Step 6: 빌드 + 실 호출 스모크** (GEMINI_API_KEY 필요)

Run: `docker compose up -d --build api ingest-worker && sleep 8 && docker compose exec api python -c "from llm import generate; print(generate('classify', '한 단어로 답하라: 대한민국의 수도는?'))"`
Expected: `서울` 포함 응답 (키 미설정 시 인증 에러 — 사용자에게 .env 설정 요청 후 재시도)

- [ ] **Step 7: Commit**

```bash
git add api/llm.py api/llm_config.json api/tests/test_llm.py api/requirements.txt api/Dockerfile docker-compose.yml .env.example
git commit -m "feat: Gemini 호출 계층(llm.py) 및 용도별 모델 config, 의존성 정리"
```

---

### Task 2: 분류기 (Stage 2)

**Files:**
- Create: `api/pipeline/classify.py`
- Test: `api/tests/test_classify.py`

**Interfaces:**
- Consumes: `llm.generate`, parsed/의 `chunks.json`
- Produces: `classify_chunks(parsed_dir: Path) -> dict` — `{"narrative_ids": [...], "table_ids": [...], "picture_ids": [...]}` 반환하고 `parsed_dir/classification.json`에 저장. table/picture id는 청크 type으로 결정(LLM 불필요), text 청크만 LLM 분류

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_classify.py`:

```python
import json

import pipeline.classify as classify


def _write_chunks(parsed_dir, chunks):
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
    )


def test_classify_routes_types_and_llm_narrative(tmp_path, monkeypatch):
    _write_chunks(tmp_path, [
        {"id": "c001", "type": "text", "page": 1, "text": "정책 배경 설명"},
        {"id": "c002", "type": "text", "page": 1, "text": "목차"},
        {"id": "c003", "type": "table", "page": 2, "ref": "tables/table_001.json"},
        {"id": "c004", "type": "picture", "page": 3, "ref": "figures/fig_001.png"},
    ])
    monkeypatch.setattr(classify.llm, "generate", lambda *a, **k: {
        "classifications": [
            {"id": "c001", "category": "NARRATIVE"},
            {"id": "c002", "category": "METADATA"},
        ]
    })
    result = classify.classify_chunks(tmp_path)
    assert result["narrative_ids"] == ["c001"]
    assert result["table_ids"] == ["c003"]
    assert result["picture_ids"] == ["c004"]
    saved = json.loads((tmp_path / "classification.json").read_text(encoding="utf-8"))
    assert saved == result


def test_classify_no_text_chunks_skips_llm(tmp_path, monkeypatch):
    _write_chunks(tmp_path, [{"id": "c001", "type": "table", "page": None, "ref": "tables/table_001.json"}])
    def boom(*a, **k):
        raise AssertionError("LLM 호출되면 안 됨")
    monkeypatch.setattr(classify.llm, "generate", boom)
    result = classify.classify_chunks(tmp_path)
    assert result == {"narrative_ids": [], "table_ids": ["c001"], "picture_ids": []}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError` (pipeline.classify 없음)

- [ ] **Step 3: 구현**

`api/pipeline/classify.py`:

```python
"""Stage 2: text 청크를 NARRATIVE/METADATA/SKIP으로 분류. 표·그림은 type으로 즉시 라우팅."""
import json
from pathlib import Path

import llm

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": ["NARRATIVE", "METADATA", "SKIP"]},
                },
                "required": ["id", "category"],
            },
        }
    },
    "required": ["classifications"],
}

PROMPT = """다음은 한국 정책문서에서 추출한 텍스트 청크 목록이다. 각 청크를 분류하라.

- NARRATIVE: 정책 배경·논리·맥락, 기술·사업 설명, 추진 체계 등 지식 위키에 반영할 서사
- METADATA: 표지, 목차, 서지정보, 발간 정보
- SKIP: 머리글/바닥글, 쪽번호, 의미 없는 조각

청크 목록:
{chunks}"""


def classify_chunks(parsed_dir: Path) -> dict:
    chunks = json.loads((parsed_dir / "chunks.json").read_text(encoding="utf-8"))
    result = {
        "narrative_ids": [],
        "table_ids": [c["id"] for c in chunks if c["type"] == "table"],
        "picture_ids": [c["id"] for c in chunks if c["type"] == "picture"],
    }
    text_chunks = [c for c in chunks if c["type"] == "text"]
    if text_chunks:
        listing = "\n".join(f"[{c['id']}] {c['text'][:500]}" for c in text_chunks)
        out = llm.generate("classify", PROMPT.format(chunks=listing), schema=CLASSIFY_SCHEMA)
        result["narrative_ids"] = [
            x["id"] for x in out["classifications"] if x["category"] == "NARRATIVE"
        ]
    (parsed_dir / "classification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_classify.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add api/pipeline/classify.py api/tests/test_classify.py
git commit -m "feat: 청크 분류기 (text→NARRATIVE/METADATA/SKIP, 표·그림은 타입 라우팅)"
```

---

### Task 3: 그림 설명 (Gemini 멀티모달)

**Files:**
- Create: `api/pipeline/describe.py`
- Test: `api/tests/test_describe.py`

**Interfaces:**
- Consumes: `llm.generate`, `llm.image_part`, parsed/figures/
- Produces: `describe_figures(parsed_dir: Path, title: str) -> list[dict]` — 각 그림에 대해 `figures/fig_NNN.desc.md` 생성(이미 있으면 재사용 — 재인제스트 비용 절약), `[{"figure": "figures/fig_NNN.png", "text": 설명}]` 반환. 설명은 서사 경로 입력에 합류

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_describe.py`:

```python
import pipeline.describe as describe


def test_describe_writes_desc_and_returns(tmp_path, monkeypatch):
    figs = tmp_path / "figures"
    figs.mkdir(parents=True)
    (figs / "fig_001.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(describe.llm, "image_part", lambda p: {"img": str(p)})
    monkeypatch.setattr(describe.llm, "generate", lambda *a, **k: "추진체계 다이어그램이다.")
    out = describe.describe_figures(tmp_path, "테스트 문서")
    assert out == [{"figure": "figures/fig_001.png", "text": "추진체계 다이어그램이다."}]
    assert (figs / "fig_001.desc.md").read_text(encoding="utf-8") == "추진체계 다이어그램이다."


def test_describe_reuses_existing_desc(tmp_path, monkeypatch):
    figs = tmp_path / "figures"
    figs.mkdir(parents=True)
    (figs / "fig_001.png").write_bytes(b"x")
    (figs / "fig_001.desc.md").write_text("기존 설명", encoding="utf-8")
    def boom(*a, **k):
        raise AssertionError("재사용 시 LLM 호출 금지")
    monkeypatch.setattr(describe.llm, "generate", boom)
    out = describe.describe_figures(tmp_path, "제목")
    assert out == [{"figure": "figures/fig_001.png", "text": "기존 설명"}]


def test_describe_no_figures_dir(tmp_path):
    assert describe.describe_figures(tmp_path, "제목") == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_describe.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/pipeline/describe.py`:

```python
"""그림·차트를 Gemini 멀티모달로 해석해 서사 텍스트로 변환. 설명 파일은 캐시로 재사용."""
from pathlib import Path

import llm

PROMPT = """이 그림은 한국 정책문서 「{title}」에서 추출되었다.
그림이 전달하는 내용을 한국어 2~5문장으로 서술하라.
차트라면 추세·비교 관계를 중심으로 서술하되, 눈금에서 읽은 수치는 추정치임을 명시하라.
추진체계도·조직도라면 구성 요소와 관계를 서술하라."""


def describe_figures(parsed_dir: Path, title: str) -> list[dict]:
    figs = parsed_dir / "figures"
    if not figs.is_dir():
        return []
    results = []
    for png in sorted(figs.glob("fig_*.png")):
        desc_path = figs / f"{png.stem}.desc.md"
        if desc_path.exists():
            text = desc_path.read_text(encoding="utf-8")
        else:
            text = llm.generate(
                "describe_figure", [llm.image_part(png), PROMPT.format(title=title)]
            )
            desc_path.write_text(text, encoding="utf-8")
        results.append({"figure": f"figures/{png.name}", "text": text})
    return results
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_describe.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add api/pipeline/describe.py api/tests/test_describe.py
git commit -m "feat: 그림·차트 Gemini 멀티모달 설명 (desc.md 캐시 재사용)"
```

---

### Task 4: 정형 표 경로 (고정 스키마 매핑 → staging)

**Files:**
- Create: `api/pipeline/map_tables.py`
- Test: `api/tests/test_map_tables.py`

**Interfaces:**
- Consumes: `llm.generate`, `app.db.connect`, parsed/tables/*.json
- Produces: `map_and_stage_tables(parsed_dir: Path, source_id: str) -> dict` — `{"staged": [{"table": str, "rows": int}], "needs_review": int}`. 신뢰도 ≥0.8이면 `staging.<table>`에 INSERT(+source_id), 미만·비대상이면 `public.staging_tables`에 보존

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_map_tables.py`:

```python
import json
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db
import pipeline.map_tables as mt


def _write_table(parsed_dir, payload):
    (parsed_dir / "tables").mkdir(parents=True, exist_ok=True)
    (parsed_dir / "tables" / "table_001.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def _cleanup(source_id):
    with db.connect() as conn:
        conn.execute("DELETE FROM staging.technologies WHERE source_id = %s", (source_id,))
        conn.execute("DELETE FROM staging_tables WHERE source_id = %s", (source_id,))


def test_high_confidence_stages_rows(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "기술 목록", "columns": ["기술명", "분야"],
                            "rows": [["HBM", "반도체"], ["고체전지", "이차전지"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {
        "table": "technologies", "confidence": 0.95,
        "column_mapping": {"기술명": "name", "분야": "field"},
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [{"table": "technologies", "rows": 2}], "needs_review": 0}
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT name, field FROM staging.technologies WHERE source_id = %s ORDER BY name",
                (source_id,),
            ).fetchall()
        assert [r["name"] for r in rows] == ["HBM", "고체전지"]
    finally:
        _cleanup(source_id)


def test_low_confidence_falls_back(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    _write_table(tmp_path, {"table_title": "알 수 없는 표", "columns": ["가", "나"], "rows": [["1", "2"]]})
    monkeypatch.setattr(mt.llm, "generate", lambda *a, **k: {
        "table": "technologies", "confidence": 0.4, "column_mapping": {}
    })
    try:
        out = mt.map_and_stage_tables(tmp_path, source_id)
        assert out == {"staged": [], "needs_review": 1}
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status, mapping_confidence FROM staging_tables WHERE source_id = %s",
                (source_id,),
            ).fetchone()
        assert row["status"] == "needs_review"
        assert abs(row["mapping_confidence"] - 0.4) < 1e-6
    finally:
        _cleanup(source_id)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --with "psycopg[binary]" --no-project python -m pytest tests/test_map_tables.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/pipeline/map_tables.py`:

```python
"""정형 표 경로: 고정 스키마 매핑(LLM) → staging 적재. LLM은 DDL을 만들지 않는다."""
import json
from pathlib import Path

import llm
from app import db

CONFIDENCE_THRESHOLD = 0.8

# LLM 직접 매핑 대상과 허용 컬럼 (FK 연결이 필요한 테이블은 제외 — staging_tables로)
CORE_TABLES = {
    "technologies": ["name", "field", "sub_field", "lead_ministry", "trl_level", "description"],
    "projects": ["project_code", "name", "lead_ministry", "budget_total", "budget_annual",
                 "start_year", "end_year", "status"],
    "policy_events": ["event_date", "event_type", "title", "description"],
    "ministries": ["name", "abbreviation"],
}
INT_COLS = {"trl_level", "budget_total", "budget_annual", "start_year", "end_year"}

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "table": {"type": "string", "enum": list(CORE_TABLES) + ["none"]},
        "confidence": {"type": "number"},
        "column_mapping": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["table", "confidence", "column_mapping"],
}

PROMPT = """한국 정책문서에서 추출한 표를 DB 스키마에 매핑하라.

대상 테이블과 컬럼:
{schema_desc}

표 제목: {title}
표 컬럼: {columns}
샘플 행 (최대 5개): {sample}

이 표가 위 테이블 중 하나에 대응하면 table에 테이블명, column_mapping에 {{"표 컬럼명": "DB 컬럼명"}}을,
대응하지 않으면 table에 "none"을 반환하라. confidence는 매핑 확신도(0~1)."""


def _coerce(col: str, val):
    if val is None or val == "":
        return None
    if col in INT_COLS:
        try:
            return int(float(str(val).replace(",", "")))
        except ValueError:
            return None
    return str(val)


def map_and_stage_tables(parsed_dir: Path, source_id: str) -> dict:
    tables_dir = parsed_dir / "tables"
    result = {"staged": [], "needs_review": 0}
    if not tables_dir.is_dir():
        return result
    schema_desc = "\n".join(f"- {t}: {', '.join(cols)}" for t, cols in CORE_TABLES.items())
    for tf in sorted(tables_dir.glob("table_*.json")):
        payload = json.loads(tf.read_text(encoding="utf-8"))
        out = llm.generate("map_table", PROMPT.format(
            schema_desc=schema_desc,
            title=payload.get("table_title", ""),
            columns=payload["columns"],
            sample=payload["rows"][:5],
        ), schema=MAP_SCHEMA)
        table = out["table"]
        mapping = {s: d for s, d in out["column_mapping"].items()
                   if table in CORE_TABLES and d in CORE_TABLES.get(table, [])}
        if table in CORE_TABLES and out["confidence"] >= CONFIDENCE_THRESHOLD and mapping:
            col_idx = {c: i for i, c in enumerate(payload["columns"])}
            dst_cols = list(mapping.values()) + ["source_id"]
            n = 0
            with db.connect() as conn:
                for row in payload["rows"]:
                    values = [_coerce(dst, row[col_idx[src]]) for src, dst in mapping.items()]
                    conn.execute(
                        f"INSERT INTO staging.{table} ({', '.join(dst_cols)}) "
                        f"VALUES ({', '.join(['%s'] * len(dst_cols))})",
                        values + [source_id],
                    )
                    n += 1
            result["staged"].append({"table": table, "rows": n})
        else:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO staging_tables (source_id, table_title, raw_data, "
                    "suggested_mapping, mapping_confidence) VALUES (%s, %s, %s, %s, %s)",
                    (source_id, payload.get("table_title", ""),
                     json.dumps(payload, ensure_ascii=False),
                     json.dumps(out, ensure_ascii=False), out["confidence"]),
                )
            result["needs_review"] += 1
    return result
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --with "psycopg[binary]" --no-project python -m pytest tests/test_map_tables.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add api/pipeline/map_tables.py api/tests/test_map_tables.py
git commit -m "feat: 표→고정 스키마 매핑 및 staging 적재 (신뢰도 0.8 미만 staging_tables 폴백)"
```

---

### Task 5: 위키 git 조작 (wiki_ops)

**Files:**
- Create: `api/wiki_ops.py`
- Test: `api/tests/test_wiki_ops.py`

**Interfaces:**
- Consumes: Phase 1의 `scripts.init_wiki.init_wiki` (테스트 픽스처용)
- Produces: `list_pages(root) -> list[str]` (tech/entity/events/synthesis/summaries 하위 .md 상대경로), `read_page(root, rel) -> str | None`, `stage_changes(root, source_id, files: dict[str, str], message) -> str` — flock으로 직렬화하여 main에서 `ingest/{source_id}` 브랜치 생성(`-B`), 파일 기록·커밋 후 main 복귀, 브랜치명 반환, `rebuild_index(root) -> str` — 디렉토리 목록으로 index.md 내용 재생성(결정적, LLM 불필요)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_wiki_ops.py`:

```python
import subprocess

from scripts.init_wiki import init_wiki
import wiki_ops


def _git_out(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def test_stage_changes_creates_branch_and_returns_to_main(tmp_path):
    init_wiki(tmp_path)
    branch = wiki_ops.stage_changes(
        tmp_path, "abc123",
        {"tech/hbm.md": "# HBM\n내용", "summaries/abc123.md": "# 요약"},
        "ingest: 테스트 소스",
    )
    assert branch == "ingest/abc123"
    assert _git_out(tmp_path, "branch", "--show-current").strip() == "main"
    assert "ingest/abc123" in _git_out(tmp_path, "branch", "--list", "ingest/*")
    files = _git_out(tmp_path, "ls-tree", "-r", "--name-only", "ingest/abc123")
    assert "tech/hbm.md" in files and "summaries/abc123.md" in files
    assert "tech/hbm.md" not in _git_out(tmp_path, "ls-tree", "-r", "--name-only", "main")


def test_stage_changes_is_rerunnable(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s1", {"tech/a.md": "v1"}, "m1")
    wiki_ops.stage_changes(tmp_path, "s1", {"tech/a.md": "v2"}, "m2")  # -B로 재생성
    content = _git_out(tmp_path, "show", "ingest/s1:tech/a.md")
    assert content == "v2"


def test_list_and_read_pages(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s2", {"tech/b.md": "# B"}, "m")
    assert "tech/b.md" not in wiki_ops.list_pages(tmp_path)  # main 기준
    assert wiki_ops.read_page(tmp_path, "tech/없는페이지.md") is None


def test_rebuild_index_lists_pages(tmp_path):
    init_wiki(tmp_path)
    (tmp_path / "tech" / "hbm.md").write_text("x", encoding="utf-8")
    idx = wiki_ops.rebuild_index(tmp_path)
    assert "tech/hbm" in idx
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests/test_wiki_ops.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/wiki_ops.py`:

```python
"""위키 git 저장소 조작. 쓰기는 ingest/{source_id} 브랜치에만 — main 직접 커밋 금지."""
import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path

PAGE_DIRS = ["tech", "entity", "events", "synthesis", "summaries"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@contextmanager
def _lock(root: Path):
    # ponytail: 프로세스 간 직렬화는 flock 하나로 충분 (단일 호스트 worker 전제), 분산 워커 도입 시 재검토
    with open(root / ".ingest.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def list_pages(root: Path) -> list[str]:
    out = []
    for d in PAGE_DIRS:
        out += sorted(
            str(p.relative_to(root)) for p in (root / d).glob("*.md")
        ) if (root / d).is_dir() else []
    return out


def read_page(root: Path, rel: str) -> str | None:
    p = root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


def rebuild_index(root: Path) -> str:
    lines = ["# NST Wiki 색인", "",
             "국가전략기술 정책 지식 위키. 페이지가 생성·갱신되면 이 색인도 함께 갱신한다.", ""]
    titles = {"tech": "tech (기술 개념)", "entity": "entity (정책 엔티티)",
              "events": "events (정책변화 이력)", "synthesis": "synthesis (종합·비교 분석)"}
    for d, title in titles.items():
        lines += [f"## {title}", ""]
        pages = sorted((root / d).glob("*.md")) if (root / d).is_dir() else []
        lines += [f"- [[{d}/{p.stem}]]" for p in pages] or ["(아직 페이지 없음)"]
        lines.append("")
    return "\n".join(lines)


def stage_changes(root: Path, source_id: str, files: dict[str, str], message: str) -> str:
    branch = f"ingest/{source_id}"
    with _lock(root):
        _git(root, "checkout", "main")
        _git(root, "checkout", "-B", branch)
        try:
            for rel, content in files.items():
                p = root / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            (root / "index.md").write_text(rebuild_index(root), encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", message)
        finally:
            _git(root, "checkout", "main")
    return branch
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests/test_wiki_ops.py -v`
Expected: 4 passed

- [ ] **Step 5: Dockerfile에 반영**

`api/Dockerfile`의 `COPY llm.py llm_config.json ./` 줄을 `COPY llm.py llm_config.json wiki_ops.py ./`로 교체.

- [ ] **Step 6: Commit**

```bash
git add api/wiki_ops.py api/tests/test_wiki_ops.py api/Dockerfile
git commit -m "feat: 위키 git 조작 (ingest 브랜치 스테이징, flock 직렬화, index 재생성)"
```

---

### Task 6: 서사 경로 (페이지 계획 → 병합 → 모순)

**Files:**
- Create: `api/pipeline/narrative.py`
- Test: `api/tests/test_narrative.py`

**Interfaces:**
- Consumes: `llm.generate`, `wiki_ops.list_pages/read_page`
- Produces: `compile_narrative(wiki_root: Path, source_id: str, meta: dict, narrative_texts: list[str]) -> dict` — `{"files": {상대경로: 내용}, "affected_pages": [{"path", "action"}], "contradictions": [...]}`. files에는 소스 요약(`summaries/{source_id}.md`)과 모순 발생 시 갱신된 `contradictions/log.md` 포함. 페이지 갱신 상한 15 초과분은 `action: "suggested"`로만 기록

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_narrative.py`:

```python
from pathlib import Path

from scripts.init_wiki import init_wiki
import pipeline.narrative as narrative


def _fake_llm(plan_pages, merged):
    def fake(purpose, contents, schema=None):
        if purpose == "plan_pages":
            return {"pages": plan_pages}
        if purpose == "merge_page":
            return merged
        raise AssertionError(f"unexpected purpose: {purpose}")
    return fake


def test_compile_narrative_builds_files(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/hbm-semiconductor.md", "action": "create", "title": "HBM 반도체"}],
        merged={"content": "---\ntitle: HBM 반도체\n---\n\n본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src1", {"title": "정책문서"}, ["HBM 설명 서사"])
    assert out["files"]["tech/hbm-semiconductor.md"].endswith("본문")
    assert "summaries/src1.md" in out["files"]
    assert out["affected_pages"] == [{"path": "tech/hbm-semiconductor.md", "action": "create"}]
    assert out["contradictions"] == []


def test_compile_narrative_records_contradictions(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "update", "title": "A"}],
        merged={"content": "새 본문", "contradictions": [
            {"summary": "분야 수 불일치", "existing": "12개", "new": "10개"}
        ]},
    ))
    out = narrative.compile_narrative(tmp_path, "src2", {"title": "문서"}, ["서사"])
    assert len(out["contradictions"]) == 1
    assert "분야 수 불일치" in out["files"]["contradictions/log.md"]


def test_compile_narrative_caps_at_15(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    pages = [{"path": f"tech/t{i}.md", "action": "create", "title": f"T{i}"} for i in range(20)]
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=pages, merged={"content": "본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src3", {"title": "문서"}, ["서사"])
    updated = [p for p in out["affected_pages"] if p["action"] != "suggested"]
    suggested = [p for p in out["affected_pages"] if p["action"] == "suggested"]
    assert len(updated) == 15
    assert len(suggested) == 5
    assert sum(1 for f in out["files"] if f.startswith("tech/")) == 15
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_narrative.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/pipeline/narrative.py`:

```python
"""서사 경로: 페이지 계획(LLM) → 페이지별 병합(LLM) → 모순 기록. 산출물은 파일 dict."""
from datetime import date
from pathlib import Path

import llm
import wiki_ops

MAX_PAGES = 15

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "update"]},
                    "title": {"type": "string"},
                },
                "required": ["path", "action", "title"],
            },
        }
    },
    "required": ["pages"],
}

PLAN_PROMPT = """새 정책문서가 인제스트되었다. 아래 서사 내용을 반영해야 할 위키 페이지 목록을 계획하라.

디렉토리 규칙:
- tech/: 기술 개념 (영문 케밥케이스, 예: hbm-semiconductor.md)
- entity/: 부처·기관 (한글 기관명, 예: 과기정통부.md)
- events/: 정책변화 이력 (YYYY-MM-슬러그.md)
- synthesis/: 종합·비교 분석

기존 페이지 목록 (있으면 update, 없으면 create):
{existing}

문서 제목: {title}
서사 내용:
{narrative}

중요도 순으로 정렬해 반환하라. 반영할 실질 내용이 있는 페이지만 포함하라."""

MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "existing": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["summary", "existing", "new"],
            },
        },
    },
    "required": ["content", "contradictions"],
}

MERGE_PROMPT = """위키 페이지를 갱신하라. 규칙:
- 기존 내용을 보존하며 새 정보를 병합한다 (통째 재작성 금지)
- 페이지는 YAML 프론트매터로 시작: title, type, related_pages, sources (source_id "{source_id}"와 last_updated "{today}"를 sources에 추가)
- 내부 링크는 [[디렉토리/파일명]] 형식, 정형 수치는 본문 하드코딩 대신 [[data:테이블?조건]] 참조
- 기존 서술과 새 정보가 충돌하면 본문을 임의로 교체하지 말고 contradictions에 기록하라

페이지 경로: {path} (title: {title})
기존 내용 (신규 페이지면 빈 값):
{current}

새 정보 (문서 「{doc_title}」에서 추출):
{narrative}

이 페이지에 관련된 내용만 반영하고, content에 페이지 전문을 반환하라."""


def compile_narrative(wiki_root: Path, source_id: str, meta: dict,
                      narrative_texts: list[str]) -> dict:
    today = date.today().isoformat()
    narrative = "\n\n".join(narrative_texts)
    existing = "\n".join(wiki_ops.list_pages(wiki_root)) or "(없음)"
    plan = llm.generate("plan_pages", PLAN_PROMPT.format(
        existing=existing, title=meta.get("title", ""), narrative=narrative,
    ), schema=PLAN_SCHEMA)

    files: dict[str, str] = {}
    affected, contradictions = [], []
    for i, page in enumerate(plan["pages"]):
        if i >= MAX_PAGES:
            affected.append({"path": page["path"], "action": "suggested"})
            continue
        current = wiki_ops.read_page(wiki_root, page["path"]) or ""
        merged = llm.generate("merge_page", MERGE_PROMPT.format(
            source_id=source_id, today=today, path=page["path"], title=page["title"],
            current=current, doc_title=meta.get("title", ""), narrative=narrative,
        ), schema=MERGE_SCHEMA)
        files[page["path"]] = merged["content"]
        affected.append({"path": page["path"], "action": page["action"]})
        for c in merged["contradictions"]:
            contradictions.append({**c, "page": page["path"]})

    files[f"summaries/{source_id}.md"] = (
        f"# {meta.get('title', source_id)}\n\n- source_id: {source_id}\n"
        f"- ingest: {today}\n\n{narrative[:2000]}\n"
    )
    if contradictions:
        log = wiki_ops.read_page(wiki_root, "contradictions/log.md") or ""
        rows = "".join(
            f"| {source_id[:8]}-{i+1} | {today} | {c['page']} | {c['existing']} | {c['new']} | 미해결 |\n"
            for i, c in enumerate(contradictions)
        )
        files["contradictions/log.md"] = log.rstrip("\n") + "\n" + rows
    return {"files": files, "affected_pages": affected, "contradictions": contradictions}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_narrative.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add api/pipeline/narrative.py api/tests/test_narrative.py
git commit -m "feat: 서사 경로 (페이지 계획·병합·모순 기록, 상한 15)"
```

---

### Task 7: 오케스트레이션 + E2E (parsed→staged)

**Files:**
- Create: `api/pipeline/compile.py`
- Modify: `api/tasks.py`, `api/app/db.py`
- Test: `api/tests/test_compile.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 2~6의 전 모듈, `app.db`
- Produces: `compile_source(source_dir: Path, source_id: str, wiki_root: Path) -> dict` — 분류→그림 설명→표 매핑→서사→브랜치 커밋을 수행하고 `{"affected_pages", "affected_tables", "contradictions", "branch"}` 반환. `db.save_results(task_id, results)` 추가. `run_ingest`가 `parsing→parsed(파싱 직후)→classifying→staged`로 확장, celery `task_time_limit=1800`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_compile.py`:

```python
import json
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from scripts.init_wiki import init_wiki
import pipeline.compile as compile_mod


def test_compile_source_full_flow(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    init_wiki(wiki)
    src = tmp_path / "src"
    parsed = src / "parsed"
    parsed.mkdir(parents=True)
    (src / "metadata.json").write_text(json.dumps({"title": "테스트 문서"}), encoding="utf-8")
    (parsed / "chunks.json").write_text(json.dumps([
        {"id": "c001", "type": "text", "page": 1, "text": "정책 서사"},
    ], ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(compile_mod.classify, "classify_chunks",
                        lambda p: {"narrative_ids": ["c001"], "table_ids": [], "picture_ids": []})
    monkeypatch.setattr(compile_mod.describe, "describe_figures", lambda p, t: [])
    monkeypatch.setattr(compile_mod.map_tables, "map_and_stage_tables",
                        lambda p, s: {"staged": [], "needs_review": 0})
    monkeypatch.setattr(compile_mod.narrative, "compile_narrative",
                        lambda root, sid, meta, texts: {
                            "files": {"tech/a.md": "본문"},
                            "affected_pages": [{"path": "tech/a.md", "action": "create"}],
                            "contradictions": [],
                        })
    source_id = str(uuid.uuid4())
    out = compile_mod.compile_source(src, source_id, wiki)
    assert out["branch"] == f"ingest/{source_id}"
    assert out["affected_pages"] == [{"path": "tech/a.md", "action": "create"}]
    assert out["affected_tables"] == {"staged": [], "needs_review": 0}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --with "psycopg[binary]" --no-project python -m pytest tests/test_compile.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/pipeline/compile.py`:

```python
"""Stage 2~5 오케스트레이션: 분류 → 그림 설명 → 표 매핑 → 서사 → 위키 브랜치 커밋."""
import json
from pathlib import Path

from pipeline import classify, describe, map_tables, narrative
import wiki_ops


def compile_source(source_dir: Path, source_id: str, wiki_root: Path) -> dict:
    meta = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
    parsed = source_dir / "parsed"
    cls = classify.classify_chunks(parsed)
    chunks = {c["id"]: c for c in json.loads((parsed / "chunks.json").read_text(encoding="utf-8"))}
    texts = [chunks[i]["text"] for i in cls["narrative_ids"]]
    texts += [f"[그림 {d['figure']}] {d['text']}"
              for d in describe.describe_figures(parsed, meta.get("title", ""))]
    affected_tables = map_tables.map_and_stage_tables(parsed, source_id)
    branch = None
    affected_pages, contradictions = [], []
    if texts:
        nar = narrative.compile_narrative(wiki_root, source_id, meta, texts)
        branch = wiki_ops.stage_changes(
            wiki_root, source_id, nar["files"],
            f"ingest: {meta.get('title', source_id)} (source: {source_id})",
        )
        affected_pages, contradictions = nar["affected_pages"], nar["contradictions"]
    return {"branch": branch, "affected_pages": affected_pages,
            "affected_tables": affected_tables, "contradictions": contradictions}
```

`api/app/db.py` 끝에 추가:

```python
def save_results(task_id: str, results: dict) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET affected_pages = %s, affected_tables = %s, "
            "contradictions = %s, branch_name = %s WHERE task_id = %s",
            (json.dumps(results["affected_pages"], ensure_ascii=False),
             json.dumps(results["affected_tables"], ensure_ascii=False),
             json.dumps(results["contradictions"], ensure_ascii=False),
             results["branch"], task_id),
        )
```

(파일 상단 import에 `import json` 추가)

`api/tasks.py`의 `run_ingest`를 다음으로 교체 (celery 앱 정의는 유지, `@celery.task` 데코레이터에 `time_limit=1800` 추가):

```python
@celery.task(name="ingest.run", time_limit=1800)
def run_ingest(task_id: str) -> None:
    task = db.get_task(task_id)
    db.set_status(task_id, "parsing")
    try:
        from pipeline.compile import compile_source
        from pipeline.parse import run_pipeline

        source_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / task["source_id"]
        run_pipeline(source_dir)
        db.set_status(task_id, "classifying")
        wiki_root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
        results = compile_source(source_dir, task["source_id"], wiki_root)
        db.save_results(task_id, results)
        db.set_status(task_id, "staged")
    except Exception as e:
        db.set_status(task_id, "failed", error=str(e))
        raise
```

기존 `test_tasks.py`의 두 테스트는 상태 기대값이 바뀐다: `test_run_ingest_md_reaches_parsed`는 LLM 호출로 이어지므로 `compile_source`를 monkeypatch해 `staged` 도달을 검증하도록 수정:

```python
def test_run_ingest_md_reaches_staged(tmp_path, monkeypatch):
    import tasks as tasks_mod
    source_id = str(uuid.uuid4())
    src_dir = tmp_path / source_id
    src_dir.mkdir()
    (src_dir / "original.md").write_text("# 제목\n본문", encoding="utf-8")
    (src_dir / "metadata.json").write_text('{"title": "t"}', encoding="utf-8")
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    import pipeline.compile as compile_mod
    monkeypatch.setattr(compile_mod, "compile_source", lambda *a, **k: {
        "branch": None, "affected_pages": [], "affected_tables": {}, "contradictions": []})
    task_id = str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        tasks_mod.run_ingest(task_id)
        assert db.get_task(task_id)["status"] == "staged"
    finally:
        db.delete_task(task_id)
```

(함수명도 `_reaches_staged`로 바꾸고, 실패 테스트는 그대로 두되 최종 상태 `failed` 기대 유지)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --no-project python -m pytest tests/test_compile.py tests/test_tasks.py -v`
Expected: 3 passed

- [ ] **Step 5: E2E 관통 검증** (실 Gemini 호출 — GEMINI_API_KEY 필요)

Run:
```bash
docker compose up -d --build api ingest-worker
cat > /tmp/e2e-policy.md << 'EOF'
# 국가전략기술 테스트 문서

## 배경

HBM 반도체는 AI 반도체의 핵심 기술로, 반도체 분야의 우선기술로 지정되었다.
과기정통부가 주관 부처를 맡는다.
EOF
TASK=$(curl -s -X POST http://localhost:8000/api/v1/ingest -H "X-Admin-Key: devkey" \
  -F "file=@/tmp/e2e-policy.md" -F "title=E2E 테스트 정책문서" | python3 -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
sleep 60 && curl -s http://localhost:8000/api/v1/ingest/$TASK/status
```
Expected: `"status": "staged"`, `affected_pages`에 1개 이상 페이지

Run: `docker compose exec api git -C /data/wiki branch --list "ingest/*" && SRC=$(docker compose exec postgres psql -U wiki -d llm_wiki -tAc "SELECT source_id FROM ingest_tasks WHERE task_id='$TASK'") && docker compose exec api git -C /data/wiki show ingest/$SRC --stat | head -15`
Expected: `ingest/{source_id}` 브랜치 존재, 커밋에 tech/ 또는 entity/ 페이지 + summaries/ 포함, main은 변경 없음

- [ ] **Step 6: README 갱신**

`README.md`의 인제스트 섹션 마지막 문장 "LLM 분류·위키 반영은 Phase 2b."를 다음으로 교체:

```markdown
파싱 후 Gemini가 내용을 분류·해석하여 서사는 위키 스테이징 브랜치(`ingest/{source_id}`)에,
표는 PostgreSQL `staging` 스키마에 적재한다 (status: `staged`). 승인·병합 UI는 Phase 3.
`.env`에 `GEMINI_API_KEY` 필수.
```

- [ ] **Step 7: 전체 회귀 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --no-project python -m pytest tests -v`
Expected: 30 passed (기존 13 + llm 2 + classify 2 + describe 3 + map_tables 2 + wiki_ops 4 + narrative 3 + compile 1 — 합계가 다르면 실제 수를 보고서에 기록하되 전부 passed여야 함)

- [ ] **Step 8: Commit**

```bash
git add api/pipeline/compile.py api/tasks.py api/app/db.py api/tests/test_compile.py api/tests/test_tasks.py README.md
git commit -m "feat: 인제스트 오케스트레이션 parsed→classifying→staged 및 E2E 관통"
```

---

### Task 8: 이월 항목 정리 (보안·테스트 공백)

**Files:**
- Modify: `api/app/ingest_api.py`
- Test: `api/tests/test_ingest_api.py` (추가)

**Interfaces:**
- Consumes: 기존 `require_admin`, `ingest` 엔드포인트
- Produces: admin key 비교를 `hmac.compare_digest`로 교체, 업로드 행복 경로 테스트(celery enqueue monkeypatch) 추가

- [ ] **Step 1: 실패하는 테스트 작성** (`api/tests/test_ingest_api.py`에 추가)

```python
def test_ingest_happy_path_md(tmp_path, monkeypatch):
    import tasks as tasks_mod
    calls = []
    monkeypatch.setattr(tasks_mod.run_ingest, "delay", lambda tid: calls.append(tid))
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.md", "# 제목\n본문".encode())},
        data={"title": "행복 경로", "tags": "NEXT, 반도체"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "queued"
    assert calls == [body["task_id"]]
    import json as _json
    src_dirs = list(tmp_path.iterdir())
    assert len(src_dirs) == 1
    meta = _json.loads((src_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
    assert meta["title"] == "행복 경로"
    assert meta["tags"] == ["NEXT", "반도체"]
    assert meta["file_hash"].startswith("sha256:")
    from app import db
    db.delete_task(body["task_id"])
```

- [ ] **Step 2: 테스트 실행** (행복 경로는 기존 코드로도 통과할 수 있음 — 통과하면 그대로 진행)

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with google-genai --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: 4 passed (신규 포함)

- [ ] **Step 3: hmac 교체**

`api/app/ingest_api.py`의 `require_admin`을:

```python
import hmac


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if not hmac.compare_digest(x_admin_key, os.environ["ADMIN_API_KEY"]):
        raise HTTPException(status_code=401, detail="invalid admin key")
```

(`import hmac`은 파일 상단 import 블록에 추가)

- [ ] **Step 4: 테스트 재확인**

Run: 같은 명령
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/ingest_api.py api/tests/test_ingest_api.py
git commit -m "fix: admin key 상수시간 비교(hmac) 및 업로드 행복 경로 테스트 추가"
```
