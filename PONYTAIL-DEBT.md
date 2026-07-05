# Ponytail Debt Ledger

의도적으로 단순화한 지름길(`ponytail:` 주석)의 원장. 새 마커를 추가하면 이 파일도 갱신한다.
갱신 명령: `/ponytail-debt` 실행 후 결과 반영. (Python `#`, SQL `--` 주석 모두 스캔)

| 위치 | 단순화한 것 | 천장 (ceiling) | 재방문 트리거 (upgrade) |
|---|---|---|---|
| `db/init/001_schema.sql:97` | staging 미러를 `LIKE INCLUDING ALL`로 생성 (FK 미복사, 시퀀스는 public과 공유) | 스키마 변경 시 public/staging 동기화가 수동 규율에 의존 — 미러 드리프트 자동 감지 없음 | ✅ 2026-07-04 처리됨 — 002_unique_keys.sql로 적용 경로 확립, public 전용 사유는 파일 주석에 기록. 이후 DDL은 public/staging 동시 검토 후 결정 |
| `api/app/main.py:19` | `/health` 실패 시 예외 원인 문자열을 그대로 노출 | 내부 호스트명·계정명 등이 응답에 노출될 수 있음 (개인용 전제) | ⚠️ 코멘트엔 트리거 없음. 팀 공유(v2) 전환 또는 NPM Access List 없이 외부 노출 운영 시 → 원인 마스킹 |
| `api/app/query_api.py:14` | 인메모리 rate limit | uvicorn 단일 프로세스 전제 (스펙 8.3) | 다중 워커 도입 시 redis 기반으로 |
| `api/wiki_ops.py:18` | 프로세스 간 직렬화를 flock 하나로 | 단일 호스트 worker 전제 | 분산 워커 도입 시 재검토 |
| `api/pipeline/map_tables.py:21` | 표 값 선행 서식 제거를 정규식 휴리스틱으로 (완전한 파서 아님, YAGNI) | 알려진 서식 유형만 커버 (◯ ① - <n> § 등) | 새 서식 유형 나타나면 패턴 추가 |
| `api/pipeline/map_tables.py:101` | 기간 문자열 → start_year 첫 연도 / end_year 끝 연도 | 매핑 1:1이라 단일 기간 컬럼은 한쪽만 채움 | 단일 컬럼에서 양쪽 다 필요해지면 행 단위 분리 |
| `api/app/db.py:104` | project_code 없는 사업·policy_events 단순 INSERT (자연키 없음) | 동일 소스 재승인은 409가 차단, 중복은 서로 다른 소스 간에만 | ⚠️ 코멘트엔 트리거 없음. 교차 소스 이벤트 중복이 질의 노이즈가 되면 정본 dedup |
| `api/app/ingest_api.py:93` | 색인 enqueue 실패를 삼킴 (승인 롤백 안 함) | 승인은 유지, 벡터 색인만 누락 가능 | ⚠️ 자동 재시도 없음 — 누락 시 `POST /reindex` 수동 복구 |
| `api/text2sql.py:83` | LLM 생성 SQL 오류를 답변 합성에 넘김 | SQL 실패는 일상 (의도된 설계) | 되돌릴 트리거 없음 — 항구적 |

마지막 스캔: 2026-07-05 · `9 markers, 3 with no trigger (원장에서 트리거 부여)`
