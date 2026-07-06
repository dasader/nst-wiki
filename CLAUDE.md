# CLAUDE.md

국가전략기술(NEXT) 정책 문서를 LLM으로 컴파일하는 위키 시스템. 정책 PDF·XLSX를
받아 서사는 git 기반 위키로, 표는 PostgreSQL 정형 데이터로 만들고, 자연어 질의에
벡터 검색(서사) + Text-to-SQL(데이터)로 답한다.

설계서: `docs/superpowers/specs/2026-07-04-kistep-llm-wiki-design.md`

## 작업 워크플로

- **main 직접 커밋 금지.** 브랜치 → PR → 머지. PR은 squash merge.
- 커밋 메시지는 한국어, `type: 요약` 형식.

## 구조

```
api/
  app/            FastAPI 라우터: main, ingest_api, query_api, read_api
  app/static/     dashboard.html — 관리자 승인 대시보드 (api 오리진 :8000이 직접 서빙)
  pipeline/       인제스트: parse → classify → map_tables/events → narrative → compile
  llm.py          Gemini 호출 단일 창구 (용도별 설정은 llm_config.json)
  embeddings.py   BGE-M3 임베딩 + Qdrant 색인
  search.py       하이브리드(밀집+희소 RRF) 위키 검색
  text2sql.py     자연어 → SQL (읽기 전용 롤 + 코드 검증 이중 방어)
  wiki_ops.py     위키 git 저장소 조작 (브랜치·커밋·diff)
  scripts/init_wiki.py   위키 git 저장소 최초 초기화 (멱등)
  tasks.py        Celery 태스크 (run_ingest, embed_pages, ...)
frontend/app/     Next.js: 질문하기(/), 위키(/wiki), 데이터(/data)
db/init/          스키마 — NNN_*.sql 넘버링 파일
```

## 핵심 규칙

- **LLM 호출은 전부 `llm.py`를 거친다.** Gemini 단일 백엔드. 모델·thinking_level은
  용도(purpose)별로 `llm_config.json`에서 관리. 다른 LLM 진입점을 만들지 말 것.
- **DB 스키마는 `db/init/NNN_*.sql`로만 변경.** LLM은 DDL을 생성하지 않는다(설계 원칙 5).
  표는 고정 스키마에 매핑만 한다.
- **위키 쓰기는 `ingest/{source_id}` 브랜치에.** 승인 시 main으로 squash 병합.
  사람의 수동 편집(`PUT /wiki/page`)만 main에 직접 커밋한다.
- **PDF는 Gemini 네이티브 파싱**(`parse_pdf.py`, 2-call: 마크다운 + 구조화 표).
  Docling·OCR 의존은 제거됨.
- **임베딩은 자원 제약이 크다.** BGE-M3(~2.3GB/프로세스)는 지연 로드, 워커에서만
  실체화, `--concurrency=1`. 모델 병렬 로드를 늘리지 말 것.
- `ponytail:` 주석은 의도된 단순화 + 상향 경로를 표시한다 — 존중하고, 없앨 땐 근거를 확인.

## 테스트

pytest는 requirements에 없다(런타임 슬림 유지). **Docker에서 실행**:

```bash
# DB 불필요한 테스트 (classify·wiki_ops·parse 등)
docker run --rm -v "$PWD/api:/app" -w /app nst-wiki-api:latest \
  sh -c "pip install -q pytest && python -m pytest tests/test_classify.py -q"

# 전체 스위트 — postgres 필요, 컴포즈 네트워크에 붙여 실행
docker compose up -d postgres          # healthy 대기
docker run --rm --network nst-wiki_default -v "$PWD/api:/app" -w /app \
  -e DATABASE_URL="postgresql://wiki:devpass@postgres:5432/llm_wiki" \
  -e READONLY_DATABASE_URL="postgresql://wiki_ro:ro_devpass@postgres:5432/llm_wiki" \
  nst-wiki-api:latest sh -c "pip install -q pytest && python -m pytest -q"
```

주의: 인증 테스트는 `ADMIN_API_KEY`를 `setdefault("testkey")`로 잡고 `testkey`
헤더를 보낸다 — 실행 시 `ADMIN_API_KEY` env를 주입하면 `setdefault`가 무시돼 401이 난다.
프런트 검증 테스트는 `node --test api/app/static/upload-validate.test.mjs`.

## 이 호스트 주의

이 머신에서 **운영 스택(`docker compose`)이 실제로 돌고 있다.** 실행 중인 컨테이너를
stop/rm 하거나 그 이미지를 삭제하지 말 것 — 앱이 내려간다. 공간 확보가 필요하면
`docker builder prune`(빌드 캐시)가 안전하다.
