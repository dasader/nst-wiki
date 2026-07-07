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
curl http://localhost:8033/health
```

## 구성

| 서비스 | 포트 | 역할 |
|---|---|---|
| frontend | 8133 | Next.js UI — 질의·위키 브라우저·데이터 탐색기 (리버스 프록시로 노출) |
| api | 8033 | FastAPI (리버스 프록시로 노출) |
| postgres | - | 정형 데이터 (public) + 승인 대기 (staging) |
| qdrant | - | 벡터 검색 (Phase 4부터 사용) |
| redis | - | Celery 큐 (Phase 2부터 사용) |

볼륨: `wiki-data`(위키 git 저장소), `sources-data`(원본 문서), `pg-data`, `qdrant-data`

DB 스키마는 `db/init/NNN_*.sql` 넘버링 파일로만 변경한다 (LLM DDL 금지 — 설계서 원칙 5).
새 볼륨은 initdb가 순서대로 적용하고, 기존 볼륨에는 수동 적용한다:
`docker compose exec postgres psql -U wiki -d llm_wiki -f /docker-entrypoint-initdb.d/NNN_*.sql`

## 문서 인제스트

```bash
curl -X POST http://localhost:8033/api/v1/ingest \
  -H "X-Admin-Key: $ADMIN_API_KEY" \
  -F "file=@문서.pdf" -F "title=문서 제목" \
  -F "publisher=발행기관" -F "tags=NEXT,반도체"
# → {"task_id": "...", "status": "queued"}

curl http://localhost:8033/api/v1/ingest/<task_id>/status
# → {"status": "staged", ...}
```

지원 포맷: PDF(기본), MD(사전 변환 문서), XLSX(사업·기관 목록). PDF는 Gemini
네이티브 파싱(마크다운 + 구조화 표 2-call)으로 처리한다 — 별도 OCR·Docling 불필요.
파싱 산출물은 `sources-data` 볼륨의 `{source_id}/parsed/`에 생성된다.
이어 Gemini가 내용을 분류·해석하여 서사는 위키 스테이징 브랜치(`ingest/{source_id}`)에,
표는 PostgreSQL `staging` 스키마에 적재한다 (status: `staged`).

**승인 대시보드**: http://localhost:8033/ — 문서 업로드(드래그·드롭), staged 태스크의
위키 diff·staging 데이터·모순 검토, 승인(위키 main 병합 + DB 반영)/거부, 소스 삭제·
위키 페이지 편집까지 한 화면에서 처리한다. `.env`에 `GEMINI_API_KEY` 필수.

## 자연어 질의

```bash
curl -X POST http://localhost:8033/api/v1/query -H "Content-Type: application/json" \
  -d '{"question": "반도체 분야에 어떤 기술이 있어?"}'
# mode: auto(기본)/narrative/data/hybrid — 서사는 위키 벡터 검색, 데이터는 Text-to-SQL(읽기 전용)
```

승인된 페이지만 색인된다. 전체 재색인: `POST /api/v1/reindex` (admin key 필요).

## 테스트

pytest는 런타임 이미지에 없으므로 빌드된 api 이미지에 얹어 실행한다(DB 필요한
테스트는 postgres 기동 후 컴포즈 네트워크에 붙인다). 자세한 명령은 `CLAUDE.md` 참조.

```bash
docker compose up -d postgres
docker run --rm --network nst-wiki_default -v "$PWD/api:/app" -w /app \
  -e DATABASE_URL="postgresql://wiki:devpass@postgres:5432/llm_wiki" \
  -e READONLY_DATABASE_URL="postgresql://wiki_ro:ro_devpass@postgres:5432/llm_wiki" \
  nst-wiki-api:latest sh -c "pip install -q pytest && python -m pytest -q"
```

## 웹 UI

http://localhost:8133 — 자연어 질의(/), 위키 브라우저(/wiki), 데이터 탐색기(/data).
승인 대시보드는 http://localhost:8033/ (admin key 필요).

리버스 프록시(예: Nginx Proxy Manager)로 호스트 8133(UI)을 노출한다. 승인 대시보드·API를
외부에서 쓰려면 별도 서브도메인 또는 경로로 호스트 8033을 추가 프록시하고 Access List를 걸 것.
8133 포트 UI도 무인증 질의(/api/v1/query)가 프록시되므로 외부 공개 시 Access List를 함께 걸 것.
