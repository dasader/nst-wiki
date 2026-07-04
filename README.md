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
