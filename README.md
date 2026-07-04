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
| frontend | 3000 | Next.js UI — 질의·위키 브라우저·데이터 탐색기 (NPM이 nst-wiki.mem.photos로 프록시) |
| api | 8000 | FastAPI (NPM이 nst-wiki.mem.photos로 프록시) |
| postgres | - | 정형 데이터 (public) + 승인 대기 (staging) |
| qdrant | - | 벡터 검색 (Phase 4부터 사용) |
| redis | - | Celery 큐 (Phase 2부터 사용) |

볼륨: `wiki-data`(위키 git 저장소), `sources-data`(원본 문서), `pg-data`, `qdrant-data`

DB 스키마는 `db/init/NNN_*.sql` 넘버링 파일로만 변경한다 (LLM DDL 금지 — 설계서 원칙 5).
새 볼륨은 initdb가 순서대로 적용하고, 기존 볼륨에는 수동 적용한다:
`docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/NNN_*.sql`

## 문서 인제스트

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -F "file=@문서.pdf" -F "title=문서 제목" \
  -F "publisher=발행기관" -F "tags=NEXT,반도체"
# → {"task_id": "...", "status": "queued"}

curl http://localhost:8000/api/v1/ingest/<task_id>/status
# → {"status": "staged", ...}
```

지원 포맷: PDF(기본), MD(사전 변환 문서), XLSX(사업·기관 목록). 파싱 산출물은
`sources-data` 볼륨의 `{source_id}/parsed/`에 생성된다.
파싱 후 Gemini가 내용을 분류·해석하여 서사는 위키 스테이징 브랜치(`ingest/{source_id}`)에,
표는 PostgreSQL `staging` 스키마에 적재한다 (status: `staged`).

**승인 대시보드**: http://localhost:8000/ — staged 태스크의 위키 diff·staging 데이터·모순을
검토하고 승인(위키 main 병합 + DB 반영) 또는 거부한다. 자연어 질의는 아래 참조.
`.env`에 `GEMINI_API_KEY` 필수.

## 자연어 질의

```bash
curl -X POST http://localhost:8000/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "반도체 분야에 어떤 기술이 있어?"}'
# mode: auto(기본)/narrative/data/hybrid — 서사는 위키 벡터 검색, 데이터는 Text-to-SQL(읽기 전용)
```

승인된 페이지만 색인된다. 전체 재색인: `POST /api/v1/reindex` (admin key 필요).

## 테스트

```bash
cd api && uv run --with pytest --with fastapi --with httpx --with redis --with "psycopg[binary]" --with "celery[redis]" --with pandas --with openpyxl --with python-multipart --with google-genai --no-project python -m pytest tests -v
```

## 웹 UI

http://localhost:3000 — 자연어 질의(/), 위키 브라우저(/wiki), 데이터 탐색기(/data).
승인 대시보드는 http://localhost:8000/ (admin key 필요).

NPM 연동: `nst-wiki.mem.photos` → 호스트 3000 (UI). 승인 대시보드·API를 외부에서 쓰려면
별도 서브도메인 또는 경로로 호스트 8000을 추가 프록시하고 Access List를 걸 것.
