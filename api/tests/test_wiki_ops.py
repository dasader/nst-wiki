import subprocess

import pytest

from scripts.init_wiki import init_wiki
import wiki_ops


def _git_out(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, check=True).stdout


def test_stage_changes_creates_branch_and_returns_to_main(tmp_path):
    init_wiki(tmp_path)
    branch = wiki_ops.stage_changes(
        tmp_path, "abc123",
        {"tech/hbm.md": "# HBM\n내용", "summaries/abc123.md": "# 요약"},
        "ingest: 테스트 소스",
    )
    assert branch == "ingest/abc123"
    assert _git_out(tmp_path, "branch", "--show-current").strip() == "main"
    assert "ingest/abc123" in _git_out(tmp_path, "branch", "--list", "ingest/*")
    files = _git_out(tmp_path, "ls-tree", "-r", "--name-only", "ingest/abc123")
    assert "tech/hbm.md" in files and "summaries/abc123.md" in files
    assert "tech/hbm.md" not in _git_out(tmp_path, "ls-tree", "-r", "--name-only", "main")


def test_stage_changes_is_rerunnable(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s1", {"tech/a.md": "v1"}, "m1")
    wiki_ops.stage_changes(tmp_path, "s1", {"tech/a.md": "v2"}, "m2")  # -B로 재생성
    content = _git_out(tmp_path, "show", "ingest/s1:tech/a.md")
    assert content == "v2"


def test_list_and_read_pages(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s2", {"tech/b.md": "# B"}, "m")
    assert "tech/b.md" not in wiki_ops.list_pages(tmp_path)  # main 기준
    assert wiki_ops.read_page(tmp_path, "tech/없는페이지.md") is None


def test_stage_changes_commits_only_intended_files(tmp_path):
    wiki = tmp_path / "wiki"
    init_wiki(wiki)
    wiki_ops.stage_changes(wiki, "s9", {"tech/c.md": "# C"}, "m")
    tree = _git_out(wiki, "ls-tree", "-r", "--name-only", "ingest/s9")
    assert ".ingest.lock" not in tree
    assert (tmp_path / "wiki.ingest.lock").exists()  # 사이드카 위치 확인


def test_stage_changes_rejects_path_escape(tmp_path):
    init_wiki(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        wiki_ops.stage_changes(tmp_path, "s10", {"../evil.md": "x"}, "m")
    assert _git_out(tmp_path, "branch", "--show-current").strip() == "main"


def test_rebuild_index_lists_pages(tmp_path):
    init_wiki(tmp_path)
    (tmp_path / "tech" / "hbm.md").write_text("x", encoding="utf-8")
    idx = wiki_ops.rebuild_index(tmp_path)
    assert "tech/hbm" in idx
