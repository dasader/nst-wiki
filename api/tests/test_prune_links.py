"""일회성 링크 정리 스크립트 — 대상만 고치고, 두 번 돌려도 커밋을 늘리지 않는다."""
import subprocess

from scripts.init_wiki import init_wiki
from scripts.prune_links import prune_committed_pages


def _commits(root) -> int:
    out = subprocess.run(["git", "-C", str(root), "rev-list", "--count", "main"],
                         capture_output=True, text=True, check=True)
    return int(out.stdout)


def _seed(tmp_path):
    root = tmp_path / "wiki"
    init_wiki(root)
    (root / "tech" / "a.md").write_text(
        "본문 [[tech/b]] 참조와 [[laws/없는법]] 링크.\n"
        "주요국은 [[data:전략기술?범위=10~20]]개 내외.\n"
        "실존: [[data:technologies?field=양자]]\n", encoding="utf-8")
    (root / "tech" / "b.md").write_text("깨끗한 페이지. [[tech/a]]\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-m", "seed"], check=True,
                   capture_output=True)
    return root


def test_prunes_dead_links_and_is_idempotent(tmp_path):
    root = _seed(tmp_path)
    base = _commits(root)

    assert prune_committed_pages(root) == ["tech/a.md"]
    md = (root / "tech" / "a.md").read_text(encoding="utf-8")
    assert "[[tech/b]]" in md                      # 실존 페이지 링크는 유지
    assert "[[laws/없는법]]" not in md and "laws/없는법" in md   # 평문화
    assert "주요국은 10~20개 내외" in md            # 없는 테이블 → 조건 값만 남김
    assert "[[data:technologies?field=양자]]" in md  # 실존·적재되는 테이블은 유지
    assert _commits(root) == base + 1

    assert prune_committed_pages(root) == []      # 재실행: 변경 없음 → 커밋 없음
    assert _commits(root) == base + 1


def test_leaves_schema_md_and_index_alone(tmp_path):
    """schema.md는 [[data:...]] 문법을 예시로 설명하는 규칙서 — 정리 대상이 아니다."""
    root = _seed(tmp_path)
    before = (root / "schema.md").read_text(encoding="utf-8")
    prune_committed_pages(root)
    assert (root / "schema.md").read_text(encoding="utf-8") == before


def test_dry_run_reports_without_committing(tmp_path):
    root = _seed(tmp_path)
    base = _commits(root)
    assert prune_committed_pages(root, dry_run=True) == ["tech/a.md"]
    assert _commits(root) == base
    assert "[[laws/없는법]]" in (root / "tech" / "a.md").read_text(encoding="utf-8")
