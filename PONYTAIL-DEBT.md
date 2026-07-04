# Ponytail Debt Ledger

의도적으로 단순화한 지름길(`ponytail:` 주석)의 원장. 새 마커를 추가하면 이 파일도 갱신한다.
갱신 명령: `/ponytail-debt` 실행 후 결과 반영.

| 위치 | 단순화한 것 | 천장 (ceiling) | 재방문 트리거 (upgrade) |
|---|---|---|---|
| `db/init/001_schema.sql:97` | staging 미러를 `LIKE INCLUDING ALL`로 생성 (FK 미복사, 시퀀스는 public과 공유) | 스키마 변경 시 public/staging 동기화가 수동 규율에 의존 — 미러 드리프트 자동 감지 없음 | ✅ 2026-07-04 처리됨 — 002_unique_keys.sql로 적용 경로 확립, public 전용 사유는 파일 주석에 기록. 이후 DDL은 public/staging 동시 검토 후 결정 |
| `api/app/main.py:16` | `/health` 실패 시 예외 원인 문자열을 그대로 노출 | 내부 호스트명·계정명 등이 응답에 노출될 수 있음 (개인용 전제) | 팀 공유(v2) 전환 시, 또는 NPM Access List 없이 외부 노출 운영 시 → 원인 마스킹 |

마지막 스캔: 2026-07-04 · `2 markers, 0 with no trigger` (트리거는 이 원장에서 부여)
