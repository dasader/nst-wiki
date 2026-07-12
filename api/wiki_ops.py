"""위키 git 저장소 조작. 쓰기는 ingest/{source_id} 브랜치에만 — main 직접 커밋 금지."""
import fcntl
import os
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

PAGE_DIRS = ["tech", "entity", "events", "synthesis", "summaries"]


def wiki_root() -> Path:
    """위키 git 저장소 경로 — WIKI_REPO_PATH 기본값의 단일 출처."""
    return Path(os.environ.get("WIKI_REPO_PATH", "/data/wiki"))


def sources_root() -> Path:
    """업로드 원본 저장 경로 — SOURCES_PATH 기본값의 단일 출처."""
    return Path(os.environ.get("SOURCES_PATH", "/data/sources"))


def page_path_re(dirs: list[str] = PAGE_DIRS) -> re.Pattern:
    """PAGE_DIRS/파일명.md 형태의 페이지 경로 정규식 — 디렉토리 목록의 단일 출처."""
    return re.compile(rf"^({'|'.join(dirs)})/[\w가-힣.-]+\.md$")


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout


def _restore_main(root: Path) -> None:
    """작업 트리를 깨끗한 main으로 되돌린다 (squash 병합은 MERGE_HEAD가 없어 abort 불가)."""
    _git(root, "checkout", "-f", "main")
    _git(root, "clean", "-fd")


def _show_stage(root: Path, stage: int, path: str) -> str:
    """충돌 인덱스의 한 단계 내용. :1:=공통조상 :2:=main측 :3:=병합 브랜치측. 없으면 빈 문자열."""
    out = subprocess.run(["git", "-C", str(root), "show", f":{stage}:{path}"],
                         capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else ""


def _ensure_repo(root: Path) -> None:
    """위키 git 저장소가 없으면 초기화한다 (멱등). 항상 _lock 안에서만 호출할 것.

    신규 배포는 위키 볼륨이 비어 있다 — 초기화가 수동 단계뿐이면 첫 인제스트가
    `git checkout -f main`에서 exit 128로 죽는다. 여기서 자동 보장한다.
    """
    if (root / ".git").exists():
        return
    from scripts.init_wiki import init_wiki  # 지연 임포트: init_wiki가 wiki_ops를 import (순환 회피)

    init_wiki(root)


@contextmanager
def _lock(root: Path):
    # ponytail: 프로세스 간 직렬화는 flock 하나로 충분 (단일 호스트 worker 전제), 분산 워커 도입 시 재검토
    # 잠금 파일은 저장소 밖 사이드카 — 작업 트리 안에 두면 git add -A에 커밋된다
    with open(root.parent / f"{root.name}.ingest.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        _ensure_repo(root)  # 잠금 안에서 초기화 — api·worker 동시 기동 시 중복 git init 방지
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


def write_page(root: Path, rel: str, content: str, message: str) -> bool:
    """main에 페이지 하나를 쓰고 커밋한다 (관리자 수동 편집용). 내용 변화가 없으면 커밋 생략 → False.

    인제스트 파이프라인은 ingest/* 브랜치만 쓰지만, 사람 편집은 감사 이력을 남기며 main에 직접 커밋한다.
    """
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes wiki root: {rel}")
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        _git(root, "add", "--", str(p.relative_to(root)))
        # 변경 없음이면 git commit이 실패하므로 diff로 미리 확인 (멱등)
        staged = subprocess.run(
            ["git", "-C", str(root), "diff", "--cached", "--quiet"],
        ).returncode
        if staged == 0:
            return False
        _git(root, "commit", "-m", message)
    return True


def delete_page(root: Path, rel: str, message: str) -> bool:
    """main에서 페이지 하나를 지우고 커밋. 파일이 없으면 False (멱등)."""
    p = (root / rel).resolve()
    if not p.is_relative_to(root.resolve()) or not p.is_file():
        return False
    with _lock(root):
        _git(root, "checkout", "-f", "main")
        if not (root / rel).is_file():
            return False
        _git(root, "rm", "--", rel)
        _git(root, "commit", "-m", message)
    return True


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
                   resolutions: dict[str, str] | None = None,
                   resolve_conflict=None) -> None:
    """ingest 브랜치를 main에 squash 병합한다.

    resolve_conflict(path, base, ours, theirs)->str가 주어지면, 두 문서가 같은 페이지를
    각각 갱신해 진짜 충돌이 난 경우 그 콜백으로 두 판을 하나로 합쳐 자동 해소한다
    (동시 업로드 승인 시 500 대신). 콜백이 없으면 종전대로 사람 해결을 요구하며 예외.
    """
    branch = f"ingest/{source_id}"
    with _lock(root):
        if not _branch_exists(root, branch):
            return  # 이미 병합·삭제된 브랜치 — 재시도 멱등
        _restore_main(root)
        # 3-way 병합으로 공유 페이지 내용은 합치되, 파생 파일 index.md는 병합 대상에서 제외한다.
        # index.md는 PAGE_DIRS에서 재생성되는 파일이라 브랜치가 오래되면 항상 충돌한다 —
        # check=False로 진행시키고 아래서 재생성으로 확정 해소한다.
        subprocess.run(["git", "-C", str(root), "merge", "--squash", branch],
                       capture_output=True, text=True)
        (root / "index.md").write_text(rebuild_index(root), encoding="utf-8")
        _git(root, "add", "--", "index.md")
        # index.md를 제외하고 남은 미해결 충돌 = 진짜 페이지 충돌.
        unmerged = _git(root, "diff", "--name-only", "--diff-filter=U").split()
        if unmerged:
            if resolve_conflict is None:
                _restore_main(root)
                raise RuntimeError(f"위키 병합 충돌(수동 해결 필요): {unmerged}")
            try:
                for path in unmerged:
                    merged = resolve_conflict(
                        path, _show_stage(root, 1, path),
                        _show_stage(root, 2, path), _show_stage(root, 3, path),
                    )
                    (root / path).write_text(merged, encoding="utf-8")
                    _git(root, "add", "--", path)
            except Exception:
                _restore_main(root)  # 해소 실패 시 트리 오염 방지 — approve()가 재시도 가능
                raise
            still = _git(root, "diff", "--name-only", "--diff-filter=U").split()
            if still:
                _restore_main(root)
                raise RuntimeError(f"위키 병합 충돌 자동 해소 실패: {still}")
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


def reset_repo(root: Path) -> None:
    """위키 저장소를 통째로 비우고 초기 상태로 재생성한다. git 이력까지 사라진다 — 되돌릴 수 없다."""
    with _lock(root):  # 인제스트 워커와 직렬화
        for p in root.iterdir():
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        from scripts.init_wiki import init_wiki  # 지연 임포트: 순환 회피 (_ensure_repo와 동일)

        init_wiki(root)
