import os
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


def test_stage_changes_recovers_from_dirty_tree(tmp_path):
    init_wiki(tmp_path)
    # 이전 크래시가 남긴 미커밋 잔재를 흉내
    (tmp_path / "tech" / "leftover.md").write_text("찌꺼기", encoding="utf-8")
    wiki_ops.stage_changes(tmp_path, "s11", {"tech/d.md": "# D"}, "m")
    tree = _git_out(tmp_path, "ls-tree", "-r", "--name-only", "ingest/s11")
    assert "tech/leftover.md" not in tree
    assert not (tmp_path / "tech" / "leftover.md").exists()


def test_diff_approve_flow(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s20", {"tech/x.md": "# X\n본문"}, "ingest: 문서")
    diff = wiki_ops.diff_branch(tmp_path, "s20")
    assert "tech/x.md" in diff and "+# X" in diff
    wiki_ops.approve_branch(tmp_path, "s20", "approve: 문서")
    assert (tmp_path / "tech" / "x.md").read_text(encoding="utf-8").startswith("# X")
    assert "ingest/s20" not in _git_out(tmp_path, "branch", "--list", "ingest/*")
    log = _git_out(tmp_path, "log", "--oneline", "main")
    assert "approve: 문서" in log


def test_approve_applies_resolutions(tmp_path):
    init_wiki(tmp_path)
    log_row = "| s21-1 | 2026-07-04 | tech/a.md | 요약 | 기존 | 신규 | 미해결 |\n"
    log_content = (tmp_path / "contradictions" / "log.md").read_text(encoding="utf-8") + log_row
    wiki_ops.stage_changes(tmp_path, "s21",
                           {"tech/a.md": "# A", "contradictions/log.md": log_content}, "m")
    wiki_ops.approve_branch(tmp_path, "s21", "approve: m", resolutions={"s21-1": "replace"})
    merged_log = (tmp_path / "contradictions" / "log.md").read_text(encoding="utf-8")
    assert "| s21-1 |" in merged_log
    assert "해결(replace)" in merged_log
    assert "| 미해결 |" not in merged_log.split("s21-1")[1].split("\n")[0]


def test_reject_deletes_branch(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s22", {"tech/y.md": "# Y"}, "m")
    wiki_ops.reject_branch(tmp_path, "s22")
    assert "ingest/s22" not in _git_out(tmp_path, "branch", "--list", "ingest/*")
    assert not (tmp_path / "tech" / "y.md").exists()
    wiki_ops.reject_branch(tmp_path, "s22")  # 멱등: 없는 브랜치도 에러 없이


def test_approve_branch_idempotent_when_branch_missing(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.approve_branch(tmp_path, "sX", "approve: 없음")  # 브랜치 없음 — 예외 없이 리턴


def test_read_page_asof(tmp_path):
    init_wiki(tmp_path)
    rel = "tech/asof.md"

    def _commit(content, date):
        (tmp_path / rel).write_text(content, encoding="utf-8")
        env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(tmp_path), "commit", "-m", "c"],
                       env=env, check=True, capture_output=True)

    _commit("v1", "2026-01-01T00:00:00")
    _commit("v2", "2026-06-01T00:00:00")
    assert wiki_ops.read_page_asof(tmp_path, rel, "2026-03-01") == "v1"   # v1과 v2 사이
    assert wiki_ops.read_page_asof(tmp_path, rel, "2026-07-01") == "v2"   # v2 이후
    assert wiki_ops.read_page_asof(tmp_path, rel, "2025-01-01") is None   # 생성 전
