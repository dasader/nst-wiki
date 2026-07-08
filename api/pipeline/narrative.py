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

내부 링크는 아래 목록의 페이지로만 걸 수 있다. 목록에 없으면 링크하지 말고 평문으로 써라
(laws/·policy/ 같은 임의 디렉토리를 만들지 말 것):
{link_targets}

페이지 경로: {path} (title: {title})
기존 내용 (신규 페이지면 빈 값):
{current}

새 정보 (문서 「{doc_title}」에서 추출):
{narrative}

이 페이지에 관련된 내용만 반영하고, content에 페이지 전문을 반환하라."""


LINK_RE = re.compile(r"\[\[([^\]:]+)\]\]")  # [[data:...]]는 콜론이 있어 매칭되지 않는다


def _prune_dead_links(files: dict[str, str], existing: list[str]) -> None:
    """실존하지 않는 페이지를 가리키는 [[링크]]를 평문으로 낮춘다 (in-place).

    프롬프트로 대상 목록을 줘도 LLM은 laws/·policy/ 같은 없는 디렉토리나 미생성 페이지로
    링크를 만든다. schema.md 규칙: "대상 페이지가 없으면 링크를 생략한다".
    """
    valid = {p if p.endswith(".md") else f"{p}.md" for p in (*existing, *files)}

    def keep_or_flatten(m: re.Match) -> str:
        target = m.group(1)
        norm = target if target.endswith(".md") else f"{target}.md"
        return m.group(0) if norm in valid else target

    for path in files:
        files[path] = LINK_RE.sub(keep_or_flatten, files[path])


def _strip_uc(fm: str) -> str:
    """프론트매터에서 기존 unresolved_contradictions 블록 제거 — 재병합 시 중복 방지."""
    out, skip = [], False
    for ln in fm.splitlines():
        if ln.startswith("unresolved_contradictions:"):
            skip = True
            continue
        if skip and ln.startswith("  "):  # 들여쓴 리스트 항목
            continue
        skip = False
        out.append(ln)
    return "\n".join(out)


def _inject_contradictions(content: str, items: list[dict]) -> str:
    """페이지 YAML 프론트매터에 unresolved_contradictions 리스트를 심어 본문에서도 충돌이 보이게 한다."""
    lines = [f'  - "{c["summary"]} (기존: {c["existing"]} / 신규: {c["new"]})"' for c in items]
    block = "unresolved_contradictions:\n" + "\n".join(lines)
    if content.startswith("---"):
        _, fm, body = content.split("---", 2)
        fm = _strip_uc(fm).rstrip("\n") + "\n" + block + "\n"
        return f"---{fm}---{body}"
    return f"---\n{block}\n---\n\n{content}"  # 프론트매터 없으면 새로 생성


def compile_narrative(wiki_root: Path, source_id: str, meta: dict,
                      narrative_texts: list[str]) -> dict:
    today = date.today().isoformat()
    narrative = "\n\n".join(narrative_texts)
    existing_pages = wiki_ops.list_pages(wiki_root)
    existing = "\n".join(existing_pages) or "(없음)"
    plan = llm.generate("plan_pages", PLAN_PROMPT.format(
        existing=existing, title=meta.get("title", ""), narrative=narrative,
    ), schema=PLAN_SCHEMA)

    # 이번 배치에서 실제로 만들어질 페이지 = 링크를 걸어도 되는 대상 (기존 페이지와 합집합)
    planned = [p["path"] for i, p in enumerate(plan["pages"])
               if PATH_RE.match(p["path"]) and i < MAX_PAGES]
    link_targets = "\n".join(sorted({*existing_pages, *planned})) or "(없음)"

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
            link_targets=link_targets,
        ), schema=MERGE_SCHEMA)
        content = merged["content"]
        if merged["contradictions"]:
            content = _inject_contradictions(content, merged["contradictions"])
        files[page["path"]] = content
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
    _prune_dead_links(files, existing_pages)  # 모든 페이지 확정 후: 깨진 링크 평문화
    return {"files": files, "affected_pages": affected, "contradictions": contradictions}
