"""위키 데이터 저장소를 초기화한다. 멱등: 이미 git 저장소면 아무것도 하지 않는다."""
import os
import subprocess
from pathlib import Path

import wiki_ops

DIRS = ["tech", "entity", "events", "synthesis", "summaries", "contradictions"]

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

| ID | 발견일 | 대상 페이지 | 요약 | 기존 주장 | 신규 주장 | 상태 |
|---|---|---|---|---|---|---|
"""


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def init_wiki(root: Path) -> bool:
    if (root / ".git").exists():
        return False
    root.mkdir(parents=True, exist_ok=True)
    for d in DIRS:
        (root / d).mkdir(exist_ok=True)
    (root / "index.md").write_text(wiki_ops.rebuild_index(root), encoding="utf-8")
    (root / "schema.md").write_text(SCHEMA_MD, encoding="utf-8")
    (root / "contradictions" / "log.md").write_text(LOG_MD, encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "wiki-bot@nst-wiki.local")
    _git(root, "config", "user.name", "nst-wiki bot")
    # 한글 파일명이 ls-tree/grep 출력에서 8진수로 quote되지 않게 (경로 링크 깨짐 방지)
    _git(root, "config", "core.quotePath", "false")
    _git(root, "add", "-A")
    _git(root, "commit", "-m", "chore: 위키 저장소 초기화")
    return True


if __name__ == "__main__":
    root = Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))
    created = init_wiki(root)
    print(f"initialized: {root}" if created else f"already initialized: {root}")
