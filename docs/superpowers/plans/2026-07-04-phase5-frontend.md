# Phase 5: 프론트엔드 (Wiki 브라우저·질의 UI·데이터 탐색기) 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Next.js 프론트엔드(질의 UI·Wiki 브라우저·데이터 탐색기)와 이를 받치는 백엔드 읽기 API를 구축하여 브라우저만으로 시스템 전체를 사용할 수 있게 한다.

**Architecture:** 스펙 Layer 5(6.1절 읽기 엔드포인트 + 3개 뷰)를 구현한다. 백엔드에 read_api(위키 목록/페이지/전문검색, 데이터 테이블 조회 — 읽기 전용 롤 사용)를 추가하고, Next.js(App Router, plain JS)가 rewrites로 `/api/*`를 api 컨테이너에 프록시해 브라우저는 같은 출처만 본다. 승인 대시보드(Phase 3 정적 HTML)는 유지하고 프론트 네비게이션에서 링크한다.

**Tech Stack:** Next.js 15 (App Router, JS, standalone output), react-markdown+remark-gfm, node:22-alpine 2-stage Docker

## Global Constraints

- 스펙 편차 (의도된 단순화, 모두 기록): AG Grid 대신 경량 테이블+서버측 필터/정렬 (데이터 규모 소형), 위키 전문검색은 `git grep` 기반 (벡터 검색과 별개 — 모델 로드 없음, 자원 방침 부합), 페이지 의존성 그래프 시각화는 v2
- 데이터 조회는 READONLY_DATABASE_URL(wiki_ro) 사용, 테이블·컬럼은 코드 화이트리스트로만
- wiki 페이지 조회는 main 기준, path는 `wiki_ops.list_pages` 결과에 있는 것만 허용 (경로 주입 차단)
- 프론트: 외부 CDN 금지(전부 npm 번들), 인증 없음(조회 전용 — admin 작업은 기존 대시보드), 한국어 UI
- 커밋 메시지·문서 한국어. 백엔드 유닛테스트는 기존 패턴(uv), 프론트 검증은 docker build + 컨테이너 기동 + curl SSR 확인
- **범위 밖:** 승인 대시보드 개편, 인증/SSO, Lint(Phase 6), 그래프 시각화
- 백엔드 테스트 명령: Phase 4와 동일 (--with 목록 동일)

---

### Task 1: 백엔드 읽기 API (read_api.py)

**Files:**
- Create: `api/app/read_api.py`
- Modify: `api/app/main.py`
- Test: `api/tests/test_read_api.py`

**Interfaces:**
- Produces (모두 인증 없음 — 조회성):
  - `GET /api/v1/wiki` → `{"pages": [상대경로...]}` (main 기준)
  - `GET /api/v1/wiki/page?path=...` → `{"path", "content_md", "history": [{"hash","date","subject"}]}` — path가 list_pages에 없으면 404
  - `GET /api/v1/wiki/search?q=...` → `{"results": [{"path", "line"}]}` (git grep -in, 대소문자 무시, 최대 50)
  - `GET /api/v1/data/{table}?sort_by=&order=asc|desc&column=&q=&page=1&limit=50` → `{"rows", "total", "page", "limit"}` — DATA_TABLES 화이트리스트(6 core + budget_history + tech_project_mapping), sort_by·column은 해당 테이블 컬럼 목록에 있어야 함(아니면 400)

- [ ] **Step 1: 실패하는 테스트 작성**

`api/tests/test_read_api.py`:

```python
import os

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")
os.environ.setdefault("READONLY_DATABASE_URL",
                      "postgresql://wiki_ro:ro_devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app
from scripts.init_wiki import init_wiki

client = TestClient(app)


def _wiki_with_page(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    import wiki_ops
    wiki_ops.stage_changes(tmp_path, "sR", {"tech/read-test.md": "# 읽기\n\n검색가능한본문"}, "m")
    wiki_ops.approve_branch(tmp_path, "sR", "approve: m")
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))


def test_wiki_list_and_page(tmp_path, monkeypatch):
    _wiki_with_page(tmp_path, monkeypatch)
    r = client.get("/api/v1/wiki")
    assert "tech/read-test.md" in r.json()["pages"]
    r = client.get("/api/v1/wiki/page", params={"path": "tech/read-test.md"})
    assert r.status_code == 200
    body = r.json()
    assert body["content_md"].startswith("# 읽기")
    assert len(body["history"]) >= 1
    assert client.get("/api/v1/wiki/page", params={"path": "../etc/passwd"}).status_code == 404


def test_wiki_search(tmp_path, monkeypatch):
    _wiki_with_page(tmp_path, monkeypatch)
    r = client.get("/api/v1/wiki/search", params={"q": "검색가능한"})
    assert r.status_code == 200
    assert any(x["path"] == "tech/read-test.md" for x in r.json()["results"])
    assert client.get("/api/v1/wiki/search", params={"q": ""}).status_code == 422


def test_data_table_whitelist_and_query():
    assert client.get("/api/v1/data/ingest_tasks").status_code == 404  # 화이트리스트 밖
    assert client.get("/api/v1/data/technologies",
                      params={"sort_by": "없는컬럼"}).status_code == 400
    r = client.get("/api/v1/data/technologies", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"rows", "total", "page", "limit"}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with python-multipart --with qdrant-client --with google-genai --no-project python -m pytest tests/test_read_api.py -v`
Expected: FAIL (404/AttributeError — read_api 없음)

- [ ] **Step 3: 구현**

`api/app/read_api.py`:

```python
"""조회 전용 읽기 API: 위키 목록·페이지·전문검색, 데이터 테이블 (스펙 6.1)."""
import os
import subprocess
from pathlib import Path

import psycopg
from fastapi import APIRouter, HTTPException, Query
from psycopg.rows import dict_row

import wiki_ops

router = APIRouter(prefix="/api/v1")

DATA_TABLES = {
    "technologies": ["id", "name", "field", "sub_field", "lead_ministry", "trl_level",
                     "description", "source_id", "created_at", "updated_at"],
    "projects": ["id", "project_code", "name", "lead_ministry", "budget_total",
                 "budget_annual", "start_year", "end_year", "status", "source_id"],
    "policy_events": ["id", "event_date", "event_type", "title", "description",
                      "affected_fields", "source_id"],
    "ministries": ["id", "name", "abbreviation", "source_id"],
    "budget_history": ["id", "project_id", "fiscal_year", "amount", "source_id"],
    "tech_project_mapping": ["technology_id", "project_id", "relevance_score", "mapping_source"],
}


def _root() -> Path:
    return Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))


@router.get("/wiki")
def wiki_list():
    return {"pages": wiki_ops.list_pages(_root())}


@router.get("/wiki/page")
def wiki_page(path: str = Query(...)):
    root = _root()
    if path not in wiki_ops.list_pages(root):
        raise HTTPException(status_code=404, detail="page not found")
    content = wiki_ops.read_page(root, path) or ""
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%h\t%ad\t%s", "--date=short", "-5",
         "main", "--", path],
        capture_output=True, text=True, check=True,
    ).stdout
    history = [
        dict(zip(["hash", "date", "subject"], line.split("\t", 2)))
        for line in log.splitlines() if line
    ]
    return {"path": path, "content_md": content, "history": history}


@router.get("/wiki/search")
def wiki_search(q: str = Query(..., min_length=1)):
    out = subprocess.run(
        ["git", "-C", str(_root()), "grep", "-in", "--max-count=1", q, "main", "--", "*.md"],
        capture_output=True, text=True,
    )
    results = []
    for line in out.stdout.splitlines()[:50]:
        # 형식: main:tech/a.md:12:내용
        parts = line.split(":", 3)
        if len(parts) >= 4:
            results.append({"path": parts[1], "line": parts[3][:200]})
    return {"results": results}


@router.get("/data/{table}")
def data_table(table: str, sort_by: str | None = None, order: str = "asc",
               column: str | None = None, q: str | None = None,
               page: int = 1, limit: int = 50):
    cols = DATA_TABLES.get(table)
    if cols is None:
        raise HTTPException(status_code=404, detail="unknown table")
    if sort_by and sort_by not in cols:
        raise HTTPException(status_code=400, detail="invalid sort_by")
    if column and column not in cols:
        raise HTTPException(status_code=400, detail="invalid column")
    if order not in ("asc", "desc"):
        raise HTTPException(status_code=400, detail="invalid order")
    limit = max(1, min(limit, 200))
    page = max(1, page)
    where, params = "", []
    if column and q:
        where = f" WHERE {column}::text ILIKE %s"
        params.append(f"%{q}%")
    order_sql = f" ORDER BY {sort_by} {order.upper()}" if sort_by else ""
    with psycopg.connect(os.environ["READONLY_DATABASE_URL"], row_factory=dict_row,
                         options="-c statement_timeout=5000") as conn:
        total = conn.execute(
            f"SELECT count(*) AS n FROM {table}{where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM {table}{where}{order_sql} LIMIT %s OFFSET %s",
            params + [limit, (page - 1) * limit],
        ).fetchall()
    return {"rows": rows, "total": total, "page": page, "limit": limit}
```

`api/app/main.py`에 라우터 등록 (기존 include 옆):

```python
from app.read_api import router as read_router

app.include_router(read_router)
```

- [ ] **Step 4: 테스트 통과 + 전체 회귀**

Run: 위 명령 → 4 passed. 전체: `... -m pytest tests -v` → 77 passed

- [ ] **Step 5: Commit**

```bash
git add api/app/read_api.py api/app/main.py api/tests/test_read_api.py
git commit -m "feat: 읽기 API (위키 목록·페이지·전문검색, 데이터 테이블 조회)"
```

---

### Task 2: Next.js 스캐폴드 + 질의 UI

**Files:**
- Create: `frontend/package.json`, `frontend/next.config.mjs`, `frontend/jsconfig.json`, `frontend/Dockerfile`, `frontend/.dockerignore`, `frontend/app/layout.js`, `frontend/app/globals.css`, `frontend/app/page.js`
- Modify: `docker-compose.yml` (frontend 서비스)

**Interfaces:**
- Produces: `http://localhost:3000/` 질의 UI (질문 입력 → POST /api/v1/query → 답변·인용·SQL 표시). rewrites: `/api/:path*` → `${API_URL}/api/:path*` (기본 http://api:8000). 상단 네비: 질의 / 위키 / 데이터 / 승인(→:8000/)

- [ ] **Step 1: 파일 작성**

`frontend/package.json`:

```json
{
  "name": "nst-wiki-frontend",
  "private": true,
  "scripts": { "dev": "next dev", "build": "next build", "start": "next start" },
  "dependencies": {
    "next": "^15.1.0",
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-markdown": "^9.0.0",
    "remark-gfm": "^4.0.0"
  }
}
```

`frontend/next.config.mjs`:

```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const api = process.env.API_URL || "http://api:8000";
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};
export default nextConfig;
```

`frontend/jsconfig.json`: `{ "compilerOptions": { "paths": { "@/*": ["./*"] } } }`

`frontend/.dockerignore`:

```
node_modules
.next
```

`frontend/Dockerfile`:

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
EXPOSE 3000
CMD ["node", "server.js"]
```

`frontend/app/globals.css`:

```css
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; color: #1d2733; background: #fafbfc; }
nav { display: flex; gap: 20px; padding: 12px 24px; border-bottom: 1px solid #dfe4ea; background: #fff; }
nav a { text-decoration: none; color: #1d2733; font-weight: 600; font-size: 14px; }
nav a:hover { color: #0b62a4; }
main { max-width: 960px; margin: 0 auto; padding: 24px; }
input, select, button, textarea { font: inherit; padding: 8px 10px; border: 1px solid #c5ccd4; border-radius: 4px; }
button { background: #0b62a4; color: #fff; border: 0; cursor: pointer; }
button:disabled { opacity: .5; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { border: 1px solid #dfe4ea; padding: 6px 10px; text-align: left; }
th { background: #f1f4f7; cursor: pointer; user-select: none; }
pre { background: #f4f6f8; padding: 12px; overflow-x: auto; font-size: 12px; }
.card { background: #fff; border: 1px solid #dfe4ea; border-radius: 6px; padding: 16px 20px; margin: 12px 0; }
.cite { display: inline-block; margin: 2px 6px 2px 0; font-size: 12px; color: #0b62a4; }
```

`frontend/app/layout.js`:

```javascript
import "./globals.css";

export const metadata = { title: "NST Wiki" };

export default function RootLayout({ children }) {
  return (
    <html lang="ko">
      <body>
        <nav>
          <a href="/">질의</a>
          <a href="/wiki">위키</a>
          <a href="/data">데이터</a>
          <a href="http://localhost:8000/" target="_blank">승인 대시보드</a>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
```

`frontend/app/page.js`:

```javascript
"use client";
import { useState } from "react";

export default function QueryPage() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  async function ask(e) {
    e.preventDefault();
    setBusy(true); setErr(null); setRes(null);
    try {
      const r = await fetch("/api/v1/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, mode }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setRes(await r.json());
    } catch (e2) { setErr(String(e2)); }
    setBusy(false);
  }

  return (
    <div>
      <h1>자연어 질의</h1>
      <form onSubmit={ask} style={{ display: "flex", gap: 8 }}>
        <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="예: 반도체 분야에 어떤 기술이 있어?" required />
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="auto">auto</option><option value="narrative">서사</option>
          <option value="data">데이터</option><option value="hybrid">혼합</option>
        </select>
        <button disabled={busy}>{busy ? "질의 중…" : "질문"}</button>
      </form>
      {busy && <p>답변 생성 중입니다 (최초 질의는 모델 로드로 오래 걸릴 수 있음)…</p>}
      {err && <div className="card" style={{ color: "#b3403a" }}>{err}</div>}
      {res && (
        <div>
          <div className="card" style={{ whiteSpace: "pre-wrap" }}>{res.answer}</div>
          {res.citations?.length > 0 && (
            <div className="card">근거:{" "}
              {res.citations.map((c) => (
                <a key={c.path} className="cite" href={`/wiki/view?path=${encodeURIComponent(c.path)}`}>
                  [{c.path}]
                </a>
              ))}
            </div>
          )}
          {res.sql && (
            <div className="card">
              <b>SQL</b> ({res.sql_error ? `오류: ${res.sql_error}` : `${res.sql_rows.length}행`})
              <pre>{res.sql}</pre>
              {res.sql_rows?.length > 0 && <pre>{JSON.stringify(res.sql_rows, null, 2)}</pre>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: compose에 frontend 추가**

`docker-compose.yml` services에:

```yaml
  frontend:
    build: ./frontend
    ports: ["3000:3000"]              # NPM이 프록시할 포트
    restart: unless-stopped
    environment:
      - API_URL=http://api:8000
    depends_on:
      - api
```

(스펙 8.1의 `NEXT_PUBLIC_API_URL`은 rewrites 프록시로 대체 — 브라우저가 api 호스트명을 알 필요 없음)

- [ ] **Step 3: 빌드·기동 확인** (npm install+build 수 분 — timeout 600000ms)

Run: `docker compose up -d --build frontend && sleep 10 && curl -s http://localhost:3000/ | grep -o "자연어 질의" | head -1`
Expected: `자연어 질의`

Run: `curl -s -X POST http://localhost:3000/api/v1/query -H "Content-Type: application/json" -d '{"question": "q", "mode": "foo"}' -o /dev/null -w "%{http_code}\n"`
Expected: `422` (rewrites 프록시 동작 확인)

- [ ] **Step 4: Commit**

```bash
git add frontend docker-compose.yml
git commit -m "feat: Next.js 프론트엔드 스캐폴드 및 질의 UI (rewrites 프록시)"
```

---

### Task 3: Wiki 브라우저

**Files:**
- Create: `frontend/app/wiki/page.js`, `frontend/app/wiki/view/page.js`

**Interfaces:**
- Produces: `/wiki` — 페이지 목록(디렉토리 그룹) + 전문검색창. `/wiki/view?path=...` — 마크다운 렌더(remark-gfm), 프론트매터는 접힌 <details>로, `[[링크]]`를 내부 링크로 변환, git 이력 5건 표시

- [ ] **Step 1: 구현**

`frontend/app/wiki/page.js`:

```javascript
"use client";
import { useEffect, useState } from "react";

export default function WikiList() {
  const [pages, setPages] = useState([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);

  useEffect(() => {
    fetch("/api/v1/wiki").then((r) => r.json()).then((b) => setPages(b.pages || []));
  }, []);

  async function search(e) {
    e.preventDefault();
    if (!q) { setHits(null); return; }
    const r = await fetch(`/api/v1/wiki/search?q=${encodeURIComponent(q)}`);
    setHits((await r.json()).results || []);
  }

  const groups = {};
  for (const p of pages) {
    const dir = p.split("/")[0];
    (groups[dir] ||= []).push(p);
  }

  return (
    <div>
      <h1>위키</h1>
      <form onSubmit={search} style={{ display: "flex", gap: 8 }}>
        <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="전문 검색 (git grep)" />
        <button>검색</button>
      </form>
      {hits && (
        <div className="card">
          <b>검색 결과 {hits.length}건</b>
          <ul>{hits.map((h, i) => (
            <li key={i}>
              <a href={`/wiki/view?path=${encodeURIComponent(h.path)}`}>{h.path}</a>
              <small> — {h.line}</small>
            </li>
          ))}</ul>
        </div>
      )}
      {Object.entries(groups).map(([dir, ps]) => (
        <div className="card" key={dir}>
          <h3>{dir}/</h3>
          <ul>{ps.map((p) => (
            <li key={p}><a href={`/wiki/view?path=${encodeURIComponent(p)}`}>{p.split("/").slice(1).join("/")}</a></li>
          ))}</ul>
        </div>
      ))}
    </div>
  );
}
```

`frontend/app/wiki/view/page.js`:

```javascript
"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

function splitFrontmatter(md) {
  const m = md.match(/^---\n([\s\S]*?)\n---\n?/);
  return m ? { front: m[1], body: md.slice(m[0].length) } : { front: null, body: md };
}

function linkifyWiki(md) {
  // [[tech/foo]] → 내부 링크, [[data:...]] → 데이터 탐색기 링크
  return md
    .replace(/\[\[data:([^\]]+)\]\]/g, (_, ref) => `[data:${ref}](/data)`)
    .replace(/\[\[([^\]:]+)\]\]/g, (_, p) =>
      `[${p}](/wiki/view?path=${encodeURIComponent(p.endsWith(".md") ? p : p + ".md")})`);
}

function Viewer() {
  const path = useSearchParams().get("path");
  const [page, setPage] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!path) return;
    fetch(`/api/v1/wiki/page?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setPage, (e) => setErr(`페이지를 찾을 수 없습니다 (${e})`));
  }, [path]);

  if (err) return <div className="card">{err}</div>;
  if (!page) return <p>불러오는 중…</p>;
  const { front, body } = splitFrontmatter(page.content_md);
  return (
    <div>
      <p><a href="/wiki">← 위키 목록</a></p>
      <h1>{page.path}</h1>
      {front && <details className="card"><summary>메타데이터</summary><pre>{front}</pre></details>}
      <div className="card">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{linkifyWiki(body)}</ReactMarkdown>
      </div>
      <div className="card">
        <b>변경 이력</b>
        <ul>{page.history.map((h) => (
          <li key={h.hash}><code>{h.hash}</code> {h.date} — {h.subject}</li>
        ))}</ul>
      </div>
    </div>
  );
}

export default function WikiView() {
  return <Suspense fallback={<p>불러오는 중…</p>}><Viewer /></Suspense>;
}
```

- [ ] **Step 2: 빌드·확인**

Run: `docker compose up -d --build frontend && sleep 10 && curl -s "http://localhost:3000/wiki" | grep -o "위키" | head -1`
Expected: `위키`

Run: `curl -s "http://localhost:3000/api/v1/wiki" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['pages']), 'pages')"`
Expected: 1 이상 (승인된 페이지 존재)

- [ ] **Step 3: Commit**

```bash
git add frontend/app/wiki
git commit -m "feat: Wiki 브라우저 (목록·전문검색·마크다운 렌더·내부링크·이력)"
```

---

### Task 4: 데이터 탐색기

**Files:**
- Create: `frontend/app/data/page.js`

**Interfaces:**
- Produces: `/data` — 테이블 선택, 컬럼별 정렬(헤더 클릭 토글), 컬럼+검색어 필터, 페이지네이션. `GET /api/v1/data/{table}` 소비

- [ ] **Step 1: 구현**

`frontend/app/data/page.js`:

```javascript
"use client";
import { useEffect, useState } from "react";

const TABLES = ["technologies", "projects", "policy_events", "ministries",
                "budget_history", "tech_project_mapping"];

export default function DataExplorer() {
  const [table, setTable] = useState("technologies");
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState(null);
  const [order, setOrder] = useState("asc");
  const [column, setColumn] = useState("");
  const [q, setQ] = useState("");
  const limit = 50;

  async function load(p = page) {
    const params = new URLSearchParams({ page: p, limit });
    if (sortBy) { params.set("sort_by", sortBy); params.set("order", order); }
    if (column && q) { params.set("column", column); params.set("q", q); }
    const r = await fetch(`/api/v1/data/${table}?${params}`);
    if (!r.ok) return;
    const b = await r.json();
    setRows(b.rows); setTotal(b.total); setPage(b.page);
  }

  useEffect(() => { setSortBy(null); setColumn(""); setQ(""); setPage(1); }, [table]);
  useEffect(() => { load(1); }, [table, sortBy, order]);   // eslint-disable-line

  const cols = rows.length ? Object.keys(rows[0]) : [];
  const maxPage = Math.max(1, Math.ceil(total / limit));

  function clickSort(c) {
    if (sortBy === c) setOrder(order === "asc" ? "desc" : "asc");
    else { setSortBy(c); setOrder("asc"); }
  }

  return (
    <div>
      <h1>데이터 탐색기</h1>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <select value={table} onChange={(e) => setTable(e.target.value)}>
          {TABLES.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={column} onChange={(e) => setColumn(e.target.value)}>
          <option value="">필터 컬럼</option>
          {cols.map((c) => <option key={c}>{c}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="검색어" />
        <button onClick={() => load(1)}>적용</button>
        <span style={{ alignSelf: "center" }}>{total}건</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr>{cols.map((c) => (
            <th key={c} onClick={() => clickSort(c)}>
              {c}{sortBy === c ? (order === "asc" ? " ↑" : " ↓") : ""}
            </th>
          ))}</tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <p>
        <button disabled={page <= 1} onClick={() => load(page - 1)}>이전</button>
        {" "}{page}/{maxPage}{" "}
        <button disabled={page >= maxPage} onClick={() => load(page + 1)}>다음</button>
      </p>
    </div>
  );
}
```

- [ ] **Step 2: 빌드·확인**

Run: `docker compose up -d --build frontend && sleep 10 && curl -s "http://localhost:3000/data" | grep -o "데이터 탐색기" | head -1`
Expected: `데이터 탐색기`

- [ ] **Step 3: Commit**

```bash
git add frontend/app/data
git commit -m "feat: 데이터 탐색기 (테이블 선택·정렬·필터·페이지네이션)"
```

---

### Task 5: E2E + README + NPM 연동 노트

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 전체 스택 E2E**

Run:
```bash
docker compose up -d --build && sleep 15
for path in "/" "/wiki" "/data"; do curl -s -o /dev/null -w "$path: %{http_code}\n" http://localhost:3000$path; done
curl -s "http://localhost:3000/api/v1/wiki" -o /dev/null -w "proxy: %{http_code}\n"
curl -s -X POST http://localhost:3000/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "위키에 어떤 기술 페이지가 있어?", "mode": "narrative"}' | python3 -c "import sys,json; b=json.load(sys.stdin); print('answer:', len(b['answer']), 'chars; citations:', len(b['citations']))"
```
Expected: 세 경로 모두 200, proxy 200, 프론트 경유 실질의 성공 (answer 1자 이상)

Run: `cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with qdrant-client --with google-genai --no-project python -m pytest tests -v 2>&1 | tail -1`
Expected: 77 passed (백엔드 회귀)

- [ ] **Step 2: README 갱신**

`## 구성` 표의 api 행 앞에 frontend 행 추가:

```markdown
| frontend | 3000 | Next.js UI — 질의·위키 브라우저·데이터 탐색기 (NPM이 nst-wiki.mem.photos로 프록시) |
```

그리고 문서 끝(테스트 섹션 뒤)에 추가:

```markdown
## 웹 UI

http://localhost:3000 — 자연어 질의(/), 위키 브라우저(/wiki), 데이터 탐색기(/data).
승인 대시보드는 http://localhost:8000/ (admin key 필요).

NPM 연동: `nst-wiki.mem.photos` → 호스트 3000 (UI). 승인 대시보드·API를 외부에서 쓰려면
별도 서브도메인 또는 경로로 호스트 8000을 추가 프록시하고 Access List를 걸 것.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: 웹 UI 사용법·NPM 연동 추가, Phase 5 완료"
```
