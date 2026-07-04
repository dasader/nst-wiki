"""서사 경로: 페이지 계획(LLM) → 페이지별 병합(LLM) → 모순 기록. 산출물은 파일 dict."""
import re
from datetime import date
from pathlib import Path

import llm
import wiki_ops

MAX_PAGES = 15

PATH_RE = re.compile(r"^(tech|entity|events|synthesis)/[\w가-힣.-]+\.md$")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "update"]},
                    "title": {"type": "string"},
                },
                "required": ["path", "action", "title"],
            },
        }
    },
    "required": ["pages"],
}

PLAN_PROMPT = """새 정책문서가 인제스트되었다. 아래 서사 내용을 반영해야 할 위키 페이지 목록을 계획하라.

디렉토리 규칙:
- tech/: 기술 개념 (영문 케밥케이스, 예: hbm-semiconductor.md)
- entity/: 부처·기관 (한글 기관명, 예: 과기정통부.md)
- events/: 정책변화 이력 (YYYY-MM-슬러그.md)
- synthesis/: 종합·비교 분석

기존 페이지 목록 (있으면 update, 없으면 create):
{existing}

문서 제목: {title}
서사 내용:
{narrative}

중요도 순으로 정렬해 반환하라. 반영할 실질 내용이 있는 페이지만 포함하라."""

MERGE_SCHEMA = {
    "type": "object",
    "properties": {
        "content": {"type": "string"},
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string"},
                    "existing": {"type": "string"},
                    "new": {"type": "string"},
                },
                "required": ["summary", "existing", "new"],
            },
        },
    },
    "required": ["content", "contradictions"],
}

MERGE_PROMPT = """위키 페이지를 갱신하라. 규칙:
- 기존 내용을 보존하며 새 정보를 병합한다 (통째 재작성 금지)
- 페이지는 YAML 프론트매터로 시작: title, type, related_pages, sources (source_id "{source_id}"와 last_updated "{today}"를 sources에 추가)
- 내부 링크는 [[디렉토리/파일명]] 형식, 정형 수치는 본문 하드코딩 대신 [[data:테이블?조건]] 참조
- 기존 서술과 새 정보가 충돌하면 본문을 임의로 교체하지 말고 contradictions에 기록하라

페이지 경로: {path} (title: {title})
기존 내용 (신규 페이지면 빈 값):
{current}

새 정보 (문서 「{doc_title}」에서 추출):
{narrative}

이 페이지에 관련된 내용만 반영하고, content에 페이지 전문을 반환하라."""


def compile_narrative(wiki_root: Path, source_id: str, meta: dict,
                      narrative_texts: list[str]) -> dict:
    today = date.today().isoformat()
    narrative = "\n\n".join(narrative_texts)
    existing = "\n".join(wiki_ops.list_pages(wiki_root)) or "(없음)"
    plan = llm.generate("plan_pages", PLAN_PROMPT.format(
        existing=existing, title=meta.get("title", ""), narrative=narrative,
    ), schema=PLAN_SCHEMA)

    files: dict[str, str] = {}
    affected, contradictions = [], []
    for i, page in enumerate(plan["pages"]):
        if not PATH_RE.match(page["path"]):
            affected.append({"path": page["path"], "action": "rejected"})
            continue
        if i >= MAX_PAGES:
            affected.append({"path": page["path"], "action": "suggested"})
            continue
        current = wiki_ops.read_page(wiki_root, page["path"]) or ""
        merged = llm.generate("merge_page", MERGE_PROMPT.format(
            source_id=source_id, today=today, path=page["path"], title=page["title"],
            current=current, doc_title=meta.get("title", ""), narrative=narrative,
        ), schema=MERGE_SCHEMA)
        files[page["path"]] = merged["content"]
        affected.append({"path": page["path"], "action": page["action"]})
        for c in merged["contradictions"]:
            contradictions.append({**c, "page": page["path"]})

    files[f"summaries/{source_id}.md"] = (
        f"# {meta.get('title', source_id)}\n\n- source_id: {source_id}\n"
        f"- ingest: {today}\n\n{narrative[:2000]}\n"
    )
    if contradictions:
        log = wiki_ops.read_page(wiki_root, "contradictions/log.md") or ""
        rows = "".join(
            f"| {source_id[:8]}-{i+1} | {today} | {c['page']} | {c['summary']} | {c['existing']} | {c['new']} | 미해결 |\n"
            for i, c in enumerate(contradictions)
        )
        files["contradictions/log.md"] = log.rstrip("\n") + "\n" + rows
    return {"files": files, "affected_pages": affected, "contradictions": contradictions}
