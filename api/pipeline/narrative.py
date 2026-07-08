"""서사 경로: 페이지 계획(LLM) → 페이지별 병합(LLM) → 모순 기록. 산출물은 파일 dict."""
import logging
import re
from datetime import date
from pathlib import Path

import llm
import wiki_ops

log = logging.getLogger(__name__)

MAX_PAGES = 15
SUMMARY_CHARS = 2000

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


SUMMARY_PROMPT = """다음 정책문서를 위키 요약 페이지용으로 요약하라.

규칙:
- 마크다운 소제목(##)으로 구조화한다. 문서 제목은 반복하지 않는다
- 핵심 목표·대상 기술·추진 체계·수치 목표를 빠뜨리지 않는다
- 원문에 없는 내용을 지어내지 않는다
- 위키 링크([[...]])와 표는 넣지 않는다 — 서술형 문장으로 쓴다

문서 제목: {title}
문서 내용:
{narrative}"""


def _summarize(title: str, narrative: str) -> str:
    """소스 요약 페이지 본문 — LLM 요약.

    요약 호출 실패로 인제스트 전체를 죽이지 않는다. 실패하면 원문 발췌로 대체하고 경고를 남긴다.
    """
    try:
        return str(llm.generate("summarize_source", SUMMARY_PROMPT.format(
            title=title, narrative=narrative,
        ))).strip()
    except Exception:
        log.warning("소스 요약 실패 — 원문 발췌로 대체", exc_info=True)
        return _excerpt(narrative)


def _excerpt(text: str, limit: int = SUMMARY_CHARS) -> str:
    """요약 실패 시의 대체 본문. 줄 경계에서 자르고, 잘렸으면 그 사실을 명시한다.

    문장 한가운데서 끊으면 "쓰다 만 문서"로 읽힌다. 잘라낸 사실도 조용히 숨기지 않는다
    (no silent caps).
    """
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit  # 줄바꿈 없는 한 덩어리면 어쩔 수 없이 하드 절단
    log.warning("요약 발췌: 전체 %d자 중 %d자만 페이지에 실음", len(text), cut)
    return (f"{text[:cut].rstrip()}\n\n"
            f"_(발췌 — 전체 {len(text):,}자 중 앞부분만 실었습니다. 전체 내용은 원본 문서를 참조하세요)_")


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

    title = meta.get("title", source_id)
    files[f"summaries/{source_id}.md"] = (
        f"# {title}\n\n- source_id: {source_id}\n"
        f"- ingest: {today}\n\n{_summarize(title, narrative)}\n"
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
