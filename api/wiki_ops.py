"""위키 git 저장소 조작. 쓰기는 ingest/{source_id} 브랜치에만 — main 직접 커밋 금지."""
import fcntl
import subprocess
from contextlib import contextmanager
from pathlib import Path

PAGE_DIRS = ["tech", "entity", "events", "synthesis", "summaries"]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


@contextmanager
def _lock(root: Path):
    # ponytail: 프로세스 간 직렬화는 flock 하나로 충분 (단일 호스트 worker 전제), 분산 워커 도입 시 재검토
    # 잠금 파일은 저장소 밖 사이드카 — 작업 트리 안에 두면 git add -A에 커밋된다
    with open(root.parent / f"{root.name}.ingest.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def list_pages(root: Path) -> list[str]:
    out = []
    for d in PAGE_DIRS:
        out += sorted(
            str(p.relative_to(root)) for p in (root / d).glob("*.md")
        ) if (root / d).is_dir() else []
    return out


def read_page(root: Path, rel: str) -> str | None:
    p = root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


def read_page_asof(root: Path, rel: str, iso_date: str) -> str | None:
    """iso_date(YYYY-MM-DD) 이하 마지막 main 커밋 시점의 페이지 내용. 그때 없으면 None.
    구조화 데이터는 이력 테이블이 없어 as-of 대상 밖 — 위키(git 이력)만 시점 조회 가능."""
    commit = _git(root, "rev-list", "-1", f"--before={iso_date}", "main").strip()
    if not commit:
        return None  # 그 날짜 이전 커밋 없음 (페이지 생성 전)
    show = subprocess.run(
        ["git", "-C", str(root), "show", f"{commit}:{rel}"],
        capture_output=True, text=True,
    )
    return show.stdout if show.returncode == 0 else None


def rebuild_index(root: Path) -> str:
    lines = ["# NST Wiki 색인", "",
             "국가전략기술 정책 지식 위키. 페이지가 생성·갱신되면 이 색인도 함께 갱신한다.", ""]
    titles = {"tech": "tech (기술 개념)", "entity": "entity (정책 엔티티)",
              "events": "events (정책변화 이력)", "synthesis": "synthesis (종합·비교 분석)"}
    for d, title in titles.items():
        lines += [f"## {title}", ""]
        pages = sorted((root / d).glob("*.md")) if (root / d).is_dir() else []
        lines += [f"- [[{d}/{p.stem}]]" for p in pages] or ["(아직 페이지 없음)"]
        lines.append("")
    return "\n".join(lines)


def stage_changes(root: Path, source_id: str, files: dict[str, str], message: str) -> str:
    branch = f"ingest/{source_id}"
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        _git(root, "clean", "-fd")
        _git(root, "checkout", "-B", branch)
        try:
            for rel, content in files.items():
                p = (root / rel).resolve()
                if not p.is_relative_to(root.resolve()):
                    raise ValueError(f"path escapes wiki root: {rel}")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")
            (root / "index.md").write_text(rebuild_index(root), encoding="utf-8")
            _git(root, "add", "-A")
            _git(root, "commit", "-m", message)
        finally:
            _git(root, "checkout", "-f", "main")
            _git(root, "clean", "-fd")
    return branch


def _branch_exists(root: Path, branch: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--verify", "--quiet", branch],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def diff_branch(root: Path, source_id: str) -> str:
    branch = f"ingest/{source_id}"
    if not _branch_exists(root, branch):
        return ""
    return _git(root, "diff", f"main...{branch}")


def approve_branch(root: Path, source_id: str, message: str,
                   resolutions: dict[str, str] | None = None) -> None:
    branch = f"ingest/{source_id}"
    with _lock(root):
        if not _branch_exists(root, branch):
            return  # 이미 병합·삭제된 브랜치 — 재시도 멱등
        _git(root, "checkout", "-f", "main")
        _git(root, "clean", "-fd")
        _git(root, "merge", "--squash", branch)
        if resolutions:
            log_path = root / "contradictions" / "log.md"
            log = log_path.read_text(encoding="utf-8")
            for cid, res in resolutions.items():
                lines = log.splitlines(keepends=True)
                log = "".join(
                    ln.replace("| 미해결 |", f"| 해결({res}) |") if f"| {cid} |" in ln else ln
                    for ln in lines
                )
            log_path.write_text(log, encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-m", message)
        _git(root, "branch", "-D", branch)


def reject_branch(root: Path, source_id: str) -> None:
    branch = f"ingest/{source_id}"
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        _git(root, "clean", "-fd")
        if _branch_exists(root, branch):
            _git(root, "branch", "-D", branch)
