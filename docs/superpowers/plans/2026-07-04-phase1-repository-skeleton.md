# Phase 1: 저장소 골격 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** docker-compose로 PostgreSQL(고정 스키마 + staging)·Qdrant·Redis·최소 API(/health)를 기동하고, 위키 데이터 저장소(/data/wiki)를 초기화하여 Phase 2 이후의 검증 기반을 만든다.

**Architecture:** 스펙(docs/superpowers/specs/2026-07-04-kistep-llm-wiki-design.md) 5.2·8.1절의 저장 레이어를 그대로 구현한다. DB 스키마는 사람이 관리하는 SQL 파일(db/init/)로 정의하고 postgres 컨테이너의 docker-entrypoint-initdb.d로 적용한다. 위키는 named volume(wiki-data) 안의 git 저장소이며 Python 스크립트로 초기화한다. API는 세 저장소 연결을 검증하는 /health 하나만 제공한다.

**Tech Stack:** Docker Compose, PostgreSQL 16, Qdrant, Redis 7, Python 3.12, FastAPI, psycopg 3, pytest

## Global Constraints

- 컨테이너 이미지(스펙 8.1 그대로): `postgres:16`, `qdrant/qdrant:latest`, `redis:7-alpine`, API 베이스는 `python:3.12-slim`
- DB 이름 `llm_wiki`, DB 사용자 `wiki`, 비밀번호는 `.env`의 `POSTGRES_PASSWORD` (기본값 `devpass`)
- API 포트 `8000` (NPM이 프록시할 포트 — 스펙 8.2)
- 위키 저장소 경로 `/data/wiki`, git 브랜치명 `main`
- DB 스키마 변경은 사람이 작성하는 `db/init/*.sql`로만 한다 (스펙 원칙 5: LLM DDL 금지)
- 커밋 메시지와 문서는 한국어
- **Phase 1 범위 밖 (만들지 말 것):** frontend, Celery ingest-worker, `GEMINI_API_KEY`/`ADMIN_API_KEY` 환경변수, 마이그레이션 프레임워크(alembic), 인증. 각각 필요해지는 Phase에서 추가한다

---

### Task 1: docker-compose 골격 (postgres / qdrant / redis)

**Files:**
- Create: `docker-compose.yml`
- Create: `.env.example`
- Create: `.gitignore`
- Create: `db/init/.gitkeep`

**Interfaces:**
- Produces: compose 서비스명 `postgres`(5432, healthcheck 포함), `qdrant`(6333), `redis`(6379). named volumes `pg-data`, `qdrant-data`, `wiki-data`, `sources-data`. `./db/init`가 postgres의 `/docker-entrypoint-initdb.d`에 마운트됨 (Task 2가 SQL을 넣음)

- [ ] **Step 1: 파일 작성**

`docker-compose.yml`:

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      - POSTGRES_DB=llm_wiki
      - POSTGRES_USER=wiki
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD:-devpass}
    volumes:
      - pg-data:/var/lib/postgresql/data
      - ./db/init:/docker-entrypoint-initdb.d:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wiki -d llm_wiki"]
      interval: 5s
      timeout: 3s
      retries: 10

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant-data:/qdrant/storage

  redis:
    image: redis:7-alpine

volumes:
  pg-data:
  qdrant-data:
  wiki-data:
  sources-data:
```

`.env.example`:

```
POSTGRES_PASSWORD=changeme
```

`.gitignore`:

```
.env
__pycache__/
*.pyc
.venv/
```

`db/init/.gitkeep`: 빈 파일 (`touch db/init/.gitkeep`)

- [ ] **Step 2: 기동 및 확인**

Run: `docker compose up -d && sleep 10 && docker compose ps`
Expected: postgres가 `Up (healthy)`, qdrant·redis가 `Up`

Run: `docker compose exec redis redis-cli ping`
Expected: `PONG`

Run: `docker compose logs qdrant | grep -i listening | head -2`
Expected: HTTP 6333 listen 로그 출력

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml .env.example .gitignore db/init/.gitkeep
git commit -m "feat: docker-compose 골격 (postgres/qdrant/redis)"
```

---

### Task 2: PostgreSQL 고정 스키마 (public 8테이블 + staging 스키마)

**Files:**
- Create: `db/init/001_schema.sql`

**Interfaces:**
- Consumes: Task 1의 `./db/init` 마운트
- Produces: `public` 스키마에 `technologies`, `projects`, `tech_project_mapping`, `budget_history`, `policy_events`, `ministries`, `staging_tables`, `ingest_tasks` (8개). `staging` 스키마에 앞 6개 핵심 테이블과 동일 구조 미러 (FK 없음, 시퀀스는 public과 공유하므로 id 충돌 없음)

- [ ] **Step 1: 스키마 SQL 작성**

`db/init/001_schema.sql` (DDL은 스펙 5.2절 그대로):

```sql
-- NEXT 기술 테이블
CREATE TABLE technologies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    field VARCHAR(50) NOT NULL,        -- 10개 NEXT 분야
    sub_field VARCHAR(100),
    lead_ministry VARCHAR(50),
    trl_level INTEGER,
    description TEXT,
    wiki_page_path VARCHAR(200),       -- Wiki 페이지 참조
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    source_id VARCHAR(100)             -- 출처 소스
);

-- R&D 사업 테이블
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    project_code VARCHAR(50) UNIQUE,
    name VARCHAR(200) NOT NULL,
    lead_ministry VARCHAR(50),
    budget_total BIGINT,               -- 총사업비 (백만원)
    budget_annual BIGINT,              -- 연간예산 (백만원)
    start_year INTEGER,
    end_year INTEGER,
    status VARCHAR(20),                -- 진행중/완료/예비타당성
    source_id VARCHAR(100)
);

-- 기술-사업 매핑 테이블
CREATE TABLE tech_project_mapping (
    technology_id INTEGER REFERENCES technologies(id),
    project_id INTEGER REFERENCES projects(id),
    relevance_score FLOAT,
    mapping_source VARCHAR(20),        -- manual/llm_inferred
    PRIMARY KEY (technology_id, project_id)
);

-- 예산 이력 테이블
CREATE TABLE budget_history (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    fiscal_year INTEGER NOT NULL,
    amount BIGINT NOT NULL,
    source_id VARCHAR(100)
);

-- 정책 이벤트 로그
CREATE TABLE policy_events (
    id SERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_type VARCHAR(50),            -- reform/announcement/law/summit
    title VARCHAR(200),
    description TEXT,
    affected_fields TEXT[],
    wiki_page_path VARCHAR(200),
    source_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 부처 테이블
CREATE TABLE ministries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(20),
    wiki_page_path VARCHAR(200)
);

-- 매핑 불가 표 보존 (스키마 검토 대기)
CREATE TABLE staging_tables (
    id SERIAL PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    table_title TEXT,                  -- 표 제목/캡션
    raw_data JSONB NOT NULL,           -- 정규화된 원본 표 (행 배열)
    suggested_mapping JSONB,           -- LLM의 매핑 제안 (참고용)
    mapping_confidence FLOAT,
    status VARCHAR(20) DEFAULT 'needs_review',  -- needs_review/mapped/discarded
    created_at TIMESTAMP DEFAULT NOW()
);

-- 인제스트 태스크 상태
CREATE TABLE ingest_tasks (
    task_id VARCHAR(100) PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL,       -- queued/parsing/classifying/staged/
                                       -- approved/rejected/failed
    branch_name VARCHAR(200),
    affected_pages JSONB,              -- 갱신된 페이지 + 갱신 제안 목록
    affected_tables JSONB,
    contradictions JSONB,
    error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    reviewed_at TIMESTAMP
);

-- 승인 대기 데이터용 staging 스키마 (스펙 5.2)
-- ponytail: LIKE INCLUDING ALL — PK·기본값 복사, FK는 복사 안 됨(의도),
-- 시퀀스는 public과 공유되어 승인 upsert 시 id 충돌 없음
CREATE SCHEMA staging;
CREATE TABLE staging.technologies (LIKE public.technologies INCLUDING ALL);
CREATE TABLE staging.projects (LIKE public.projects INCLUDING ALL);
CREATE TABLE staging.tech_project_mapping (LIKE public.tech_project_mapping INCLUDING ALL);
CREATE TABLE staging.budget_history (LIKE public.budget_history INCLUDING ALL);
CREATE TABLE staging.policy_events (LIKE public.policy_events INCLUDING ALL);
CREATE TABLE staging.ministries (LIKE public.ministries INCLUDING ALL);
```

- [ ] **Step 2: DB 재생성으로 스키마 적용**

initdb 스크립트는 빈 데이터 볼륨에서만 실행되므로 볼륨을 지우고 다시 올린다 (이 시점에 보존할 데이터 없음):

Run: `docker compose down -v && docker compose up -d && sleep 10`
Expected: 에러 없이 기동

- [ ] **Step 3: 테이블 확인**

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -c "\dt public.*"`
Expected: 8개 테이블 (technologies, projects, tech_project_mapping, budget_history, policy_events, ministries, staging_tables, ingest_tasks)

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -c "\dt staging.*"`
Expected: 6개 테이블

- [ ] **Step 4: 삽입 스모크 테스트 (공유 시퀀스 확인)**

Run:
```bash
docker compose exec postgres psql -U wiki -d llm_wiki -c \
  "INSERT INTO technologies (name, field) VALUES ('HBM','반도체') RETURNING id;"
docker compose exec postgres psql -U wiki -d llm_wiki -c \
  "INSERT INTO staging.technologies (name, field) VALUES ('양자컴퓨팅','양자') RETURNING id;"
```
Expected: 첫 번째 id=1, 두 번째 id=2 (public과 staging이 시퀀스 공유 → id 충돌 없음)

Run:
```bash
docker compose exec postgres psql -U wiki -d llm_wiki -c \
  "DELETE FROM technologies; DELETE FROM staging.technologies;"
```
Expected: `DELETE 1` 두 번

- [ ] **Step 5: Commit**

```bash
git add db/init/001_schema.sql
git commit -m "feat: PostgreSQL 고정 스키마 (public 8테이블 + staging 미러)"
```

---

### Task 3: 위키 저장소 초기화 스크립트

**Files:**
- Create: `api/scripts/__init__.py` (빈 파일)
- Create: `api/scripts/init_wiki.py`
- Create: `api/tests/__init__.py` (빈 파일)
- Test: `api/tests/test_init_wiki.py`

**Interfaces:**
- Produces: `init_wiki(root: Path) -> bool` — 위키 디렉토리 구조(스펙 5.1)·`index.md`·`schema.md`·`contradictions/log.md`를 만들고 git 저장소(main 브랜치)로 초기 커밋. 이미 초기화된 경우(`.git` 존재) 아무것도 하지 않고 `False` 반환(멱등). CLI 실행 시 `WIKI_REPO_PATH` 환경변수(기본 `/data/wiki`) 사용. Phase 2 인제스트 파이프라인이 이 구조와 schema.md 규칙을 전제로 동작한다

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_init_wiki.py`:

```python
import subprocess

from scripts.init_wiki import DIRS, init_wiki


def test_init_creates_structure_and_git(tmp_path):
    assert init_wiki(tmp_path) is True
    for d in DIRS:
        assert (tmp_path / d).is_dir()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "schema.md").exists()
    assert (tmp_path / "contradictions" / "log.md").exists()
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    )
    assert "위키 저장소 초기화" in log.stdout
    branch = subprocess.run(
        ["git", "-C", str(tmp_path), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    )
    assert branch.stdout.strip() == "main"


def test_init_is_idempotent(tmp_path):
    init_wiki(tmp_path)
    assert init_wiki(tmp_path) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run:
```bash
cd api && uv run --with pytest --no-project python -m pytest tests -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.init_wiki'` (또는 import 에러)

- [ ] **Step 3: 구현**

`api/scripts/init_wiki.py`:

````python
"""위키 데이터 저장소를 초기화한다. 멱등: 이미 git 저장소면 아무것도 하지 않는다."""
import os
import subprocess
import sys
from pathlib import Path

DIRS = ["tech", "entity", "events", "synthesis", "summaries", "contradictions"]

INDEX_MD = """\
# NST Wiki 색인

국가전략기술 정책 지식 위키. 페이지가 생성·갱신되면 이 색인도 함께 갱신한다.

## tech (기술 개념)

(아직 페이지 없음)

## entity (정책 엔티티)

(아직 페이지 없음)

## events (정책변화 이력)

(아직 페이지 없음)

## synthesis (종합·비교 분석)

(아직 페이지 없음)
"""

SCHEMA_MD = """\
# 위키 운영 규칙 (LLM 컴파일 규칙서)

이 문서는 인제스트 파이프라인의 LLM이 위키를 읽고 쓸 때 따라야 하는 규칙이다.

## 디렉토리와 페이지 유형

| 디렉토리 | type | 내용 | 파일명 규칙 |
|---|---|---|---|
| tech/ | tech_concept | 기술별 개념 페이지 | 영문 케밥케이스 (hbm-semiconductor.md) |
| entity/ | policy_entity | 부처·기관 페이지 | 한글 기관명 (과기정통부.md) |
| events/ | policy_event | 정책변화 이력 | YYYY-MM-슬러그.md |
| synthesis/ | synthesis | 종합·비교 분석 | 영문 케밥케이스 |
| summaries/ | source_summary | 소스별 요약 | {source_id}.md |
| contradictions/ | - | 모순 기록 (log.md에만 기록) | log.md 고정 |

## 프론트매터 표준

모든 페이지는 다음 프론트매터로 시작한다:

```yaml
---
title: HBM 반도체
type: tech_concept
next_field: 반도체
related_pages:
  - tech/quantum-computing
  - entity/과기정통부
data_refs:
  - "technologies?name=HBM"
sources:
  - source_id: "abc123"
    last_updated: "2026-07-04"
unresolved_contradictions: []
---
```

## 링크 규칙

- 위키 내부 링크: `[[tech/hbm-semiconductor]]` 형식 (확장자 생략)
- DB 참조: `[[data:technologies?field=반도체]]` — 정형 수치는 본문에 하드코딩하지 않고 data 참조로 연결한다
- 깨진 링크를 만들지 않는다. 대상 페이지가 없으면 먼저 생성하거나 링크를 생략한다

## 갱신 규칙

- 페이지는 통째로 재작성하지 않고 기존 내용에 새 정보를 병합한다
- 모든 사실 서술은 sources 프론트매터의 source_id로 근거를 유지한다
- 소스 1건당 자동 갱신 페이지 상한: 15개 (초과분은 갱신 제안으로만)
- 기존 서술과 신규 정보가 충돌하면: 본문을 임의로 교체하지 말고
  contradictions/log.md에 기록하고 해당 페이지 프론트매터의
  unresolved_contradictions에 모순 ID를 추가한다
"""

LOG_MD = """\
# 모순·충돌 기록

| ID | 발견일 | 대상 페이지 | 기존 주장 (source) | 신규 주장 (source) | 상태 |
|---|---|---|---|---|---|
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def init_wiki(root: Path) -> bool:
    if (root / ".git").exists():
        return False
    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (root / d).mkdir(exist_ok=True)
        (root / d / ".gitkeep").touch()
    (root / "index.md").write_text(INDEX_MD, encoding="utf-8")
    (root / "schema.md").write_text(SCHEMA_MD, encoding="utf-8")
    (root / "contradictions" / "log.md").write_text(LOG_MD, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "wiki-bot@nst-wiki.local")
    _git(root, "config", "user.name", "nst-wiki bot")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: 위키 저장소 초기화")
    return True


if __name__ == "__main__":
    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    created = init_wiki(root)
    print(f"initialized: {root}" if created else f"already initialized: {root}")
    sys.exit(0)
````

`api/scripts/__init__.py`와 `api/tests/__init__.py`는 빈 파일로 생성.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add api/scripts api/tests
git commit -m "feat: 위키 저장소 초기화 스크립트 (구조·schema.md·git 초기 커밋)"
```

---

### Task 4: 최소 API (/health) + compose 통합 + 위키 볼륨 초기화

**Files:**
- Create: `api/app/__init__.py` (빈 파일)
- Create: `api/app/main.py`
- Create: `api/requirements.txt`
- Create: `api/Dockerfile`
- Modify: `docker-compose.yml` (api 서비스 추가)

**Interfaces:**
- Consumes: Task 1의 compose 서비스명(postgres/qdrant/redis)과 volumes, Task 3의 `scripts/init_wiki.py`
- Produces: `GET /health` → `{"postgres": "ok", "qdrant": "ok", "redis": "ok"}` (하나라도 실패 시 HTTP 503). api 컨테이너 환경변수 `DATABASE_URL`, `QDRANT_URL`, `REDIS_URL`, `WIKI_REPO_PATH`. wiki-data 볼륨이 초기화된 git 저장소가 됨

- [ ] **Step 1: API 구현**

`api/requirements.txt`:

```
fastapi
uvicorn[standard]
psycopg[binary]
httpx
redis
```

`api/app/main.py`:

```python
import os

import httpx
import psycopg
import redis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(title="nst-wiki API")


def _check(fn) -> str:
    try:
        fn()
        return "ok"
    except Exception as e:  # ponytail: 개인용 헬스체크라 원인 문자열 그대로 노출
        return f"error: {e}"


@app.get("/health")
def health():
    checks = {
        "postgres": _check(lambda: psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3).close()),
        "qdrant": _check(lambda: httpx.get(os.environ["QDRANT_URL"] + "/readyz", timeout=3).raise_for_status()),
        "redis": _check(lambda: redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3).ping()),
    }
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(checks, status_code=200 if ok else 503)
```

`api/Dockerfile`:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY scripts ./scripts
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: compose에 api 서비스 추가**

`docker-compose.yml`의 `services:` 아래 맨 앞에 추가:

```yaml
  api:
    build: ./api
    ports: ["8000:8000"]              # NPM이 프록시할 포트
    environment:
      - DATABASE_URL=postgresql://wiki:${POSTGRES_PASSWORD:-devpass}@postgres:5432/llm_wiki
      - QDRANT_URL=http://qdrant:6333
      - REDIS_URL=redis://redis:6379/0
      - WIKI_REPO_PATH=/data/wiki
    volumes:
      - wiki-data:/data/wiki
      - sources-data:/data/sources
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_started
      redis:
        condition: service_started
```

- [ ] **Step 3: 기동 및 /health 확인**

Run: `docker compose up -d --build && sleep 10`
Expected: 에러 없이 빌드·기동

Run: `curl -s -w "\n%{http_code}\n" http://localhost:8000/health`
Expected:
```
{"postgres":"ok","qdrant":"ok","redis":"ok"}
200
```

- [ ] **Step 4: 실패 경로 확인 (503)**

Run: `docker compose stop redis && sleep 2 && curl -s -w "\n%{http_code}\n" http://localhost:8000/health && docker compose start redis`
Expected: `"redis":"error: ..."` 포함, HTTP `503`

- [ ] **Step 5: 위키 볼륨 초기화**

Run: `docker compose exec api python scripts/init_wiki.py`
Expected: `initialized: /data/wiki`

Run: `docker compose exec api git -C /data/wiki log --oneline`
Expected: `chore: 위키 저장소 초기화` 커밋 1건

Run: `docker compose exec api python scripts/init_wiki.py`
Expected: `already initialized: /data/wiki` (멱등 확인)

- [ ] **Step 6: Commit**

```bash
git add api/app api/requirements.txt api/Dockerfile docker-compose.yml
git commit -m "feat: 최소 API(/health) 및 compose 통합, 위키 볼륨 초기화"
```

---

### Task 5: 클린 관통 검증 + README

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: Task 1~4 전부
- Produces: 빈 상태에서 문서만 보고 전체 스택을 올릴 수 있는 절차

- [ ] **Step 1: README 작성**

`README.md`:

````markdown
# nst-wiki

국가전략기술 정책 지식을 컴파일하는 LLM Wiki 시스템.
설계서: `docs/superpowers/specs/2026-07-04-kistep-llm-wiki-design.md`

## 요구사항

- Docker + Docker Compose
- (개발 시) uv, git

## 기동

```bash
cp .env.example .env          # POSTGRES_PASSWORD 수정
docker compose up -d --build
docker compose exec api python scripts/init_wiki.py   # 최초 1회
curl http://localhost:8000/health
```

## 구성

| 서비스 | 포트 | 역할 |
|---|---|---|
| api | 8000 | FastAPI (NPM이 nst-wiki.mem.photos로 프록시) |
| postgres | - | 정형 데이터 (public) + 승인 대기 (staging) |
| qdrant | - | 벡터 검색 (Phase 4부터 사용) |
| redis | - | Celery 큐 (Phase 2부터 사용) |

볼륨: `wiki-data`(위키 git 저장소), `sources-data`(원본 문서), `pg-data`, `qdrant-data`

DB 스키마는 `db/init/*.sql`로만 변경한다 (LLM DDL 금지 — 설계서 원칙 5).

## 테스트

```bash
cd api && uv run --with pytest --no-project python -m pytest tests -v
```
````

- [ ] **Step 2: 클린 상태 관통 검증**

Run:
```bash
docker compose down -v
docker compose up -d --build && sleep 15
curl -s -w "\n%{http_code}\n" http://localhost:8000/health
docker compose exec postgres psql -U wiki -d llm_wiki -c "\dt public.*" | tail -3
docker compose exec api python scripts/init_wiki.py
docker compose exec api git -C /data/wiki log --oneline
```
Expected: health 200 / public 테이블 목록 출력 / `initialized: /data/wiki` / 초기 커밋 1건

- [ ] **Step 3: 로컬 테스트 재확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests -v`
Expected: 2 passed

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: README (기동 절차·구성) 추가, Phase 1 완료"
```
