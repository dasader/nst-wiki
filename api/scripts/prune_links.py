"""이미 main에 커밋된 페이지의 죽은 링크를 일회성으로 정리한다 (멱등).

narrative.prune_dead_links()는 컴파일 시점에만 돈다 — 그 코드가 들어오기 전에 만들어진
페이지는 LLM이 창작한 '없는 페이지·없는 테이블' 링크를 그대로 안고 있다. 한 번 돌려 확정한다.

대상은 PAGE_DIRS의 .md만. 제외:
- schema.md   : [[data:...]] 문법을 예시로 설명하는 규칙서. 정리하면 문서가 망가진다
- index.md    : rebuild_index가 만드는 파생 파일
- contradictions/log.md : 표 형태 로그, 링크 없음

    docker compose exec api python -m scripts.prune_links [--dry-run]
"""
import sys
from pathlib import Path

import wiki_ops
from pipeline.narrative import prune_dead_links

MESSAGE = "fix: 기존 페이지의 죽은 위키·데이터 링크 일괄 정리"


def prune_committed_pages(root: Path, dry_run: bool = False) -> list[str]:
    """정리된 페이지 경로 목록. 변경이 없으면 커밋하지 않는다 (재실행 안전)."""
    with wiki_ops._lock(root):
        wiki_ops._git(root, "checkout", "-f", "main")
        paths = wiki_ops.list_pages(root)
        files = {p: (root / p).read_text(encoding="utf-8") for p in paths}
        before = dict(files)
        prune_dead_links(files, paths)
        changed = [p for p in paths if files[p] != before[p]]
        if dry_run or not changed:
            return changed
        for p in changed:
            (root / p).write_text(files[p], encoding="utf-8")
        wiki_ops._git(root, "add", "--", *changed)
        wiki_ops._git(root, "commit", "-m", MESSAGE)
    return changed


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    root = wiki_ops.wiki_root()
    changed = prune_committed_pages(root, dry_run=dry)
    print(f"{'[dry-run] ' if dry else ''}정리된 페이지 {len(changed)}개")
    for p in changed:
        print("  -", p)
