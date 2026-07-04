# Phase 4: 벡터 임베딩 + 하이브리드 질의 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 승인된 위키 페이지를 BGE-M3(밀집+희소)로 Qdrant에 색인하고, `POST /api/v1/query`가 자연어 질문을 서사(벡터 검색+합성)·데이터(Text-to-SQL)·혼합 경로로 처리해 인용 포함 답변을 반환한다.

**Architecture:** 스펙 5.3·6.2절(Stage 8 + 질의 처리)을 구현한다. 임베딩은 승인 시점에 celery 태스크로 수행(BGE-M3는 워커에서만 로드, model-cache 볼륨에 캐시). Qdrant `wiki_pages` 컬렉션에 dense(1024, cosine)+sparse 하이브리드 색인, 질의 시 RRF 융합. Text-to-SQL은 읽기 전용 DB 롤(004)로 SELECT만 실행. 의도 분류·SQL 생성·답변 합성은 기존 `llm.generate` 경유.

**Tech Stack:** 기존 스택 + qdrant-client, FlagEmbedding(BGE-M3 — torch는 docling이 이미 설치)

## Global Constraints

- 임베딩 모델은 `BAAI/bge-m3` 고정, dense+sparse 하이브리드 (스펙 5.3). 모델 로드는 지연(lazy) — API 컨테이너에서 임포트만으로 로드되면 안 됨
- **승인된 지식만 색인** (스펙 5.3): 색인은 approve 이후 celery 태스크에서만
- Text-to-SQL은 읽기 전용 롤 `wiki_ro`로만 실행, 단일 SELECT 문만 허용(코드 검증 + DB 권한 이중 방어), `statement_timeout` 5초, LIMIT 없으면 100 부여 (스펙 6.2)
- 스펙 5.3의 `db_records` 컬렉션은 만들지 않는다 — 데이터 질의는 Text-to-SQL 담당 (의도된 단순화, 필요 확인 시 추가)
- 유닛테스트 실 API·실 모델 호출 금지 (mock) — 실 임베딩·실 Gemini는 검증 단계·E2E에서만
- 커밋 메시지·문서·프롬프트 한국어
- **범위 밖:** 프론트엔드 질의 UI(Phase 5), Lint(Phase 6), db_records
- 테스트 명령(전체): `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --with qdrant-client --no-project python -m pytest tests -v`

---

### Task 1: 004 읽기 전용 롤 + 의존성·compose 보강

**Files:**
- Create: `db/init/004_readonly_role.sql`
- Modify: `api/requirements.txt`, `docker-compose.yml`

**Interfaces:**
- Produces: DB 롤 `wiki_ro`(SELECT만, public 6개 핵심 테이블), env `READONLY_DATABASE_URL`(api·worker), worker에 `QDRANT_URL` 추가, deps `qdrant-client`·`FlagEmbedding`

- [ ] **Step 1: 마이그레이션 작성**

`db/init/004_readonly_role.sql`:

```sql
-- Text-to-SQL 실행 전용 읽기 롤 (스펙 6.2: 읽기 전용 계정 + public 화이트리스트).
-- 개인 자가호스팅 전제로 비밀번호는 dev 기본값 — 외부 노출 시 .env로 교체할 것.
DO $$ BEGIN
    CREATE ROLE wiki_ro LOGIN PASSWORD 'ro_devpass';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
GRANT CONNECT ON DATABASE llm_wiki TO wiki_ro;
GRANT USAGE ON SCHEMA public TO wiki_ro;
GRANT SELECT ON technologies, projects, tech_project_mapping, budget_history,
                policy_events, ministries TO wiki_ro;
```

- [ ] **Step 2: 기존 볼륨 적용 + 확인**

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/004_readonly_role.sql`
Expected: 에러 없음

Run: `docker compose exec postgres psql -U wiki_ro -d llm_wiki -c "SELECT count(*) FROM technologies;" && docker compose exec postgres psql -U wiki_ro -d llm_wiki -c "INSERT INTO ministries (name) VALUES ('x');" 2>&1 | grep -o "permission denied[^\"]*" | head -1`
Expected: count 반환 성공 / `permission denied for table ministries`

Run: `docker compose exec postgres psql -U wiki_ro -d llm_wiki -c "SELECT count(*) FROM staging.technologies;" 2>&1 | grep -o "permission denied[^\"]*" | head -1`
Expected: `permission denied for schema staging`

- [ ] **Step 3: 의존성·compose**

`api/requirements.txt` 끝에 `qdrant-client`와 `FlagEmbedding>=1.2` 추가.
`docker-compose.yml`: api·ingest-worker 양쪽 environment에 `- READONLY_DATABASE_URL=postgresql://wiki_ro:ro_devpass@postgres:5432/llm_wiki` 추가, ingest-worker에 `- QDRANT_URL=http://qdrant:6333` 추가, api 서비스 volumes에 `- model-cache:/root/.cache` 복원 (2b에서 제거했으나 질의 경로가 질문 1건 인코딩에 BGE-M3를 필요로 함 — 커밋 메시지에 사유 기록).

Run: `docker compose up -d --build && sleep 10 && docker compose exec api python -c "import qdrant_client; print('ok')"` (timeout 600000ms)
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add db/init/004_readonly_role.sql api/requirements.txt docker-compose.yml
git commit -m "feat: Text-to-SQL 읽기 전용 롤(004) 및 벡터 의존성"
```

---

### Task 2: 임베딩 모듈 (embeddings.py)

**Files:**
- Create: `api/embeddings.py`
- Test: `api/tests/test_embeddings.py`

**Interfaces:**
- Produces: `chunk_page(text: str, max_chars: int = 1200) -> list[str]` (## 헤딩 우선 분할, 초과 시 문단 분할), `encode(texts: list[str]) -> list[dict]` (각 `{"dense": [float...], "sparse": {int: float}}` — BGE-M3 lazy 싱글턴), `ensure_collection(client)`, `index_page(client, path: str, text: str)` (기존 포인트 삭제 후 청크 업서트 — 멱등), `qdrant()` (QDRANT_URL 클라이언트). 컬렉션명 상수 `COLLECTION = "wiki_pages"`, dense 1024 cosine + sparse

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_embeddings.py`:

```python
import embeddings


def test_chunk_page_splits_by_heading():
    text = "# 제목\n\n서론입니다.\n\n## 배경\n\n" + "가" * 100 + "\n\n## 현황\n\n나나나"
    chunks = embeddings.chunk_page(text)
    assert len(chunks) == 3
    assert chunks[1].startswith("## 배경")


def test_chunk_page_splits_long_sections():
    text = "## 긴절\n\n" + ("문단입니다. " * 40 + "\n\n") * 5  # 한 절이 max_chars 초과
    chunks = embeddings.chunk_page(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)  # 문단 경계라 약간 여유


def test_index_page_deletes_then_upserts(monkeypatch):
    calls = []
    monkeypatch.setattr(embeddings, "encode",
                        lambda texts: [{"dense": [0.0] * 1024, "sparse": {1: 0.5}} for _ in texts])

    class FakeClient:
        def delete(self, collection_name, points_selector):
            calls.append(("delete", collection_name))

        def upsert(self, collection_name, points):
            calls.append(("upsert", collection_name, len(points)))

    embeddings.index_page(FakeClient(), "tech/a.md", "# A\n\n본문\n\n## 절\n\n내용")
    assert calls[0][0] == "delete"
    assert calls[1][0] == "upsert"
    assert calls[1][2] >= 1
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with qdrant-client --no-project python -m pytest tests/test_embeddings.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/embeddings.py`:

```python
"""BGE-M3 임베딩 + Qdrant 색인. 모델 로드는 지연 — 워커에서만 실체화된다."""
import os
import uuid

COLLECTION = "wiki_pages"
_model = None


def _bge():
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel

        _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    return _model


def encode(texts: list[str]) -> list[dict]:
    out = _bge().encode(texts, return_dense=True, return_sparse=True)
    return [
        {
            "dense": out["dense_vecs"][i].tolist(),
            "sparse": {int(k): float(v) for k, v in out["lexical_weights"][i].items()},
        }
        for i in range(len(texts))
    ]


def chunk_page(text: str, max_chars: int = 1200) -> list[str]:
    sections, cur = [], []
    for line in text.splitlines():
        if line.startswith("## ") and cur:
            sections.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    chunks = []
    for sec in sections:
        if len(sec) <= max_chars:
            if sec:
                chunks.append(sec)
            continue
        buf = ""
        for para in sec.split("\n\n"):
            if buf and len(buf) + len(para) > max_chars:
                chunks.append(buf.strip())
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
    return chunks


def qdrant():
    from qdrant_client import QdrantClient

    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))


def ensure_collection(client) -> None:
    from qdrant_client import models

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            COLLECTION,
            vectors_config={"dense": models.VectorParams(size=1024, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )


def index_page(client, path: str, text: str) -> int:
    from qdrant_client import models

    client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="path", match=models.MatchValue(value=path))
        ])),
    )
    chunks = chunk_page(text)
    if not chunks:
        return 0
    vecs = encode(chunks)
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}#{i}")),
            vector={
                "dense": v["dense"],
                "sparse": models.SparseVector(
                    indices=list(v["sparse"].keys()), values=list(v["sparse"].values())
                ),
            },
            payload={"path": path, "chunk": i, "text": chunk},
        )
        for i, (chunk, v) in enumerate(zip(chunks, vecs))
    ]
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)
```

참고: FlagEmbedding/qdrant-client 버전에 따라 API가 다를 수 있다 — Task 3의 컨테이너 실검증에서 오류 시 설치 버전 시그니처에 맞추고 보고서에 기록.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with qdrant-client --no-project python -m pytest tests/test_embeddings.py -v`
Expected: 3 passed

- [ ] **Step 5: Dockerfile COPY 갱신 후 커밋**

`api/Dockerfile`의 `COPY llm.py llm_config.json wiki_ops.py ./` → `COPY llm.py llm_config.json wiki_ops.py embeddings.py ./`

```bash
git add api/embeddings.py api/tests/test_embeddings.py api/Dockerfile
git commit -m "feat: BGE-M3 임베딩·Qdrant 색인 모듈 (하이브리드, 페이지 단위 멱등)"
```

---

### Task 3: 승인 시 색인 (celery 태스크 + approve 훅 + reindex)

**Files:**
- Modify: `api/tasks.py`, `api/app/ingest_api.py`
- Test: `api/tests/test_tasks.py` (추가)

**Interfaces:**
- Consumes: Task 2 전부, `wiki_ops.list_pages/read_page`
- Produces: celery 태스크 `embed_pages(paths: list[str])` (name="embed.pages") — main의 해당 페이지들을 읽어 색인, 없는 페이지는 스킵. `reindex_all()` (name="embed.reindex") — 전체 페이지 재색인. approve 성공 시 `affected_pages`의 update/create 경로를 enqueue. `POST /api/v1/reindex` [admin] → reindex 태스크 enqueue

- [ ] **Step 1: 실패하는 테스트 작성** (`api/tests/test_tasks.py`에 추가)

```python
def test_embed_pages_reads_main_and_indexes(tmp_path, monkeypatch):
    import tasks as tasks_mod
    from scripts.init_wiki import init_wiki
    import wiki_ops
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "sE", {"tech/e.md": "# E\n\n본문"}, "m")
    wiki_ops.approve_branch(tmp_path, "sE", "approve: m")
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    indexed = []
    import embeddings
    monkeypatch.setattr(embeddings, "qdrant", lambda: object())
    monkeypatch.setattr(embeddings, "ensure_collection", lambda c: None)
    monkeypatch.setattr(embeddings, "index_page", lambda c, p, t: indexed.append(p) or 1)
    tasks_mod.embed_pages(["tech/e.md", "tech/없는페이지.md"])
    assert indexed == ["tech/e.md"]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with qdrant-client --with google-genai --no-project python -m pytest tests/test_tasks.py -v`
Expected: 신규 1개 FAIL — `AttributeError` (embed_pages 없음)

- [ ] **Step 3: 구현**

`api/tasks.py`에 추가:

```python
@celery.task(name="embed.pages", time_limit=1800)
def embed_pages(paths: list[str]) -> int:
    import embeddings
    import wiki_ops

    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    client = embeddings.qdrant()
    embeddings.ensure_collection(client)
    n = 0
    for p in paths:
        text = wiki_ops.read_page(root, p)
        if text is not None:
            n += embeddings.index_page(client, p, text)
    return n


@celery.task(name="embed.reindex", time_limit=3600)
def reindex_all() -> int:
    import wiki_ops

    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    return embed_pages(wiki_ops.list_pages(root))
```

`api/app/ingest_api.py`의 approve 성공 반환 직전(try 블록 안, wiki 병합 뒤)에 추가:

```python
        pages = [p["path"] for p in (task["affected_pages"] or [])
                 if p.get("action") in ("create", "update")]
        if pages:
            from tasks import embed_pages

            embed_pages.delay(pages)
```

그리고 reindex 엔드포인트 추가:

```python
@router.post("/reindex", dependencies=[Depends(require_admin)])
def reindex():
    from tasks import reindex_all

    reindex_all.delay()
    return {"status": "queued"}
```

- [ ] **Step 4: 테스트 통과 + 컨테이너 실 임베딩 검증** (최초 실행은 BGE-M3 다운로드 ~2.3GB — timeout 600000ms, 실패 시 재실행)

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with qdrant-client --with google-genai --no-project python -m pytest tests/test_tasks.py -v`
Expected: 3 passed

Run:
```bash
docker compose up -d --build api ingest-worker && sleep 8
source .env
curl -s -X POST http://localhost:8000/api/v1/reindex -H "X-Admin-Key: $ADMIN_API_KEY"
sleep 240 && docker compose logs ingest-worker --tail 5 | grep -E "embed.reindex.*succeeded" || docker compose logs ingest-worker --tail 20
curl -s http://localhost:8000/health >/dev/null && docker compose exec api python -c "
from qdrant_client import QdrantClient
import os
c = QdrantClient(url='http://qdrant:6333')
print('points:', c.count('wiki_pages').count)"
```
Expected: reindex succeeded 로그, points 1 이상 (승인된 페이지 존재)

- [ ] **Step 5: Commit**

```bash
git add api/tasks.py api/app/ingest_api.py api/tests/test_tasks.py
git commit -m "feat: 승인 시 위키 페이지 임베딩 색인 (celery) 및 전체 재색인"
```

---

### Task 4: 하이브리드 검색 (search.py)

**Files:**
- Create: `api/search.py`
- Test: `api/tests/test_search.py`

**Interfaces:**
- Consumes: `embeddings.encode/qdrant/COLLECTION`
- Produces: `search_wiki(question: str, limit: int = 5) -> list[dict]` — `[{"path", "text", "score"}]`. dense+sparse prefetch 후 RRF 융합

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_search.py`:

```python
import search


def test_search_wiki_fuses_and_formats(monkeypatch):
    import embeddings
    monkeypatch.setattr(embeddings, "encode",
                        lambda texts: [{"dense": [0.0] * 1024, "sparse": {3: 1.0}}])

    class P:
        def __init__(self, path, text, score):
            self.payload = {"path": path, "text": text}
            self.score = score

    class FakeClient:
        def query_points(self, collection_name, prefetch, query, limit, with_payload):
            assert collection_name == "wiki_pages"
            assert len(prefetch) == 2
            class R: points = [P("tech/a.md", "본문", 0.9)]
            return R()

    monkeypatch.setattr(embeddings, "qdrant", lambda: FakeClient())
    out = search.search_wiki("HBM이 뭐야?")
    assert out == [{"path": "tech/a.md", "text": "본문", "score": 0.9}]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with qdrant-client --no-project python -m pytest tests/test_search.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/search.py`:

```python
"""하이브리드(밀집+희소 RRF) 위키 검색."""
import embeddings


def search_wiki(question: str, limit: int = 5) -> list[dict]:
    from qdrant_client import models

    vec = embeddings.encode([question])[0]
    client = embeddings.qdrant()
    res = client.query_points(
        collection_name=embeddings.COLLECTION,
        prefetch=[
            models.Prefetch(query=vec["dense"], using="dense", limit=20),
            models.Prefetch(
                query=models.SparseVector(
                    indices=list(vec["sparse"].keys()), values=list(vec["sparse"].values())
                ),
                using="sparse", limit=20,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return [
        {"path": p.payload["path"], "text": p.payload["text"], "score": p.score}
        for p in res.points
    ]
```

- [ ] **Step 4: 테스트 통과 + 컨테이너 실검증**

Run: `cd api && uv run --with pytest --with qdrant-client --no-project python -m pytest tests/test_search.py -v`
Expected: 1 passed

Run: `docker compose up -d --build ingest-worker && docker compose exec ingest-worker python -c "
from search import search_wiki
for r in search_wiki('양자컴퓨팅'):
    print(round(r['score'], 3), r['path'])"`
Expected: 색인된 페이지가 점수순으로 출력 (Task 3에서 색인됨)

- [ ] **Step 5: Dockerfile COPY 갱신 후 커밋**

`api/Dockerfile`의 embeddings.py COPY 줄에 `search.py` 추가 (`COPY llm.py llm_config.json wiki_ops.py embeddings.py search.py ./`).

```bash
git add api/search.py api/tests/test_search.py api/Dockerfile
git commit -m "feat: 하이브리드(RRF) 위키 검색"
```

---

### Task 5: Text-to-SQL (text2sql.py)

**Files:**
- Create: `api/text2sql.py`
- Test: `api/tests/test_text2sql.py`

**Interfaces:**
- Consumes: `llm.generate`, env `READONLY_DATABASE_URL`
- Produces: `run_data_query(question: str) -> dict` — `{"sql": str, "rows": list[dict], "error": str | None}`. LLM이 SQL 생성 → `validate_sql`(단일 SELECT만, 세미콜론·주석 거부) → 읽기 전용 연결로 실행(statement_timeout 5s, LIMIT 없으면 100 부여). `validate_sql(sql: str) -> str` (정규화된 SQL 반환, 위반 시 ValueError)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_text2sql.py`:

```python
import os

import pytest

os.environ.setdefault("READONLY_DATABASE_URL",
                      "postgresql://wiki_ro:ro_devpass@127.0.0.1:5433/llm_wiki")

import text2sql


def test_validate_sql_accepts_select():
    assert text2sql.validate_sql("SELECT name FROM technologies").startswith("SELECT")


def test_validate_sql_appends_limit():
    out = text2sql.validate_sql("SELECT name FROM technologies")
    assert out.rstrip().endswith("LIMIT 100")
    kept = text2sql.validate_sql("SELECT name FROM technologies LIMIT 5")
    assert "LIMIT 5" in kept and "LIMIT 100" not in kept


@pytest.mark.parametrize("bad", [
    "DELETE FROM technologies",
    "SELECT 1; DROP TABLE technologies",
    "UPDATE technologies SET name='x'",
    "SELECT 1 -- 주석",
    "WITH x AS (SELECT 1) INSERT INTO ministries (name) SELECT 'a'",
])
def test_validate_sql_rejects(bad):
    with pytest.raises(ValueError):
        text2sql.validate_sql(bad)


def test_run_data_query_executes(monkeypatch):
    monkeypatch.setattr(text2sql.llm, "generate",
                        lambda *a, **k: {"sql": "SELECT 1 AS one"})
    out = text2sql.run_data_query("아무거나")
    assert out["error"] is None
    assert out["rows"] == [{"one": 1}]


def test_run_data_query_blocks_write(monkeypatch):
    monkeypatch.setattr(text2sql.llm, "generate",
                        lambda *a, **k: {"sql": "DELETE FROM technologies"})
    out = text2sql.run_data_query("전부 지워줘")
    assert out["rows"] == []
    assert out["error"] is not None
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with google-genai --no-project python -m pytest tests/test_text2sql.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: 구현**

`api/text2sql.py`:

```python
"""자연어 → SQL. 이중 방어: 코드 검증(단일 SELECT) + wiki_ro 읽기 전용 롤."""
import os
import re

import psycopg
from psycopg.rows import dict_row

import llm

SCHEMA_DESC = """
- technologies(id, name, field, sub_field, lead_ministry, trl_level, description): NEXT 기술
- projects(id, project_code, name, lead_ministry, budget_total, budget_annual, start_year, end_year, status): R&D 사업 (예산 단위: 백만원)
- tech_project_mapping(technology_id, project_id, relevance_score): 기술-사업 매핑
- budget_history(id, project_id, fiscal_year, amount): 연도별 예산
- policy_events(id, event_date, event_type, title, description, affected_fields): 정책 이벤트
- ministries(id, name, abbreviation): 부처
"""

SQL_SCHEMA = {
    "type": "object",
    "properties": {"sql": {"type": "string"}},
    "required": ["sql"],
}

PROMPT = """다음 PostgreSQL 스키마에서 질문에 답하는 SELECT 문 하나를 작성하라.
{schema}
규칙: SELECT 단일 문장만. 세미콜론·주석 금지. 한국어 값은 그대로 비교. 집계 질문이면 집계 함수 사용.

질문: {question}"""


def validate_sql(sql: str) -> str:
    s = sql.strip()
    if ";" in s or "--" in s or "/*" in s:
        raise ValueError("금지된 토큰 (세미콜론/주석)")
    if not re.match(r"(?is)^\s*SELECT\b", s):
        raise ValueError("SELECT 문만 허용")
    if re.search(r"(?i)\b(insert|update|delete|drop|alter|create|grant|truncate|copy)\b", s):
        raise ValueError("쓰기 키워드 금지")
    if not re.search(r"(?i)\bLIMIT\s+\d+", s):
        s = f"{s} LIMIT 100"
    return s


def run_data_query(question: str) -> dict:
    out = llm.generate("text2sql", PROMPT.format(schema=SCHEMA_DESC, question=question),
                       schema=SQL_SCHEMA)
    raw = out["sql"]
    try:
        sql = validate_sql(raw)
        with psycopg.connect(os.environ["READONLY_DATABASE_URL"], row_factory=dict_row,
                             options="-c statement_timeout=5000") as conn:
            rows = conn.execute(sql).fetchall()
        return {"sql": sql, "rows": rows, "error": None}
    except Exception as e:  # ponytail: LLM 생성 SQL은 실패가 일상 — 오류를 답변 합성에 넘긴다
        return {"sql": raw, "rows": [], "error": str(e)}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --with google-genai --no-project python -m pytest tests/test_text2sql.py -v`
Expected: 9 passed (parametrize 5건 포함)

- [ ] **Step 5: Dockerfile COPY에 text2sql.py 추가 후 커밋**

```bash
git add api/text2sql.py api/tests/test_text2sql.py api/Dockerfile
git commit -m "feat: Text-to-SQL (단일 SELECT 검증 + 읽기 전용 롤 실행)"
```

---

### Task 6: 질의 API + E2E + README

**Files:**
- Create: `api/app/query_api.py`
- Modify: `api/app/main.py`, `README.md`
- Test: `api/tests/test_query_api.py`

**Interfaces:**
- Consumes: `search.search_wiki`, `text2sql.run_data_query`, `llm.generate`
- Produces: `POST /api/v1/query` body `{"question": str, "mode": "auto|narrative|data|hybrid"}` (기본 auto) → `{"answer", "mode", "citations": [{"path"}...], "sql": str|null, "sql_rows": [...], "sql_error": str|null}`. auto면 LLM 의도 분류(purpose "route_query"). narrative: 검색 상위 5청크로 합성(인용은 사용된 path). data: run_data_query 결과를 짧은 자연어로 요약. hybrid: 둘 다 수행 후 합성

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_query_api.py`:

```python
import os

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _patch(monkeypatch, route="narrative", chunks=None, data=None):
    from app import query_api
    monkeypatch.setattr(query_api.llm, "generate", _fake_llm(route))
    monkeypatch.setattr(query_api.search, "search_wiki",
                        lambda q, limit=5: chunks if chunks is not None else [])
    monkeypatch.setattr(query_api.text2sql, "run_data_query",
                        lambda q: data or {"sql": None, "rows": [], "error": None})


def _fake_llm(route):
    def fake(purpose, contents, schema=None):
        if purpose == "route_query":
            return {"mode": route}
        if purpose == "synthesize":
            return "합성된 답변"
        raise AssertionError(purpose)
    return fake


def test_query_narrative(monkeypatch):
    _patch(monkeypatch, route="narrative",
           chunks=[{"path": "tech/a.md", "text": "본문", "score": 0.9}])
    r = client.post("/api/v1/query", json={"question": "HBM이 뭐야?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "narrative"
    assert body["answer"] == "합성된 답변"
    assert body["citations"] == [{"path": "tech/a.md"}]
    assert body["sql"] is None


def test_query_data_mode_explicit(monkeypatch):
    _patch(monkeypatch, data={"sql": "SELECT 1", "rows": [{"c": 1}], "error": None})
    r = client.post("/api/v1/query", json={"question": "예산 합계?", "mode": "data"})
    body = r.json()
    assert body["mode"] == "data"
    assert body["sql"] == "SELECT 1"
    assert body["sql_rows"] == [{"c": 1}]


def test_query_rate_limited(monkeypatch):
    _patch(monkeypatch, route="narrative", chunks=[])
    from app import query_api
    monkeypatch.setattr(query_api, "RATE_LIMIT", 2)
    query_api._hits.clear()
    for _ in range(2):
        assert client.post("/api/v1/query",
                           json={"question": "q", "mode": "narrative"}).status_code == 200
    assert client.post("/api/v1/query",
                       json={"question": "q", "mode": "narrative"}).status_code == 429
    query_api._hits.clear()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with qdrant-client --with google-genai --no-project python -m pytest tests/test_query_api.py -v`
Expected: FAIL — `ImportError` (query_api 없음)

- [ ] **Step 3: 구현**

`api/app/query_api.py`:

```python
"""자연어 질의: 의도 분류 → 서사(벡터)/데이터(SQL)/혼합 → 합성 (스펙 6.2)."""
import json

from fastapi import APIRouter
from pydantic import BaseModel

import llm
import search
import text2sql

router = APIRouter(prefix="/api/v1")

# ponytail: 인메모리 rate limit — uvicorn 단일 프로세스 전제 (스펙 8.3), 다중 워커 도입 시 redis로
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

RATE_LIMIT = int(os.environ.get("QUERY_RATE_LIMIT", "10"))  # 분당 IP별
_hits: dict[str, deque] = defaultdict(deque)


def _check_rate(ip: str) -> None:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    q.append(now)


ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["narrative", "data", "hybrid"]}},
    "required": ["mode"],
}

ROUTE_PROMPT = """질문의 유형을 분류하라.
- narrative: 배경·이유·맥락·설명을 묻는 질문
- data: 목록·수치·집계·필터링을 묻는 질문
- hybrid: 둘 다 필요한 질문

질문: {question}"""

SYNTH_PROMPT = """다음 자료만 근거로 질문에 한국어로 답하라. 자료에 없는 내용은 모른다고 답하라.
서사 자료를 인용할 때는 문장 끝에 [경로] 형식으로 출처를 표기하라.

{context}

질문: {question}"""


class QueryBody(BaseModel):
    question: str
    mode: str = "auto"


@router.post("/query")
def query(body: QueryBody, request: Request):
    _check_rate(request.client.host if request.client else "unknown")
    mode = body.mode
    if mode == "auto":
        mode = llm.generate("route_query", ROUTE_PROMPT.format(question=body.question),
                            schema=ROUTE_SCHEMA)["mode"]
    chunks, data = [], {"sql": None, "rows": [], "error": None}
    if mode in ("narrative", "hybrid"):
        chunks = search.search_wiki(body.question)
    if mode in ("data", "hybrid"):
        data = text2sql.run_data_query(body.question)

    context = ""
    if chunks:
        context += "## 서사 자료 (위키)\n" + "\n\n".join(
            f"[{c['path']}]\n{c['text']}" for c in chunks
        )
    if data["sql"]:
        context += (f"\n\n## 데이터 자료 (SQL: {data['sql']})\n"
                    + (f"오류: {data['error']}" if data["error"]
                       else json.dumps(data["rows"], ensure_ascii=False, default=str)))
    if not context:
        context = "(자료 없음)"
    answer = llm.generate("synthesize",
                          SYNTH_PROMPT.format(context=context, question=body.question))
    return {
        "answer": answer,
        "mode": mode,
        "citations": [{"path": c["path"]} for c in chunks],
        "sql": data["sql"],
        "sql_rows": data["rows"],
        "sql_error": data["error"],
    }
```

`api/app/main.py`에 라우터 추가 (기존 include 옆):

```python
from app.query_api import router as query_router

app.include_router(query_router)
```

- [ ] **Step 4: 테스트 통과 + 전체 회귀**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with qdrant-client --with google-genai --no-project python -m pytest tests -v`
Expected: 67 passed (50 + tasks 1 + embeddings 3 + search 1 + text2sql 9 + query_api 3 — 실제 수를 보고서에 기록, 전부 passed)

- [ ] **Step 5: E2E (실 Gemini + 실 임베딩·검색)** — 질의 경로는 질문 1건을 api 컨테이너에서 인코딩한다 (model-cache 마운트는 Task 1에서 복원됨, 최초 호출은 모델 로드로 수십 초).

Run:
```bash
docker compose up -d --build api ingest-worker && sleep 8
curl -s -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "양자컴퓨팅에 대해 알려줘", "mode": "narrative"}' | python3 -m json.tool
```
Expected: answer에 한국어 답변 + citations에 위키 경로 (최초 호출은 모델 로드로 수십 초)

Run:
```bash
curl -s -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "기술 테이블에 몇 건이 있어?", "mode": "data"}' | python3 -m json.tool
```
Expected: sql에 SELECT count 계열, sql_rows에 결과

- [ ] **Step 6: README 갱신 + 커밋**

README의 승인 대시보드 문단 뒤에 추가:

````markdown
## 자연어 질의

```bash
curl -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "반도체 분야에 어떤 기술이 있어?"}'
# mode: auto(기본)/narrative/data/hybrid — 서사는 위키 벡터 검색, 데이터는 Text-to-SQL(읽기 전용)
```

승인된 페이지만 색인된다. 전체 재색인: `POST /api/v1/reindex` (admin key 필요).
````

```bash
git add api/app/query_api.py api/app/main.py api/tests/test_query_api.py docker-compose.yml README.md
git commit -m "feat: 하이브리드 자연어 질의 API (의도 분류·벡터 검색·Text-to-SQL·합성)"
```
