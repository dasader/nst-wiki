# Phase 2a: 인제스트 수용·파싱 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PDF/MD/XLSX 문서를 업로드하면 Celery 워커가 포맷별로 파싱하여 `parsed/` 산출물(document.json, document.md, chunks.json, tables/, figures/)을 생성하고 ingest_tasks 상태로 추적한다.

**Architecture:** 스펙(docs/superpowers/specs/2026-07-04-kistep-llm-wiki-design.md) 3절·4.1절의 Stage 0~1을 구현한다. FastAPI가 업로드를 받아 `/data/sources/{source_id}/`에 원본·메타데이터를 저장하고 Celery(Redis 브로커)에 태스크를 넘긴다. 워커는 확장자로 분기해 PDF는 Docling(TableFormer ACCURATE + EasyOCR ko/en), XLSX는 pandas, MD는 헤딩 분할로 파싱한다. LLM 호출(Stage 2 이후)은 Phase 2b가 담당한다 — Phase 2는 2a(본 계획)와 2b(LLM 컴파일)로 분해되었다.

**Tech Stack:** FastAPI, Celery 5(Redis 브로커), Docling 2.x, pandas+openpyxl, psycopg 3

## Global Constraints

- 컨테이너 스택은 Phase 1 그대로. 새 파이썬 의존성은 `api/requirements.txt`에만 추가
- Docling 설정: TableFormer `ACCURATE` 모드 + EasyOCR `["ko", "en"]` (스펙 4.1·8.2절)
- parsed/ 산출물 규약(스펙 3절): `document.json`, `document.md`, `chunks.json`, `tables/table_NNN.json`, `figures/fig_NNN.png` (NNN은 1부터 3자리)
- chunks.json 규약: `[{"id": "cNNN", "type": "text|table|picture", "page": int|null, "text": "...", "ref": "tables/... 또는 figures/..."}]` — text 청크는 `text` 필드, table/picture 청크는 `ref` 필드
- 상태 전이(2a 범위): `queued → parsing → parsed | failed(+error)`. `classifying` 이후는 Phase 2b
- `POST /api/v1/ingest`는 `X-Admin-Key` 헤더 필수(스펙 6.1·8.3절), 키는 환경변수 `ADMIN_API_KEY`
- 지원 포맷은 `.pdf` `.md` `.xlsx`만. 그 외 업로드는 HTTP 400 (스펙 1.4절)
- 커밋 메시지·문서는 한국어
- **범위 밖 (만들지 말 것):** Gemini/LLM 호출, 분류기, 서사·표 경로, 스테이징 브랜치(모두 Phase 2b), frontend, admin key 외 인증, rate limit
- 스펙 3절의 `ingest_log.json`은 만들지 않는다 — 인제스트 이력은 `ingest_tasks` 테이블이 담당 (의도된 스펙 단순화)
- 테스트는 호스트에서 `uv run --with ... --no-project python -m pytest` (Phase 1과 동일 패턴). DB 통합 테스트는 compose postgres의 127.0.0.1:5433 포트 사용

---

### Task 1: 의존성·이미지·compose 보강

**Files:**
- Modify: `api/requirements.txt` (전체 교체)
- Modify: `docker-compose.yml`
- Modify: `.env.example`

**Interfaces:**
- Produces: api 이미지에 celery/pandas/openpyxl/docling 포함. compose에 `model-cache` 볼륨(`/root/.cache` — docling·EasyOCR 모델 캐시), postgres 호스트 포트 `127.0.0.1:5433`, 전 서비스 `restart: unless-stopped`, api 환경변수 `ADMIN_API_KEY`

- [ ] **Step 1: requirements.txt 교체**

`api/requirements.txt` 전체를 다음으로 교체:

```
fastapi
uvicorn[standard]
psycopg[binary]
httpx
redis
celery[redis]>=5.4
python-multipart
pandas>=2.2
openpyxl>=3.1
docling>=2.15,<3
```

- [ ] **Step 2: docker-compose.yml 수정**

api 서비스: `restart: unless-stopped` 추가, environment에 `- ADMIN_API_KEY=${ADMIN_API_KEY:-devkey}` 추가, volumes에 `- model-cache:/root/.cache` 추가.
postgres 서비스: `restart: unless-stopped`와 `ports: ["127.0.0.1:5433:5432"]` 추가.
qdrant·redis 서비스: `restart: unless-stopped` 추가.
volumes 블록에 `model-cache:` 추가.

- [ ] **Step 3: .env.example에 추가**

```
POSTGRES_PASSWORD=changeme
ADMIN_API_KEY=changeme
```

- [ ] **Step 4: 빌드 및 확인** (docling+torch 설치로 최초 빌드 수 분 소요 — 정상)

Run: `docker compose up -d --build && sleep 10 && curl -s -w " [%{http_code}]\n" http://localhost:8000/health`
Expected: `{"postgres":"ok","qdrant":"ok","redis":"ok"} [200]`

Run: `docker compose exec api python -c "import celery, pandas, docling; print('deps ok')"`
Expected: `deps ok`

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -c "SELECT 1;"`
Expected: 에러 없이 `1` 반환 (호스트 5433 포트 접근은 Task 2의 pytest가 검증)

- [ ] **Step 5: Commit**

```bash
git add api/requirements.txt docker-compose.yml .env.example
git commit -m "feat: 인제스트 의존성(celery/pandas/docling) 및 compose 보강 (restart 정책, 모델 캐시, dev DB 포트)"
```

---

### Task 2: DB 헬퍼 (ingest_tasks CRUD)

**Files:**
- Create: `api/app/db.py`
- Test: `api/tests/test_db.py`
- Modify: `db/init/001_schema.sql` (상태 주석 1줄)

**Interfaces:**
- Produces: `connect() -> psycopg.Connection`, `create_task(task_id: str, source_id: str) -> None`, `get_task(task_id: str) -> dict | None` (dict_row, 전 컬럼), `set_status(task_id: str, status: str, error: str | None = None) -> None`, `delete_task(task_id: str) -> None`. 이후 태스크(tasks.py, ingest_api.py)가 이 시그니처를 그대로 사용

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_db.py`:

```python
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db


def test_task_roundtrip():
    task_id, source_id = str(uuid.uuid4()), str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        t = db.get_task(task_id)
        assert t["status"] == "queued"
        assert t["source_id"] == source_id
        db.set_status(task_id, "failed", error="boom")
        t = db.get_task(task_id)
        assert t["status"] == "failed"
        assert t["error"] == "boom"
    finally:
        db.delete_task(task_id)
    assert db.get_task(task_id) is None
```

(`.env`에서 `POSTGRES_PASSWORD`를 devpass 외 값으로 설정했다면 `DATABASE_URL` 환경변수를 맞춰 export 후 실행)

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --no-project python -m pytest tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError` (app.db 없음)

- [ ] **Step 3: 구현**

`api/app/db.py`:

```python
"""ingest_tasks 테이블 접근 헬퍼."""
import os

import psycopg
from psycopg.rows import dict_row


def connect() -> psycopg.Connection:
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)


def create_task(task_id: str, source_id: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO ingest_tasks (task_id, source_id, status) VALUES (%s, %s, 'queued')",
            (task_id, source_id),
        )


def get_task(task_id: str) -> dict | None:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM ingest_tasks WHERE task_id = %s", (task_id,)
        ).fetchone()


def set_status(task_id: str, status: str, error: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status = %s, error = %s WHERE task_id = %s",
            (status, error, task_id),
        )


def delete_task(task_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM ingest_tasks WHERE task_id = %s", (task_id,))
```

- [ ] **Step 4: 스키마 주석 갱신** (`parsed` 상태 추가 — 주석만이라 기존 DB 볼륨 영향 없음)

`db/init/001_schema.sql`에서:

```sql
    status VARCHAR(30) NOT NULL,       -- queued/parsing/classifying/staged/
                                       -- approved/rejected/failed
```

를 다음으로 교체:

```sql
    status VARCHAR(30) NOT NULL,       -- queued/parsing/parsed/classifying/
                                       -- staged/approved/rejected/failed
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --no-project python -m pytest tests/test_db.py -v`
Expected: 1 passed

- [ ] **Step 6: Commit**

```bash
git add api/app/db.py api/tests/test_db.py db/init/001_schema.sql
git commit -m "feat: ingest_tasks DB 헬퍼 및 parsed 상태 추가"
```

---

### Task 3: 포맷 분기 + MD/XLSX 파싱

**Files:**
- Create: `api/pipeline/__init__.py` (빈 파일)
- Create: `api/pipeline/parse.py`
- Test: `api/tests/test_parse.py`

**Interfaces:**
- Produces: `run_pipeline(source_dir: Path) -> None` — `source_dir/original.*`를 찾아 확장자로 분기, `source_dir/parsed/`에 산출물 생성. `.pdf`는 `pipeline.parse_pdf.parse_pdf(src, out)`를 지연 임포트로 호출(Task 4가 구현). 미지원 확장자는 `ValueError`. `parse_md(src: Path, out: Path) -> None`, `parse_xlsx(src: Path, out: Path) -> None`

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_parse.py`:

```python
import json

import pandas as pd
import pytest

from pipeline.parse import parse_md, parse_xlsx, run_pipeline


def test_parse_md_splits_by_heading(tmp_path):
    src = tmp_path / "original.md"
    src.write_text("# 제목\n\n서론.\n\n## 배경\n\n본문.", encoding="utf-8")
    out = tmp_path / "parsed"
    out.mkdir()
    parse_md(src, out)
    assert (out / "document.md").read_text(encoding="utf-8").startswith("# 제목")
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    assert [c["type"] for c in chunks] == ["text", "text"]
    assert chunks[0]["id"] == "c001"
    assert "배경" in chunks[1]["text"]


def test_parse_xlsx_sheets_to_tables(tmp_path):
    src = tmp_path / "original.xlsx"
    pd.DataFrame({"사업명": ["A사업"], "예산": [100]}).to_excel(
        src, index=False, sheet_name="사업목록"
    )
    out = tmp_path / "parsed"
    out.mkdir()
    parse_xlsx(src, out)
    data = json.loads((out / "tables" / "table_001.json").read_text(encoding="utf-8"))
    assert data["table_title"] == "사업목록"
    assert data["columns"] == ["사업명", "예산"]
    assert data["rows"] == [["A사업", 100]]
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    assert chunks == [{"id": "c001", "type": "table", "page": None, "ref": "tables/table_001.json"}]


def test_run_pipeline_dispatches_md(tmp_path):
    (tmp_path / "original.md").write_text("# 하나", encoding="utf-8")
    run_pipeline(tmp_path)
    assert (tmp_path / "parsed" / "chunks.json").exists()


def test_run_pipeline_rejects_unknown_ext(tmp_path):
    (tmp_path / "original.hwp").write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        run_pipeline(tmp_path)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with pandas --with openpyxl --no-project python -m pytest tests/test_parse.py -v`
Expected: FAIL — `ModuleNotFoundError` (pipeline.parse 없음)

- [ ] **Step 3: 구현**

`api/pipeline/__init__.py`: 빈 파일.

`api/pipeline/parse.py`:

```python
"""Stage 0 포맷 분기 + MD/XLSX 파싱. PDF는 parse_pdf 모듈(도클링)로 위임."""
import json
from pathlib import Path


def run_pipeline(source_dir: Path) -> None:
    original = next(source_dir.glob("original.*"))
    out = source_dir / "parsed"
    out.mkdir(exist_ok=True)
    ext = original.suffix.lower()
    if ext == ".pdf":
        from pipeline.parse_pdf import parse_pdf  # docling 임포트는 무거워서 지연

        parse_pdf(original, out)
    elif ext == ".md":
        parse_md(original, out)
    elif ext == ".xlsx":
        parse_xlsx(original, out)
    else:
        raise ValueError(f"unsupported format: {ext}")


def _write_chunks(out: Path, chunks: list[dict]) -> None:
    (out / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_md(src: Path, out: Path) -> None:
    text = src.read_text(encoding="utf-8")
    (out / "document.md").write_text(text, encoding="utf-8")
    sections, cur = [], []
    for line in text.splitlines():
        if line.startswith("#") and cur:
            sections.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    chunks = [
        {"id": f"c{i:03d}", "type": "text", "page": None, "text": s}
        for i, s in enumerate((s for s in sections if s), 1)
    ]
    _write_chunks(out, chunks)


def parse_xlsx(src: Path, out: Path) -> None:
    import pandas as pd

    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)
    chunks = []
    for i, (sheet, df) in enumerate(pd.read_excel(src, sheet_name=None).items(), 1):
        ref = f"tables/table_{i:03d}.json"
        payload = {
            "table_title": str(sheet),
            "columns": [str(c) for c in df.columns],
            "rows": df.astype(object).where(df.notna(), None).values.tolist(),
        }
        (out / ref).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        chunks.append({"id": f"c{i:03d}", "type": "table", "page": None, "ref": ref})
    _write_chunks(out, chunks)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with pandas --with openpyxl --no-project python -m pytest tests/test_parse.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add api/pipeline api/tests/test_parse.py
git commit -m "feat: 포맷 분기(run_pipeline) 및 MD/XLSX 파싱"
```

---

### Task 4: PDF 파싱 (Docling)

**Files:**
- Create: `api/pipeline/parse_pdf.py`

**Interfaces:**
- Consumes: Task 3의 chunks.json 규약과 `run_pipeline`의 지연 임포트 경로 `pipeline.parse_pdf.parse_pdf`
- Produces: `parse_pdf(src: Path, out: Path) -> None` — document.md/document.json/chunks.json/tables/figures 생성

- [ ] **Step 1: 구현** (docling은 호스트에 설치하지 않으므로 TDD 대신 컨테이너 검증 — Step 2)

`api/pipeline/parse_pdf.py`:

```python
"""Docling 기반 PDF 파싱 (스펙 4.1 Stage 1: TableFormer ACCURATE + EasyOCR ko/en)."""
import json
from pathlib import Path


def parse_pdf(src: Path, out: Path) -> None:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        EasyOcrOptions,
        PdfPipelineOptions,
        TableFormerMode,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, TableItem, TextItem

    opts = PdfPipelineOptions()
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    opts.do_ocr = True
    opts.ocr_options = EasyOcrOptions(lang=["ko", "en"])
    opts.generate_picture_images = True
    opts.images_scale = 2.0
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    doc = converter.convert(str(src)).document

    (out / "document.md").write_text(doc.export_to_markdown(), encoding="utf-8")
    (out / "document.json").write_text(
        json.dumps(doc.export_to_dict(), ensure_ascii=False), encoding="utf-8"
    )
    (out / "tables").mkdir(exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)

    chunks: list[dict] = []
    n_table = n_fig = 0
    for item, _level in doc.iterate_items():
        page = item.prov[0].page_no if getattr(item, "prov", None) else None
        cid = f"c{len(chunks) + 1:03d}"
        if isinstance(item, TableItem):
            n_table += 1
            ref = f"tables/table_{n_table:03d}.json"
            df = item.export_to_dataframe(doc=doc)
            payload = {
                "table_title": "",
                "columns": [str(c) for c in df.columns],
                "rows": df.astype(object).where(df.notna(), None).values.tolist(),
            }
            (out / ref).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            chunks.append({"id": cid, "type": "table", "page": page, "ref": ref})
        elif isinstance(item, PictureItem):
            img = item.get_image(doc)
            if img is None:
                continue
            n_fig += 1
            ref = f"figures/fig_{n_fig:03d}.png"
            img.save(out / ref)
            chunks.append({"id": cid, "type": "picture", "page": page, "ref": ref})
        elif isinstance(item, TextItem) and item.text.strip():
            chunks.append({"id": cid, "type": "text", "page": page, "text": item.text})
    (out / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )
```

참고: docling 마이너 버전에 따라 `export_to_dataframe(doc=doc)`가 인자를 받지 않을 수 있다. `TypeError` 발생 시 `item.export_to_dataframe()`로 바꿔서 재시도하고, 실제 사용한 형태를 보고서에 기록할 것.

- [ ] **Step 2: 컨테이너에서 샘플 PDF로 검증** (최초 실행 시 모델 다운로드로 수 분 소요 — model-cache 볼륨에 저장되어 이후 재사용)

Run:
```bash
curl -sL -o /tmp/sample.pdf https://pdfobject.com/pdf/sample.pdf
docker compose up -d --build api
docker compose cp /tmp/sample.pdf api:/tmp/sample.pdf
docker compose exec api python -c "
from pathlib import Path
from pipeline.parse_pdf import parse_pdf
out = Path('/tmp/out'); out.mkdir(exist_ok=True)
parse_pdf(Path('/tmp/sample.pdf'), out)
print(sorted(p.name for p in out.iterdir()))
import json; print('chunks:', len(json.loads((out/'chunks.json').read_text())))
"
```
Expected: `['chunks.json', 'document.json', 'document.md', 'figures', 'tables']` 출력 및 chunks 1개 이상

- [ ] **Step 3: 호스트 유닛테스트 회귀 확인** (docling 미설치 환경에서 parse.py가 깨지지 않는지)

Run: `cd api && uv run --with pytest --with pandas --with openpyxl --no-project python -m pytest tests/test_parse.py -v`
Expected: 4 passed

- [ ] **Step 4: Commit**

```bash
git add api/pipeline/parse_pdf.py
git commit -m "feat: Docling 기반 PDF 파싱 (TableFormer ACCURATE, EasyOCR ko/en)"
```

---

### Task 5: Celery 워커

**Files:**
- Create: `api/tasks.py`
- Test: `api/tests/test_tasks.py`
- Modify: `docker-compose.yml` (ingest-worker 서비스 추가)

**Interfaces:**
- Consumes: Task 2의 `db.get_task/set_status`, Task 3의 `run_pipeline`
- Produces: Celery 앱 `tasks.celery`(브로커 `REDIS_URL`), 태스크 `run_ingest(task_id: str)`(name="ingest.run") — 상태를 parsing→parsed|failed로 전이. `SOURCES_PATH` 환경변수(기본 `/data/sources`)는 호출 시점에 읽음. Task 6이 `run_ingest.delay(task_id)`로 enqueue

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_tasks.py`:

```python
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db
from tasks import run_ingest


def test_run_ingest_md_reaches_parsed(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    src_dir = tmp_path / source_id
    src_dir.mkdir()
    (src_dir / "original.md").write_text("# 제목\n본문", encoding="utf-8")
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    task_id = str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        run_ingest(task_id)
        assert db.get_task(task_id)["status"] == "parsed"
        assert (src_dir / "parsed" / "chunks.json").exists()
    finally:
        db.delete_task(task_id)


def test_run_ingest_failure_records_error(tmp_path, monkeypatch):
    source_id = str(uuid.uuid4())
    (tmp_path / source_id).mkdir()
    (tmp_path / source_id / "original.hwp").write_bytes(b"x")
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    task_id = str(uuid.uuid4())
    db.create_task(task_id, source_id)
    try:
        try:
            run_ingest(task_id)
        except ValueError:
            pass
        t = db.get_task(task_id)
        assert t["status"] == "failed"
        assert "unsupported" in t["error"]
    finally:
        db.delete_task(task_id)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --no-project python -m pytest tests/test_tasks.py -v`
Expected: FAIL — `ModuleNotFoundError` (tasks 없음)

- [ ] **Step 3: 구현**

`api/tasks.py`:

```python
"""Celery 앱과 인제스트 태스크. 워커 실행: celery -A tasks worker"""
import os
from pathlib import Path

from celery import Celery

from app import db

celery = Celery("nst_wiki", broker=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))


@celery.task(name="ingest.run")
def run_ingest(task_id: str) -> None:
    task = db.get_task(task_id)
    db.set_status(task_id, "parsing")
    try:
        from pipeline.parse import run_pipeline

        source_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / task["source_id"]
        run_pipeline(source_dir)
        db.set_status(task_id, "parsed")
    except Exception as e:
        db.set_status(task_id, "failed", error=str(e))
        raise
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --no-project python -m pytest tests/test_tasks.py -v`
Expected: 2 passed

- [ ] **Step 5: compose에 ingest-worker 추가**

`docker-compose.yml`의 `services:`에 추가 (api 서비스 다음):

```yaml
  ingest-worker:
    build: ./api
    command: celery -A tasks worker --concurrency=2 --loglevel=info
    restart: unless-stopped
    environment:
      - DATABASE_URL=postgresql://wiki:${POSTGRES_PASSWORD:-devpass}@postgres:5432/llm_wiki
      - REDIS_URL=redis://redis:6379/0
      - WIKI_REPO_PATH=/data/wiki
    volumes:
      - wiki-data:/data/wiki
      - sources-data:/data/sources
      - model-cache:/root/.cache
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_started
```

- [ ] **Step 6: 워커 기동 확인**

Run: `docker compose up -d --build ingest-worker && sleep 8 && docker compose logs ingest-worker | grep -E "ready|ingest.run" | head -3`
Expected: `celery@... ready.` 로그와 등록된 태스크 `ingest.run` 확인 (`docker compose logs ingest-worker | grep -A3 tasks`)

- [ ] **Step 7: Commit**

```bash
git add api/tasks.py api/tests/test_tasks.py docker-compose.yml
git commit -m "feat: Celery 인제스트 워커 (상태 전이 queued→parsing→parsed|failed)"
```

---

### Task 6: 인제스트 API + 관통 검증 + README

**Files:**
- Create: `api/app/ingest_api.py`
- Modify: `api/app/main.py`
- Test: `api/tests/test_ingest_api.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 2의 `db.create_task/get_task`, Task 5의 `tasks.run_ingest.delay`
- Produces: `POST /api/v1/ingest` (X-Admin-Key 필수, multipart: file + title 필수, source_type/publisher/publish_date/tags 선택) → `{"task_id", "status": "queued"}`. `GET /api/v1/ingest/{task_id}/status` → `{status, affected_pages, affected_tables, contradictions, error}`. 원본은 `/data/sources/{source_id}/original.{ext}` + `metadata.json`(스펙 3절 스키마, file_hash는 sha256)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_ingest_api.py`:

```python
import os

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ingest_requires_admin_key():
    r = client.post("/api/v1/ingest", headers={"X-Admin-Key": "wrong"})
    assert r.status_code == 401


def test_ingest_rejects_unsupported_ext(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCES_PATH", str(tmp_path))
    r = client.post(
        "/api/v1/ingest",
        headers={"X-Admin-Key": "testkey"},
        files={"file": ("doc.hwp", b"x")},
        data={"title": "테스트"},
    )
    assert r.status_code == 400


def test_status_unknown_task_404():
    r = client.get("/api/v1/ingest/00000000-0000-0000-0000-000000000000/status")
    assert r.status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: FAIL — 라우터 미구현이므로 401 기대 테스트가 404 응답으로 assert 실패

- [ ] **Step 3: 구현**

`api/app/ingest_api.py`:

```python
"""소스 업로드·상태 조회 엔드포인트 (스펙 6.1절)."""
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile

from app import db

router = APIRouter(prefix="/api/v1")
ALLOWED_EXTS = {".pdf", ".md", ".xlsx"}


def require_admin(x_admin_key: str = Header(default="")) -> None:
    if x_admin_key != os.environ["ADMIN_API_KEY"]:
        raise HTTPException(status_code=401, detail="invalid admin key")


@router.post("/ingest", dependencies=[Depends(require_admin)])
async def ingest(
    file: UploadFile = File(...),
    title: str = Form(...),
    source_type: str = Form("policy_doc"),
    publisher: str = Form(""),
    publish_date: str = Form(""),
    tags: str = Form(""),
):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"unsupported format: {ext}")
    source_id, task_id = str(uuid.uuid4()), str(uuid.uuid4())
    src_dir = Path(os.environ.get("SOURCES_PATH", "/data/sources")) / source_id
    src_dir.mkdir(parents=True)
    data = await file.read()
    (src_dir / f"original{ext}").write_bytes(data)
    meta = {
        "source_id": source_id,
        "source_type": source_type,
        "title": title,
        "publisher": publisher,
        "publish_date": publish_date,
        "ingest_date": datetime.now(timezone.utc).isoformat(),
        "tags": [t.strip() for t in tags.split(",") if t.strip()],
        "file_hash": "sha256:" + hashlib.sha256(data).hexdigest(),
    }
    (src_dir / "metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    db.create_task(task_id, source_id)
    from tasks import run_ingest  # celery 브로커 연결은 enqueue 시점에만 필요

    run_ingest.delay(task_id)
    return {"task_id": task_id, "status": "queued"}


@router.get("/ingest/{task_id}/status")
def ingest_status(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return {
        "status": task["status"],
        "affected_pages": task["affected_pages"],
        "affected_tables": task["affected_tables"],
        "contradictions": task["contradictions"],
        "error": task["error"],
    }
```

`api/app/main.py` 끝에 추가:

```python
from app.ingest_api import router as ingest_router

app.include_router(ingest_router)
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: 3 passed

(참고: unsupported-ext 테스트는 celery enqueue 전에 400으로 반환되므로 브로커 연결이 필요 없다. DB는 400 경로에서도 접근하지 않는다.)

- [ ] **Step 5: E2E 관통 검증** (실제 PDF 업로드 → 파싱 완료까지)

Run:
```bash
docker compose up -d --build api ingest-worker
curl -sL -o /tmp/sample.pdf https://pdfobject.com/pdf/sample.pdf
TASK=$(curl -s -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Admin-Key: devkey" \
  -F "file=@/tmp/sample.pdf" -F "title=샘플 정책문서" | python3 -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
echo "task=$TASK"
sleep 90 && curl -s http://localhost:8000/api/v1/ingest/$TASK/status
```
Expected: 최종 status가 `"parsed"` (최초 실행은 모델 다운로드로 더 걸릴 수 있음 — parsing이면 추가 대기 후 재조회)

Run: `SRC=$(docker compose exec postgres psql -U wiki -d llm_wiki -tAc "SELECT source_id FROM ingest_tasks WHERE task_id='$TASK'") && docker compose exec api ls /data/sources/$SRC/parsed`
Expected: `chunks.json document.json document.md figures tables`

- [ ] **Step 6: README 갱신**

`README.md`의 `## 테스트` 섹션 앞에 추가:

````markdown
## 문서 인제스트

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -F "file=@문서.pdf" -F "title=문서 제목" \
  -F "publisher=발행기관" -F "tags=NEXT,반도체"
# → {"task_id": "...", "status": "queued"}

curl http://localhost:8000/api/v1/ingest/<task_id>/status
# → {"status": "parsed", ...}
```

지원 포맷: PDF(기본), MD(사전 변환 문서), XLSX(사업·기관 목록). 파싱 산출물은
`sources-data` 볼륨의 `{source_id}/parsed/`에 생성된다. LLM 분류·위키 반영은 Phase 2b.
````

- [ ] **Step 7: 전체 테스트 회귀 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --no-project python -m pytest tests -v`
Expected: 12 passed (init_wiki 2 + db 1 + parse 4 + tasks 2 + ingest_api 3)

- [ ] **Step 8: Commit**

```bash
git add api/app/ingest_api.py api/app/main.py api/tests/test_ingest_api.py README.md
git commit -m "feat: 인제스트 업로드·상태 API 및 E2E 관통 (업로드→파싱→parsed)"
```
