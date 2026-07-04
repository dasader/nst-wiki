# Phase 3: 승인 워크플로 + 최소 대시보드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** staged 상태의 인제스트를 사람이 검토(위키 diff·staging 데이터·모순)하고 승인(위키 squash 병합 + DB upsert) 또는 거부할 수 있게 하여 스펙의 신뢰 모델(Stage 6~7)을 완성한다.

**Architecture:** 스펙 4.1(Stage 6~7)·4.5·6.1절을 구현한다. `wiki_ops`에 승인(squash 병합+모순 해결 반영)·거부·diff 조회를 추가하고, `db`에 staging→public upsert(유니크 제약 기반 ON CONFLICT)를 추가한다. 승인 순서는 DB upsert → 위키 병합 → 상태 전이 (upsert는 멱등이라 위키 실패 후 재승인 안전). 대시보드는 Next.js(Phase 5) 대신 FastAPI가 서빙하는 단일 정적 HTML — 새 서비스 없이 승인 UI를 제공한다(의도된 스펙 단순화, Phase 5가 대체). 임베딩(Stage 8)은 Phase 4.

**Tech Stack:** 기존 스택 + 정적 HTML/vanilla JS (프레임워크 없음)

## Global Constraints

- 상태 전이: `staged → approved | rejected` (그 외 상태에서 approve/reject는 HTTP 409)
- approve/reject는 `X-Admin-Key` 필수 (기존 `require_admin` 재사용), review·목록 조회는 인증 없음 (스펙 8.3)
- 승인 단위는 소스 1건 전체 — DB upsert와 위키 병합이 한 요청에서 함께 수행 (스펙 4.1)
- **DB 마이그레이션 규칙 (이번에 확립):** 스키마 변경은 `db/init/NNN_*.sql` 넘버링 파일로만. 새 볼륨은 initdb가 순서대로 적용, 기존 볼륨은 `docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/NNN_*.sql`로 수동 적용. staging 스키마는 제약이 불필요하므로(중복도 검토 대상) public에만 적용하고 그 사유를 파일에 주석으로 남긴다
- upsert 키: `technologies(name)`, `ministries(name)` — 002에서 UNIQUE 추가. `projects`는 기존 `project_code` UNIQUE 사용(코드 없는 행은 단순 INSERT). `policy_events`는 자연키가 없어 항상 INSERT
- 커밋 메시지·문서 한국어. 유닛테스트 실 API 호출 금지 (E2E만 실 호출)
- **범위 밖:** 임베딩·벡터 검색(Phase 4), Next.js frontend(Phase 5), `/stats`(Phase 6), staging_tables의 스키마 검토 UI(수동 SQL로 충분 — v2)
- 테스트 명령(전체): `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --no-project python -m pytest tests -v`

---

### Task 1: 마이그레이션 002 (유니크 제약) + 적용 경로 확립

**Files:**
- Create: `db/init/002_unique_keys.sql`
- Modify: `README.md`, `PONYTAIL-DEBT.md`

**Interfaces:**
- Produces: `technologies_name_key`, `ministries_name_key` UNIQUE 제약 (Task 3의 ON CONFLICT가 의존). README에 마이그레이션 규칙 문서화

- [ ] **Step 1: 마이그레이션 파일 작성**

`db/init/002_unique_keys.sql`:

```sql
-- 승인 upsert(ON CONFLICT)용 유니크 제약.
-- staging 스키마에는 적용하지 않는다: 승인 전 데이터는 중복도 검토 대상이며,
-- 제약 위반으로 인제스트가 실패하는 것보다 사람이 승인 화면에서 거르는 것이 맞다.
ALTER TABLE technologies ADD CONSTRAINT technologies_name_key UNIQUE (name);
ALTER TABLE ministries ADD CONSTRAINT ministries_name_key UNIQUE (name);
```

- [ ] **Step 2: 기존 볼륨에 적용**

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/002_unique_keys.sql`
Expected: `ALTER TABLE` 두 번 (public 테이블이 비어 있어 제약 생성 즉시 성공)

Run: `docker compose exec postgres psql -U wiki -d llm_wiki -c "\d technologies" | grep -A2 Indexes`
Expected: `technologies_name_key` UNIQUE 표시

- [ ] **Step 3: README에 마이그레이션 규칙 추가**

`README.md`의 "DB 스키마는 `db/init/*.sql`로만 변경한다" 문장을 다음으로 교체:

```markdown
DB 스키마는 `db/init/NNN_*.sql` 넘버링 파일로만 변경한다 (LLM DDL 금지 — 설계서 원칙 5).
새 볼륨은 initdb가 순서대로 적용하고, 기존 볼륨에는 수동 적용한다:
`docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/NNN_*.sql`
```

- [ ] **Step 4: PONYTAIL-DEBT 원장 갱신** (staging 미러 항목의 트리거가 도래했으므로)

`PONYTAIL-DEBT.md`의 staging 미러 행에서 재방문 트리거 셀 내용을 다음으로 교체:
`✅ 2026-07-04 처리됨 — 002_unique_keys.sql로 적용 경로 확립, public 전용 사유는 파일 주석에 기록. 이후 DDL은 public/staging 동시 검토 후 결정`

- [ ] **Step 5: Commit**

```bash
git add db/init/002_unique_keys.sql README.md PONYTAIL-DEBT.md
git commit -m "feat: upsert용 유니크 제약(002) 및 마이그레이션 적용 경로 확립"
```

---

### Task 2: wiki_ops 확장 (diff·승인·거부)

**Files:**
- Modify: `api/wiki_ops.py`
- Test: `api/tests/test_wiki_ops.py` (추가)

**Interfaces:**
- Consumes: 기존 `_git`, `_lock`, `stage_changes`
- Produces: `diff_branch(root, source_id) -> str` (main...branch 패치 텍스트, 브랜치 없으면 빈 문자열), `approve_branch(root, source_id, message, resolutions: dict[str, str] | None = None) -> None` (flock, squash 병합 + 모순 해결 반영 + 커밋 + 브랜치 삭제), `reject_branch(root, source_id) -> None` (브랜치 강제 삭제, 없으면 무시). resolutions는 `{모순ID: "keep|replace|both"}` — log.md에서 해당 ID 행의 `미해결`을 `해결(값)`으로 치환

- [ ] **Step 1: 실패하는 테스트 작성** (`api/tests/test_wiki_ops.py`에 추가)

```python
def test_diff_approve_flow(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s20", {"tech/x.md": "# X\n본문"}, "ingest: 문서")
    diff = wiki_ops.diff_branch(tmp_path, "s20")
    assert "tech/x.md" in diff and "+# X" in diff
    wiki_ops.approve_branch(tmp_path, "s20", "approve: 문서")
    assert (tmp_path / "tech" / "x.md").read_text(encoding="utf-8").startswith("# X")
    assert "ingest/s20" not in _git_out(tmp_path, "branch", "--list", "ingest/*")
    log = _git_out(tmp_path, "log", "--oneline", "main")
    assert "approve: 문서" in log


def test_approve_applies_resolutions(tmp_path):
    init_wiki(tmp_path)
    log_row = "| s21-1 | 2026-07-04 | tech/a.md | 요약 | 기존 | 신규 | 미해결 |\n"
    log_content = (tmp_path / "contradictions" / "log.md").read_text(encoding="utf-8") + log_row
    wiki_ops.stage_changes(tmp_path, "s21",
                           {"tech/a.md": "# A", "contradictions/log.md": log_content}, "m")
    wiki_ops.approve_branch(tmp_path, "s21", "approve: m", resolutions={"s21-1": "replace"})
    merged_log = (tmp_path / "contradictions" / "log.md").read_text(encoding="utf-8")
    assert "| s21-1 |" in merged_log
    assert "해결(replace)" in merged_log
    assert "| 미해결 |" not in merged_log.split("s21-1")[1].split("\n")[0]


def test_reject_deletes_branch(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s22", {"tech/y.md": "# Y"}, "m")
    wiki_ops.reject_branch(tmp_path, "s22")
    assert "ingest/s22" not in _git_out(tmp_path, "branch", "--list", "ingest/*")
    assert not (tmp_path / "tech" / "y.md").exists()
    wiki_ops.reject_branch(tmp_path, "s22")  # 멱등: 없는 브랜치도 에러 없이
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests/test_wiki_ops.py -v`
Expected: 신규 3개 FAIL — `AttributeError` (diff_branch 없음)

- [ ] **Step 3: 구현** (`api/wiki_ops.py`에 추가)

```python
def _branch_exists(root: Path, branch: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def diff_branch(root: Path, source_id: str) -> str:
    branch = f"ingest/{source_id}"
    if not _branch_exists(root, branch):
        return ""
    return _git(root, "diff", f"main...{branch}")


def approve_branch(root: Path, source_id: str, message: str,
                   resolutions: dict[str, str] | None = None) -> None:
    branch = f"ingest/{source_id}"
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        _git(root, "clean", "-fd")
        _git(root, "merge", "--squash", branch)
        if resolutions:
            log_path = root / "contradictions" / "log.md"
            log = log_path.read_text(encoding="utf-8")
            for cid, res in resolutions.items():
                lines = log.splitlines(keepends=True)
                log = "".join(
                    ln.replace("| 미해결 |", f"| 해결({res}) |") if f"| {cid} |" in ln else ln
                    for ln in lines
                )
            log_path.write_text(log, encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", message)
        _git(root, "branch", "-D", branch)


def reject_branch(root: Path, source_id: str) -> None:
    branch = f"ingest/{source_id}"
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        _git(root, "clean", "-fd")
        if _branch_exists(root, branch):
            _git(root, "branch", "-D", branch)
```

참고: 기존 `_git`은 stdout을 반환하도록 이미 구현되어 있다(`text=True`). `diff_branch`가 그 반환값을 그대로 쓴다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --no-project python -m pytest tests/test_wiki_ops.py -v`
Expected: 9 passed (기존 6 + 신규 3)

- [ ] **Step 5: Commit**

```bash
git add api/wiki_ops.py api/tests/test_wiki_ops.py
git commit -m "feat: 위키 승인(squash 병합·모순 해결)·거부·diff 조회"
```

---

### Task 3: DB 확장 (staged 조회·upsert·삭제·목록)

**Files:**
- Modify: `api/app/db.py`
- Test: `api/tests/test_db.py` (추가)

**Interfaces:**
- Consumes: Task 1의 유니크 제약, Phase 2b의 staging 데이터 규약(각 staging 테이블에 source_id 컬럼)
- Produces:
  - `list_staged(source_id) -> dict` — `{"technologies": [행...], "projects": [...], "policy_events": [...], "ministries": [...], "needs_review": [staging_tables 행...]}` (빈 테이블은 빈 리스트)
  - `upsert_staged(source_id) -> dict` — staging→public upsert 후 staging 행 삭제, `{테이블명: 행수}` 반환. 한 트랜잭션
  - `discard_staged(source_id) -> None` — staging 행 삭제 + staging_tables 행 status='discarded'
  - `list_tasks(limit: int = 50) -> list[dict]` — 최근 인제스트 태스크 (created_at 역순)

- [ ] **Step 1: 실패하는 테스트 작성** (`api/tests/test_db.py`에 추가)

```python
def _stage_tech(source_id, name, field="반도체"):
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging.technologies (name, field, source_id) VALUES (%s, %s, %s)",
            (name, field, source_id),
        )


def test_upsert_staged_inserts_and_updates():
    source_id = str(uuid.uuid4())
    _stage_tech(source_id, "업서트기술", field="반도체")
    try:
        counts = db.upsert_staged(source_id)
        assert counts["technologies"] == 1
        assert db.list_staged(source_id)["technologies"] == []  # staging 비워짐
        # 같은 name으로 재승인 → 갱신 (중복 행 없음)
        source_id2 = str(uuid.uuid4())
        _stage_tech(source_id2, "업서트기술", field="이차전지")
        db.upsert_staged(source_id2)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT field FROM technologies WHERE name = %s", ("업서트기술",)
            ).fetchall()
        assert len(rows) == 1
        assert rows[0]["field"] == "이차전지"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM technologies WHERE name = %s", ("업서트기술",))
            conn.execute("DELETE FROM staging.technologies WHERE source_id IN (%s, %s)",
                         (source_id, source_id2))


def test_discard_staged_clears():
    source_id = str(uuid.uuid4())
    _stage_tech(source_id, "폐기기술")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging_tables (source_id, table_title, raw_data) VALUES (%s, %s, %s)",
            (source_id, "표", '{"columns": [], "rows": []}'),
        )
    try:
        db.discard_staged(source_id)
        assert db.list_staged(source_id)["technologies"] == []
        with db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM staging_tables WHERE source_id = %s", (source_id,)
            ).fetchone()
        assert row["status"] == "discarded"
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM staging_tables WHERE source_id = %s", (source_id,))


def test_list_tasks_recent_first():
    t1, t2 = str(uuid.uuid4()), str(uuid.uuid4())
    db.create_task(t1, str(uuid.uuid4()))
    db.create_task(t2, str(uuid.uuid4()))
    try:
        tasks = db.list_tasks(limit=10)
        ids = [t["task_id"] for t in tasks]
        assert ids.index(t2) < ids.index(t1)  # 최신 먼저
    finally:
        db.delete_task(t1)
        db.delete_task(t2)
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --no-project python -m pytest tests/test_db.py -v`
Expected: 신규 3개 FAIL — `AttributeError` (upsert_staged 없음)

- [ ] **Step 3: 구현** (`api/app/db.py`에 추가)

```python
STAGED_TABLES = ["technologies", "projects", "policy_events", "ministries"]

_UPSERT_SQL = {
    "technologies": """
        INSERT INTO technologies (name, field, sub_field, lead_ministry, trl_level,
                                  description, source_id)
        SELECT name, field, sub_field, lead_ministry, trl_level, description, source_id
        FROM staging.technologies WHERE source_id = %s
        ON CONFLICT (name) DO UPDATE SET
            field = EXCLUDED.field, sub_field = EXCLUDED.sub_field,
            lead_ministry = EXCLUDED.lead_ministry, trl_level = EXCLUDED.trl_level,
            description = EXCLUDED.description, source_id = EXCLUDED.source_id,
            updated_at = NOW()
    """,
    "ministries": """
        INSERT INTO ministries (name, abbreviation, wiki_page_path)
        SELECT name, abbreviation, wiki_page_path
        FROM staging.ministries WHERE source_id = %s
        ON CONFLICT (name) DO UPDATE SET abbreviation = EXCLUDED.abbreviation
    """,
    "projects": """
        INSERT INTO projects (project_code, name, lead_ministry, budget_total,
                              budget_annual, start_year, end_year, status, source_id)
        SELECT project_code, name, lead_ministry, budget_total, budget_annual,
               start_year, end_year, status, source_id
        FROM staging.projects WHERE source_id = %s AND project_code IS NOT NULL
        ON CONFLICT (project_code) DO UPDATE SET
            name = EXCLUDED.name, lead_ministry = EXCLUDED.lead_ministry,
            budget_total = EXCLUDED.budget_total, budget_annual = EXCLUDED.budget_annual,
            start_year = EXCLUDED.start_year, end_year = EXCLUDED.end_year,
            status = EXCLUDED.status, source_id = EXCLUDED.source_id
    """,
    # ponytail: project_code 없는 사업·policy_events는 자연키가 없어 단순 INSERT —
    # 동일 소스 재승인은 상태 가드(409)가 막아주므로 중복은 서로 다른 소스 간에만 발생 가능
    "_projects_no_code": """
        INSERT INTO projects (name, lead_ministry, budget_total, budget_annual,
                              start_year, end_year, status, source_id)
        SELECT name, lead_ministry, budget_total, budget_annual, start_year,
               end_year, status, source_id
        FROM staging.projects WHERE source_id = %s AND project_code IS NULL
    """,
    "policy_events": """
        INSERT INTO policy_events (event_date, event_type, title, description,
                                   affected_fields, wiki_page_path, source_id)
        SELECT event_date, event_type, title, description, affected_fields,
               wiki_page_path, source_id
        FROM staging.policy_events WHERE source_id = %s
    """,
}


def list_staged(source_id: str) -> dict:
    out = {}
    with connect() as conn:
        for t in STAGED_TABLES:
            out[t] = conn.execute(
                f"SELECT * FROM staging.{t} WHERE source_id = %s", (source_id,)
            ).fetchall()
        out["needs_review"] = conn.execute(
            "SELECT id, table_title, raw_data, suggested_mapping, mapping_confidence, status "
            "FROM staging_tables WHERE source_id = %s", (source_id,)
        ).fetchall()
    return out


def upsert_staged(source_id: str) -> dict:
    counts = {}
    with connect() as conn:  # 한 트랜잭션: 전부 성공 시에만 커밋
        for t in STAGED_TABLES:
            cur = conn.execute(_UPSERT_SQL[t], (source_id,))
            counts[t] = cur.rowcount
            if t == "projects":
                cur = conn.execute(_UPSERT_SQL["_projects_no_code"], (source_id,))
                counts[t] += cur.rowcount
            conn.execute(f"DELETE FROM staging.{t} WHERE source_id = %s", (source_id,))
    return counts


def discard_staged(source_id: str) -> None:
    with connect() as conn:
        for t in STAGED_TABLES:
            conn.execute(f"DELETE FROM staging.{t} WHERE source_id = %s", (source_id,))
        conn.execute(
            "UPDATE staging_tables SET status = 'discarded' WHERE source_id = %s",
            (source_id,),
        )


def list_tasks(limit: int = 50) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT task_id, source_id, status, branch_name, created_at, reviewed_at "
            "FROM ingest_tasks ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with "psycopg[binary]" --no-project python -m pytest tests/test_db.py -v`
Expected: 4 passed (기존 1 + 신규 3)

- [ ] **Step 5: Commit**

```bash
git add api/app/db.py api/tests/test_db.py
git commit -m "feat: staging 조회·upsert·폐기 및 태스크 목록 DB 헬퍼"
```

---

### Task 4: review·approve·reject·목록 엔드포인트

**Files:**
- Modify: `api/app/ingest_api.py`
- Test: `api/tests/test_ingest_api.py` (추가)

**Interfaces:**
- Consumes: Task 2 `wiki_ops.diff_branch/approve_branch/reject_branch`, Task 3 `db.list_staged/upsert_staged/discard_staged/list_tasks`, 기존 `require_admin`
- Produces:
  - `GET /api/v1/ingest` → `{"tasks": [...]}` (list_tasks)
  - `GET /api/v1/ingest/{task_id}/review` → `{status, wiki_diff, staged, contradictions, suggestions}` (suggestions는 affected_pages 중 action=="suggested"|"rejected")
  - `POST /api/v1/ingest/{task_id}/approve` [admin], body `{"contradiction_resolutions": {...}}` (선택) → 순서: upsert_staged → approve_branch(브랜치 있으면) → status approved + reviewed_at. staged 아니면 409
  - `POST /api/v1/ingest/{task_id}/reject` [admin] → discard_staged + reject_branch + status rejected + reviewed_at. staged 아니면 409

- [ ] **Step 1: 실패하는 테스트 작성** (`api/tests/test_ingest_api.py`에 추가)

```python
import uuid as _uuid


def test_review_and_approve_reject_flow(tmp_path, monkeypatch):
    from app import ingest_api
    # wiki_ops를 스텁으로: API 로직만 검증 (git 실동작은 test_wiki_ops가 검증)
    calls = {}
    monkeypatch.setattr(ingest_api.wiki_ops, "diff_branch", lambda r, s: "diff-텍스트")
    monkeypatch.setattr(ingest_api.wiki_ops, "approve_branch",
                        lambda r, s, m, resolutions=None: calls.setdefault("approve", resolutions))
    monkeypatch.setattr(ingest_api.wiki_ops, "reject_branch", lambda r, s: calls.setdefault("reject", s))
    monkeypatch.setattr(ingest_api.db, "upsert_staged", lambda s: {"technologies": 1})
    monkeypatch.setattr(ingest_api.db, "discard_staged", lambda s: None)
    monkeypatch.setattr(ingest_api.db, "list_staged", lambda s: {"technologies": [], "needs_review": []})

    from app import db as real_db
    task_id, source_id = str(_uuid.uuid4()), str(_uuid.uuid4())
    real_db.create_task(task_id, source_id)
    try:
        with real_db.connect() as conn:
            conn.execute(
                "UPDATE ingest_tasks SET status='staged', branch_name=%s, "
                "affected_pages=%s, contradictions=%s WHERE task_id=%s",
                (f"ingest/{source_id}",
                 '[{"path": "tech/a.md", "action": "create"}, {"path": "tech/b.md", "action": "suggested"}]',
                 '[{"summary": "모순1"}]', task_id),
            )
        r = client.get(f"/api/v1/ingest/{task_id}/review")
        assert r.status_code == 200
        body = r.json()
        assert body["wiki_diff"] == "diff-텍스트"
        assert body["source_id"] == source_id
        assert body["suggestions"] == [{"path": "tech/b.md", "action": "suggested"}]
        assert body["contradictions"] == [{"summary": "모순1"}]

        r = client.post(f"/api/v1/ingest/{task_id}/approve",
                        headers={"X-Admin-Key": "testkey"},
                        json={"contradiction_resolutions": {"x-1": "keep"}})
        assert r.status_code == 200
        assert calls["approve"] == {"x-1": "keep"}
        assert real_db.get_task(task_id)["status"] == "approved"

        # 이미 approved → 재승인 409
        r = client.post(f"/api/v1/ingest/{task_id}/approve", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409

        # reject도 409 (approved 상태)
        r = client.post(f"/api/v1/ingest/{task_id}/reject", headers={"X-Admin-Key": "testkey"})
        assert r.status_code == 409
    finally:
        real_db.delete_task(task_id)


def test_list_tasks_endpoint():
    r = client.get("/api/v1/ingest")
    assert r.status_code == 200
    assert "tasks" in r.json()
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with google-genai --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: 신규 2개 FAIL (404 — 엔드포인트 없음)

- [ ] **Step 3: 구현** (`api/app/ingest_api.py`에 추가)

상단 import에 추가:

```python
from datetime import datetime, timezone  # 기존 import 확인 — 이미 있으면 생략
from pydantic import BaseModel

import wiki_ops
```

엔드포인트 추가:

```python
class ApproveBody(BaseModel):
    contradiction_resolutions: dict[str, str] = {}


def _wiki_root() -> Path:
    return Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))


@router.get("/ingest")
def list_ingest_tasks():
    return {"tasks": db.list_tasks()}


@router.get("/ingest/{task_id}/review")
def review(task_id: str):
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    pages = task["affected_pages"] or []
    return {
        "status": task["status"],
        "source_id": task["source_id"],
        "wiki_diff": wiki_ops.diff_branch(_wiki_root(), task["source_id"]),
        "staged": db.list_staged(task["source_id"]),
        "affected_pages": pages,
        "suggestions": [p for p in pages if p.get("action") in ("suggested", "rejected")],
        "contradictions": task["contradictions"] or [],
    }


def _reviewable(task_id: str) -> dict:
    task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    if task["status"] != "staged":
        raise HTTPException(status_code=409, detail=f"not staged (status: {task['status']})")
    return task


@router.post("/ingest/{task_id}/approve", dependencies=[Depends(require_admin)])
def approve(task_id: str, body: ApproveBody | None = None):
    task = _reviewable(task_id)
    counts = db.upsert_staged(task["source_id"])
    if task["branch_name"]:
        resolutions = body.contradiction_resolutions if body else {}
        wiki_ops.approve_branch(
            _wiki_root(), task["source_id"],
            f"approve: {task['source_id']}", resolutions=resolutions or None,
        )
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status='approved', reviewed_at=%s WHERE task_id=%s",
            (datetime.now(timezone.utc), task_id),
        )
    return {"status": "approved", "upserted": counts}


@router.post("/ingest/{task_id}/reject", dependencies=[Depends(require_admin)])
def reject(task_id: str):
    task = _reviewable(task_id)
    db.discard_staged(task["source_id"])
    wiki_ops.reject_branch(_wiki_root(), task["source_id"])
    with db.connect() as conn:
        conn.execute(
            "UPDATE ingest_tasks SET status='rejected', reviewed_at=%s WHERE task_id=%s",
            (datetime.now(timezone.utc), task_id),
        )
    return {"status": "rejected"}
```

주의: `GET /ingest`와 `GET /ingest/{task_id}/status` 라우트 순서 충돌은 없다 (경로가 겹치지 않음). psycopg dict_row는 JSONB 컬럼을 파이썬 객체로 돌려주므로 `task["affected_pages"]`는 이미 list다.

- [ ] **Step 4: 테스트 통과 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with google-genai --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: 6 passed (기존 4 + 신규 2)

- [ ] **Step 5: Commit**

```bash
git add api/app/ingest_api.py api/tests/test_ingest_api.py
git commit -m "feat: 인제스트 목록·리뷰·승인·거부 엔드포인트 (상태 가드 409)"
```

---

### Task 5: 최소 승인 대시보드 (정적 HTML)

**Files:**
- Create: `api/app/static/dashboard.html`
- Modify: `api/app/main.py`, `api/Dockerfile`

**Interfaces:**
- Consumes: Task 4의 API 4종
- Produces: `GET /` → 대시보드 HTML. 기능: 태스크 목록(상태 뱃지, 최신순), 태스크 선택 시 리뷰 뷰(위키 diff `<pre>`, staging 테이블 HTML 표, 모순 목록에 keep/replace/both 선택, 제안 목록), admin key 입력(localStorage 저장), 승인/거부 버튼 → API 호출 후 목록 갱신

- [ ] **Step 1: HTML 작성**

`api/app/static/dashboard.html`:

```html
<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>nst-wiki 승인 대시보드</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 0; display: flex; height: 100vh; }
  #list { width: 340px; border-right: 1px solid #ccc; overflow-y: auto; padding: 12px; }
  #detail { flex: 1; overflow-y: auto; padding: 16px 24px; }
  .task { padding: 8px; border-bottom: 1px solid #eee; cursor: pointer; font-size: 13px; }
  .task:hover { background: #f5f5f5; }
  .badge { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: 11px; color: #fff; }
  .b-staged { background: #b58900; } .b-approved { background: #2a7d4f; }
  .b-rejected { background: #b3403a; } .b-failed { background: #777; }
  .b-parsing, .b-classifying, .b-queued { background: #4a76c9; }
  pre { background: #f6f6f6; padding: 12px; overflow-x: auto; font-size: 12px; }
  table { border-collapse: collapse; font-size: 12px; margin: 8px 0; }
  th, td { border: 1px solid #ddd; padding: 4px 8px; }
  button { padding: 8px 20px; margin-right: 8px; cursor: pointer; }
  #approve { background: #2a7d4f; color: #fff; border: 0; }
  #reject { background: #b3403a; color: #fff; border: 0; }
  #adminkey { width: 240px; }
  .contradiction { border: 1px solid #e5c07b; background: #fdf6e3; padding: 8px; margin: 6px 0; font-size: 13px; }
</style>
</head>
<body>
<div id="list">
  <p><input id="adminkey" type="password" placeholder="Admin Key"></p>
  <div id="tasks"></div>
</div>
<div id="detail"><p>왼쪽에서 태스크를 선택하세요.</p></div>
<script>
const $ = (s) => document.querySelector(s);
const keyInput = $("#adminkey");
keyInput.value = localStorage.getItem("adminkey") || "";
keyInput.addEventListener("change", () => localStorage.setItem("adminkey", keyInput.value));

let current = null;
let currentSource8 = "";

async function loadList() {
  const res = await fetch("/api/v1/ingest");
  const { tasks } = await res.json();
  $("#tasks").innerHTML = tasks.map(t => `
    <div class="task" onclick="openTask('${t.task_id}')">
      <span class="badge b-${t.status}">${t.status}</span>
      <code>${t.task_id.slice(0, 8)}</code><br>
      <small>${t.created_at ?? ""}</small>
    </div>`).join("");
}

async function openTask(taskId) {
  current = taskId;
  const res = await fetch(`/api/v1/ingest/${taskId}/review`);
  const r = await res.json();
  currentSource8 = (r.source_id || "").slice(0, 8);
  const stagedTables = Object.entries(r.staged || {})
    .filter(([k, v]) => k !== "needs_review" && v.length)
    .map(([k, v]) => `<h4>staging.${k} (${v.length}행)</h4>` + htmlTable(v)).join("");
  const needsReview = (r.staged?.needs_review || []).length
    ? `<h4>스키마 검토 필요 (${r.staged.needs_review.length}건)</h4>` + htmlTable(r.staged.needs_review)
    : "";
  const contradictions = (r.contradictions || []).map((c, i) => `
    <div class="contradiction">
      <b>${c.summary ?? ""}</b> — ${c.page ?? ""}<br>기존: ${c.existing ?? ""} / 신규: ${c.new ?? ""}<br>
      <label><input type="radio" name="res${i}" value="keep"> 기존 유지</label>
      <label><input type="radio" name="res${i}" value="replace" checked> 신규 채택</label>
      <label><input type="radio" name="res${i}" value="both"> 병기</label>
    </div>`).join("");
  const suggestions = (r.suggestions || []).map(s => `<li>${s.path} (${s.action})</li>`).join("");
  const actions = r.status === "staged"
    ? `<p><button id="approve" onclick="act('approve')">승인</button>
       <button id="reject" onclick="act('reject')">거부</button></p>` : "";
  $("#detail").innerHTML = `
    <h2><span class="badge b-${r.status}">${r.status}</span> ${taskId}</h2>
    ${actions}
    ${contradictions ? "<h3>모순 (" + r.contradictions.length + ")</h3>" + contradictions : ""}
    ${suggestions ? "<h3>갱신 제안 (미적용)</h3><ul>" + suggestions + "</ul>" : ""}
    ${stagedTables}${needsReview}
    <h3>위키 diff</h3><pre>${escapeHtml(r.wiki_diff || "(없음)")}</pre>`;
}

function htmlTable(rows) {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  return `<table><tr>${cols.map(c => `<th>${c}</th>`).join("")}</tr>` +
    rows.map(r => `<tr>${cols.map(c => `<td>${escapeHtml(String(r[c] ?? ""))}</td>`).join("")}</tr>`).join("") +
    "</table>";
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function act(action) {
  const body = { contradiction_resolutions: {} };
  // 모순 ID 규약은 log.md와 동일: {source_id 앞 8자}-{순번} (narrative.py가 기록하는 형식)
  document.querySelectorAll(".contradiction").forEach((el, i) => {
    const v = el.querySelector("input:checked");
    if (v) body.contradiction_resolutions[`${currentSource8}-${i + 1}`] = v.value;
  });
  const res = await fetch(`/api/v1/ingest/${current}/${action}`, {
    method: "POST",
    headers: { "X-Admin-Key": keyInput.value, "Content-Type": "application/json" },
    body: action === "approve" ? JSON.stringify(body) : null,
  });
  alert(action + ": " + res.status + " " + (await res.text()).slice(0, 300));
  await loadList();
  if (res.ok) openTask(current);
}

loadList();
setInterval(loadList, 15000);
</script>
</body>
</html>
```

- [ ] **Step 2: FastAPI에서 서빙** (`api/app/main.py`)

```python
from fastapi.responses import FileResponse
from pathlib import Path

STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(STATIC / "dashboard.html")
```

`api/Dockerfile`의 `COPY app ./app`은 static/까지 포함하므로 변경 불필요 — 확인만 할 것.

- [ ] **Step 3: 기동 확인**

Run: `docker compose up -d --build api && sleep 5 && curl -s http://localhost:8000/ | grep -o "<title>[^<]*</title>"`
Expected: `<title>nst-wiki 승인 대시보드</title>`

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with google-genai --no-project python -m pytest tests/test_ingest_api.py -v`
Expected: 6 passed

- [ ] **Step 4: Commit**

```bash
git add api/app/static/dashboard.html api/app/main.py
git commit -m "feat: 최소 승인 대시보드 (정적 HTML — 목록·diff·모순 해결·승인/거부)"
```

---

### Task 6: E2E 관통 (승인·거부) + README

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 1~5 전부, 실 Gemini (GEMINI_API_KEY)

- [ ] **Step 1: 승인 E2E** (실 Gemini — 문서 인제스트부터 승인까지)

Run:
```bash
docker compose up -d --build api ingest-worker
cat > /tmp/e2e-approve.md << 'EOF'
# 승인 테스트 문서

## 배경

양자컴퓨팅은 양자 분야의 국가전략기술이다. 한국표준과학연구원이 핵심 연구기관이다.
EOF
TASK=$(curl -s -X POST http://localhost:8000/api/v1/ingest -H "X-Admin-Key: devkey" \
  -F "file=@/tmp/e2e-approve.md" -F "title=승인 E2E" | python3 -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
sleep 60 && curl -s http://localhost:8000/api/v1/ingest/$TASK/status | python3 -m json.tool | head -3
```
Expected: `"status": "staged"`

Run:
```bash
curl -s http://localhost:8000/api/v1/ingest/$TASK/review | python3 -c "import sys,json; r=json.load(sys.stdin); print('diff:', len(r['wiki_diff']), 'chars; pages:', len(r['affected_pages']))"
curl -s -X POST http://localhost:8000/api/v1/ingest/$TASK/approve -H "X-Admin-Key: devkey" -H "Content-Type: application/json" -d '{}'
```
Expected: review에 diff/페이지 존재, approve 응답 `{"status": "approved", "upserted": {...}}`

Run: `docker compose exec api git -C /data/wiki log --oneline -3 && docker compose exec api git -C /data/wiki branch --list "ingest/*"`
Expected: main에 `approve:` 커밋 존재, 해당 ingest 브랜치는 삭제됨 (이전 E2E의 미승인 브랜치는 남아 있을 수 있음)

- [ ] **Step 2: 거부 E2E**

Run:
```bash
TASK2=$(curl -s -X POST http://localhost:8000/api/v1/ingest -H "X-Admin-Key: devkey" \
  -F "file=@/tmp/e2e-approve.md" -F "title=거부 E2E" | python3 -c "import sys,json;print(json.load(sys.stdin)['task_id'])")
sleep 60 && curl -s -X POST http://localhost:8000/api/v1/ingest/$TASK2/reject -H "X-Admin-Key: devkey"
curl -s http://localhost:8000/api/v1/ingest/$TASK2/status | python3 -m json.tool | head -3
```
Expected: reject 응답 `{"status": "rejected"}`, status 확인 `rejected`

- [ ] **Step 3: 전체 회귀**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --no-project python -m pytest tests -v`
Expected: 46 passed (38 + wiki_ops 3 + db 3 + ingest_api 2 — 합계 다르면 실제 수 기록, 전부 passed)

- [ ] **Step 4: README 갱신**

README의 인제스트 섹션 마지막 문단("파싱 후 Gemini가 ... 승인·병합 UI는 Phase 3.")을 다음으로 교체:

```markdown
파싱 후 Gemini가 내용을 분류·해석하여 서사는 위키 스테이징 브랜치(`ingest/{source_id}`)에,
표는 PostgreSQL `staging` 스키마에 적재한다 (status: `staged`).

**승인 대시보드**: http://localhost:8000/ — staged 태스크의 위키 diff·staging 데이터·모순을
검토하고 승인(위키 main 병합 + DB 반영) 또는 거부한다. 벡터 검색·질의는 Phase 4.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "docs: 승인 대시보드 사용법 추가, Phase 3 완료"
```
