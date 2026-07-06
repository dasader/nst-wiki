# Gemini 네이티브 PDF 파싱 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF 파싱을 로컬 Docling에서 Gemini 멀티모달 2회 호출로 교체하고, Docling·describe_figure를 제거한다.

**Architecture:** `parse_pdf()`가 Call 1(마크다운·그림 인라인·표 플레이스홀더) + Call 2(구조화 표 JSON)를 호출해 기존 산출물 계약(`document.md`, `tables/*.json`, `chunks.json`)을 재현한다. 다운스트림(classify·map_tables·narrative·events)은 무변경. describe_figure 스테이지와 Docling 의존성은 삭제.

**Tech Stack:** Python, google-genai 2.10, FastAPI/Celery 파이프라인, pytest.

## Global Constraints

- 프로젝트 언어 한국어(주석·프롬프트). ponytail: 최소 diff.
- 브랜치 `feat/gemini-pdf-parse`에서 작업, main 직접 커밋 금지.
- **다운스트림 계약 불변**: `chunks.json` 청크는 `{id,type:"text",page,text}` 또는 `{id,type:"table",page,ref}`. `tables/table_NNN.json`은 `{table_title, columns:[str], rows:[[str]]}`. classify는 `type`으로 라우팅.
- **캐싱**: 두 Gemini 호출 모두 `contents`의 **맨 앞에 PDF Part** — 암묵적 캐싱 활성화 조건.
- 테스트 실행:
  `cd api && uv run --with pytest --with fastapi --with httpx --with 'psycopg[binary]' --with redis --with python-multipart --with celery --no-project python -m pytest tests/<file> -q`
- 실 Gemini API 호출은 Task 6(검증)에서만. 단위 테스트는 `llm.generate` monkeypatch로 API 없이.

---

### Task 1: `llm.pdf_part` 헬퍼 + purpose별 타임아웃

**Files:**
- Modify: `api/llm.py`
- Modify: `api/llm_config.json`
- Test: `api/tests/test_llm.py`

**Interfaces:**
- Produces: `llm.pdf_part(path: Path) -> types.Part` (PDF bytes → Part). `resolve_config("parse_markdown"|"parse_tables")`가 `thinking_level="low"`, `timeout_ms=180000` 반환. `generate`는 `cfg.get("timeout_ms", _TIMEOUT_MS)`로 클라이언트 타임아웃 설정.

- [ ] **Step 1: 실패 테스트 작성** — `api/tests/test_llm.py`에 추가:

```python
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_llm.py -q`
Expected: FAIL (`pdf_part` 없음 / config 키 없음)

- [ ] **Step 3: 구현** — `api/llm_config.json`의 항목에 추가:

```json
  "parse_markdown": {"thinking_level": "low", "timeout_ms": 180000},
  "parse_tables": {"thinking_level": "low", "timeout_ms": 180000},
```

`api/llm.py`에 `image_part` 아래로 `pdf_part` 추가:

```python
def pdf_part(path: Path):
    from google.genai import types

    return types.Part.from_bytes(data=path.read_bytes(), mime_type="application/pdf")
```

`generate` 내 클라이언트 생성 타임아웃을 purpose 설정에서:

```python
    client = genai.Client(  # GEMINI_API_KEY 환경변수 자동 인식
        http_options=types.HttpOptions(timeout=cfg.get("timeout_ms", _TIMEOUT_MS))
    )
```

(`llm.py` 상단에 `from pathlib import Path` 이미 없으면 추가 — `pdf_part` 타입힌트용. 런타임엔 불필요하나 일관성 위해.)

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_llm.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add api/llm.py api/llm_config.json api/tests/test_llm.py
git commit -m "feat: llm.pdf_part 헬퍼 + parse purpose별 타임아웃"
```

---

### Task 2: 공유 마크다운 청커 추출

**Files:**
- Modify: `api/pipeline/parse.py`
- Test: `api/tests/test_parse.py`

**Interfaces:**
- Produces: `parse.chunk_markdown(md: str) -> list[str]` — 마크다운을 헤딩(`#`) 경계로 분할한 비어있지 않은 섹션 문자열 리스트. `parse_md`는 이를 사용하도록 리팩터(동작 불변).

- [ ] **Step 1: 실패 테스트 작성** — `api/tests/test_parse.py`에 추가:

```python
def test_chunk_markdown_splits_on_headings():
    from pipeline.parse import chunk_markdown
    md = "머리말\n# 1장\n가나다\n## 1.1\n라마바\n# 2장\n사아자"
    secs = chunk_markdown(md)
    assert secs[0] == "머리말"
    assert secs[1].startswith("# 1장")
    assert any("## 1.1" in s for s in secs)
    assert secs[-1].startswith("# 2장")
    assert "" not in secs  # 빈 섹션 제거
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with pandas --with openpyxl --no-project python -m pytest tests/test_parse.py::test_chunk_markdown_splits_on_headings -q`
Expected: FAIL (`chunk_markdown` 없음)

- [ ] **Step 3: 구현** — `api/pipeline/parse.py`에 함수 추가하고 `parse_md`가 이를 쓰도록 교체:

```python
def chunk_markdown(md: str) -> list[str]:
    """마크다운을 헤딩(#) 경계로 분할한 비어있지 않은 섹션 리스트."""
    sections, cur = [], []
    for line in md.splitlines():
        if line.startswith("#") and cur:
            sections.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    return [s for s in sections if s]
```

`parse_md` 본문의 섹션 분할부를 다음으로 대체:

```python
def parse_md(src: Path, out: Path) -> None:
    text = src.read_text(encoding="utf-8")
    (out / "document.md").write_text(text, encoding="utf-8")
    chunks = [
        {"id": f"c{i:03d}", "type": "text", "page": None, "text": s}
        for i, s in enumerate(chunk_markdown(text), 1)
    ]
    _write_chunks(out, chunks)
```

- [ ] **Step 4: 테스트 통과 확인** (신규 테스트 + 기존 parse_md 회귀)

Run: `cd api && uv run --with pytest --with pandas --with openpyxl --no-project python -m pytest tests/test_parse.py -q`
Expected: PASS (기존 md 파싱 테스트 포함 전부)

- [ ] **Step 5: 커밋**

```bash
git add api/pipeline/parse.py api/tests/test_parse.py
git commit -m "refactor: 마크다운 청커를 chunk_markdown로 공유 추출"
```

---

### Task 3: `parse_pdf.py` Gemini 2-call 재작성

**Files:**
- Rewrite: `api/pipeline/parse_pdf.py`
- Test: `api/tests/test_parse_pdf.py` (신설)

**Interfaces:**
- Consumes: `llm.pdf_part` (Task 1), `llm.generate`, `parse.chunk_markdown` (Task 2).
- Produces: `parse_pdf(src: Path, out: Path) -> None` — `out/document.md`, `out/tables/table_NNN.json`, `out/chunks.json` 생성. `_fit_cells(rows, n)` 셀 정합 가드.

- [ ] **Step 1: 실패 테스트 작성** — `api/tests/test_parse_pdf.py` 신설:

```python
import json
from pathlib import Path

import pipeline.parse_pdf as ppdf


def _fake_generate(purpose, contents, schema=None):
    # contents[0]은 PDF Part여야 함(캐시 조건) — 타입 존재만 확인
    assert contents and hasattr(contents[0], "inline_data")
    if purpose == "parse_markdown":
        return "# 제목\n본문 [표: 예산] 과 [그림: 조직도] 포함\n## 2절\n내용 문단"
    return {"tables": [{
        "table_title": "예산", "page": 3,
        "columns": ["항목", "금액"],
        "rows": [{"cells": ["연구", "100"]}, {"cells": ["운영"]}],  # 셀 부족 행
    }]}


def test_parse_pdf_produces_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(ppdf.llm, "generate", _fake_generate)
    src = tmp_path / "original.pdf"
    src.write_bytes(b"%PDF-1.4")
    out = tmp_path / "parsed"
    out.mkdir()
    ppdf.parse_pdf(src, out)

    # 표 파일: Docling과 동일 스키마 + 셀 정합 가드(부족분 '' 패딩)
    t = json.loads((out / "tables" / "table_001.json").read_text(encoding="utf-8"))
    assert t == {"table_title": "예산", "columns": ["항목", "금액"],
                 "rows": [["연구", "100"], ["운영", ""]]}

    # chunks.json: 텍스트 청크(플레이스홀더 보존) + 표 청크
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    texts = [c for c in chunks if c["type"] == "text"]
    tables = [c for c in chunks if c["type"] == "table"]
    assert any("[표: 예산]" in c["text"] for c in texts)
    assert any("[그림: 조직도]" in c["text"] for c in texts)
    assert tables == [{"id": "t001", "type": "table", "page": 3,
                       "ref": "tables/table_001.json"}]
    assert (out / "document.md").read_text(encoding="utf-8").startswith("# 제목")


def test_parse_pdf_rejects_empty_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(ppdf.llm, "generate",
                        lambda p, c, schema=None: "" if p == "parse_markdown" else {"tables": []})
    src = tmp_path / "original.pdf"; src.write_bytes(b"%PDF")
    out = tmp_path / "parsed"; out.mkdir()
    import pytest
    with pytest.raises(ValueError, match="빈 마크다운"):
        ppdf.parse_pdf(src, out)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_parse_pdf.py -q`
Expected: FAIL (현 parse_pdf는 Docling 시그니처)

- [ ] **Step 3: 구현** — `api/pipeline/parse_pdf.py` 전체 교체:

```python
"""Gemini 네이티브 PDF 파싱 (Docling 대체). 2회 호출: 마크다운 + 구조화 표.
두 호출 모두 PDF Part를 맨 앞에 둬 암묵적 캐싱을 활성화한다."""
import json
from pathlib import Path

import llm
from pipeline.parse import chunk_markdown

MD_PROMPT = (
    "이 정부 정책 PDF 문서 전체를 구조 보존 마크다운으로 변환하라.\n"
    "- 본문 내용을 생략하지 말 것(쪽번호·머리말 반복만 생략).\n"
    "- 표는 전체 그리드 대신 `[표: 간결한제목]` 한 줄 플레이스홀더로만 표기.\n"
    "- 그림·차트·조직도는 `[그림: 한 줄 요약]`으로 본문 흐름에 인라인. 차트 수치는 추정임을 명시.\n"
    "- 숫자·연월일·부처명·연락처는 원문 그대로."
)

TABLES_PROMPT = (
    "이 정부 정책 PDF의 모든 표를 구조화해 추출하라.\n"
    "- table_title: 표 제목(없으면 인접 맥락으로 간결히). page: 쪽번호.\n"
    "- columns: 헤더 행. 없으면 성격에 맞게 명명.\n"
    "- rows: 각 행의 cells(열 순서대로). 병합셀 값 채움. 목차·점선 표 제외.\n"
    "- 숫자·연월일·부처명은 원문 그대로. 셀 개수는 columns 길이에 맞춰라."
)

TABLE_SCHEMA = {
    "type": "object",
    "properties": {"tables": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "table_title": {"type": "string"},
            "page": {"type": "integer"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {"cells": {"type": "array", "items": {"type": "string"}}},
                "required": ["cells"]}},
        },
        "required": ["table_title", "columns", "rows"]}}},
    "required": ["tables"],
}


def _fit_cells(rows: list, n: int) -> list:
    """LLM 셀 개수 오차 방어: 부족분 '' 패딩, 초과분 절단. (스파이크선 0건이나 방어적)"""
    return [(r + [""] * n)[:n] for r in rows]


def parse_pdf(src: Path, out: Path) -> None:
    pdf = llm.pdf_part(src)  # 맨 앞 배치 → 암묵적 캐시

    # Call 1: 마크다운 (그림 인라인, 표 플레이스홀더)
    md = llm.generate("parse_markdown", [pdf, MD_PROMPT])
    if not md or not md.strip():
        raise ValueError(f"빈 마크다운 파싱 결과: {src}")
    (out / "document.md").write_text(md, encoding="utf-8")

    # Call 2: 구조화 표 → Docling과 동일한 tables/*.json 스키마
    data = llm.generate("parse_tables", [pdf, TABLES_PROMPT], schema=TABLE_SCHEMA)
    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)
    table_chunks = []
    for i, t in enumerate(data.get("tables", []), 1):
        cols = t["columns"]
        rows = _fit_cells([r["cells"] for r in t["rows"]], len(cols))
        ref = f"tables/table_{i:03d}.json"
        (out / ref).write_text(
            json.dumps({"table_title": t.get("table_title", ""),
                        "columns": cols, "rows": rows}, ensure_ascii=False),
            encoding="utf-8")
        table_chunks.append({"id": f"t{i:03d}", "type": "table",
                             "page": t.get("page"), "ref": ref})

    # chunks.json: 텍스트 청크(플레이스홀더 보존) + 표 청크
    text_chunks = [
        {"id": f"c{i:03d}", "type": "text", "page": None, "text": s}
        for i, s in enumerate(chunk_markdown(md), 1)
    ]
    (out / "chunks.json").write_text(
        json.dumps(text_chunks + table_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with google-genai --no-project python -m pytest tests/test_parse_pdf.py -q`
Expected: PASS (2 tests)

- [ ] **Step 5: 커밋**

```bash
git add api/pipeline/parse_pdf.py api/tests/test_parse_pdf.py
git commit -m "feat: parse_pdf를 Gemini 2-call 파싱으로 재작성 (Docling 대체)"
```

---

### Task 4: describe_figure 스테이지 제거

**Files:**
- Modify: `api/pipeline/compile.py`
- Delete: `api/pipeline/describe.py`, `api/tests/test_describe.py`
- Modify: `api/llm_config.json`
- Test: `api/tests/test_compile.py` (기존 회귀)

**Interfaces:**
- Consumes: Task 3의 chunks(그림이 텍스트에 인라인). Produces: `compile.py`가 `describe` 미참조.

- [ ] **Step 1: 회귀 테스트 확인(현행 통과 상태 기록)**

Run: `cd api && uv run --with pytest --with 'psycopg[binary]' --with fastapi --with httpx --no-project python -m pytest tests/test_compile.py -q`
Expected: PASS (변경 전 기준선)

- [ ] **Step 2: describe 배선 제거** — `api/pipeline/compile.py`:
  - import 라인 `from pipeline import classify, describe, events, map_tables, narrative` 에서 `describe` 제거.
  - 다음 두 줄 삭제:

```python
    texts += [f"[그림 {d['figure']}] {d['text']}"
              for d in describe.describe_figures(parsed, meta.get("title", ""))]
```

  - 모듈 docstring "분류 → 그림 설명 → 표 매핑 …"에서 "그림 설명 → " 제거.

- [ ] **Step 3: 파일·설정 삭제**

```bash
git rm api/pipeline/describe.py api/tests/test_describe.py
```

  `api/llm_config.json`에서 `"describe_figure": {},` 라인 삭제.

- [ ] **Step 4: 회귀 테스트 통과 확인** — compile이 describe 없이 동작:

Run: `cd api && uv run --with pytest --with 'psycopg[binary]' --with fastapi --with httpx --no-project python -m pytest tests/test_compile.py -q`
Expected: PASS

만약 `test_compile.py`가 `describe.describe_figures`를 monkeypatch하고 있으면 그 setattr 라인을 제거한다(더 이상 호출 경로 없음).

- [ ] **Step 5: 커밋**

```bash
git add -A
git commit -m "refactor: describe_figure 스테이지 제거 (그림은 파싱시 인라인 흡수)"
```

---

### Task 5: Docling 의존성·Dockerfile 정리

**Files:**
- Modify: `api/requirements.txt`
- Modify: `api/Dockerfile`

**Interfaces:** 런타임에 docling/easyocr 미참조(Task 3에서 제거됨) → 의존성 삭제 안전.

- [ ] **Step 1: 잔존 참조 없음 확인**

Run: `cd api && grep -rniE "docling|easyocr" --include=*.py . || echo CLEAN`
Expected: `CLEAN` (코드에서 docling/easyocr 임포트 전무)

- [ ] **Step 2: requirements 정리** — `api/requirements.txt`에서 다음 두 줄 삭제:

```
docling>=2.15,<3
easyocr
```

- [ ] **Step 3: Dockerfile 정리** — `api/Dockerfile`에서 EasyOCR 사전 다운로드 블록 삭제:

```dockerfile
# EasyOCR ko/en 모델을 빌드 시 사전 다운로드 → 런타임 동시 다운로드 레이스(/root/.EasyOCR/model/temp.zip) 방지
RUN python -c "import easyocr; easyocr.Reader(['ko','en'], gpu=False)"
```

- [ ] **Step 4: 빌드·임포트 검증** (통합 시점, 실 docker)

```bash
cd /home/dev/code/nst-wiki && docker compose build api ingest-worker
docker compose run --rm --no-deps api python -c "import pipeline.parse_pdf; print('ok, no docling')"
```
Expected: 빌드 성공(이미지 대폭 축소), 임포트 ok.

- [ ] **Step 5: 커밋**

```bash
git add api/requirements.txt api/Dockerfile
git commit -m "chore: Docling·EasyOCR 의존성 및 모델 baking 제거"
```

---

### Task 6: 실 API 검증 (스펙 §10 게이트)

**Files:**
- Scratch only: `scratchpad/validate_parse.py` (커밋 안 함)

**Interfaces:** 실 인재확보 PDF를 새 parse_pdf로 파싱해 3개 검증 항목 확인. 코드 변경 없음 — 통과 실패 시 Task 3 프롬프트 조정.

- [ ] **Step 1: 검증 스크립트 작성** — 컨테이너에서 실행, `parse_pdf(pdf, out)` 후:
  1. `chunks.json` 텍스트에 앵커(전략기술특화연구소, 683061, 044-202-6751 등 §Step1 24개) 정규화 커버리지 ≥ 22/24.
  2. `tables/*.json` ≥ 6개, NRI-고용보험 결합률 표에 `511,601`·`74.9%` 존재.
  3. **캐시 히트**: Call 2를 client 직접 호출로 재현해 `usage_metadata.cached_content_token_count > 0` 확인(또는 두 호출 연속 시 두 번째의 cached 토큰 로깅).
  4. **인라인 그림**: `[그림:` 마커 ≥ 1개이고 의미 있는 한 줄인지 육안.

- [ ] **Step 2: 파싱→narrative 스모크** — 파싱 산출물로 `compile_source`를 dry하게 태워(또는 classify+map_tables만) 표 플레이스홀더가 narrative 품질을 떨어뜨리지 않는지 확인. staged 표 개수·narrative 텍스트 육안.

- [ ] **Step 3: 결과 기록** — 3개 게이트 통과 여부를 요약. 실패 항목 있으면:
  - 앵커 누락 → MD_PROMPT의 "생략 금지" 강화
  - 캐시 미스 → 두 호출 간격/PDF-first 배치 점검, 필요 시 명시적 캐시(업그레이드 경로)
  - 인라인 그림 부실 → MD_PROMPT 그림 지침 구체화

- [ ] **Step 4: 스크래치 정리** — 컨테이너 임시파일 삭제. 커밋 없음(검증 통과 기록만 PR 설명에).

---

## 마무리

전 태스크 완료 후 superpowers:finishing-a-development-branch로 PR 생성 → main 머지(브랜치 워크플로). PR 설명에 Task 6 검증 결과 요약 첨부.
