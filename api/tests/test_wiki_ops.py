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


def test_stage_changes_initializes_missing_repo(tmp_path):
    """신규 배포(위키 볼륨이 빔): init_wiki를 수동 실행하지 않아도 첫 인제스트가 성공해야 한다.
    회귀: git checkout -f main → exit 128로 태스크가 failed 되던 문제."""
    root = tmp_path / "wiki"
    root.mkdir()  # 볼륨 마운트로 디렉토리만 존재, .git 없음
    branch = wiki_ops.stage_changes(root, "s1", {"tech/a.md": "# A"}, "ingest: 첫 문서")
    assert branch == "ingest/s1"
    assert (root / ".git").is_dir()
    assert "tech/a.md" in _git_out(root, "ls-tree", "-r", "--name-only", "ingest/s1")
    assert _git_out(root, "branch", "--show-current").strip() == "main"


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


def test_approve_stale_branch_after_main_advanced(tmp_path):
    """다른 문서가 먼저 승인돼 main이 앞서 나간 뒤, 오래 staged로 있던 브랜치를 승인해도
    파생 파일(index.md) 충돌로 실패하지 않고 페이지가 main에 반영돼야 한다."""
    init_wiki(tmp_path)
    # B: 이른 시점(빈 main)에서 브랜치 생성 — 이후 승인되지 않고 방치
    wiki_ops.stage_changes(tmp_path, "sB", {"tech/b.md": "# B", "summaries/sB.md": "# 요약B"}, "ingest: B")
    # A: 나중에 staged→승인 → main 전진 (index.md 재생성 포함)
    wiki_ops.stage_changes(tmp_path, "sA", {"tech/a.md": "# A"}, "ingest: A")
    wiki_ops.approve_branch(tmp_path, "sA", "approve: A")
    # 이제 sB는 낡은 base(빈 main)에 갇힘 — 승인이 예외 없이 성공해야 한다
    wiki_ops.approve_branch(tmp_path, "sB", "approve: B")
    assert (tmp_path / "tech" / "b.md").read_text(encoding="utf-8") == "# B"
    assert (tmp_path / "tech" / "a.md").read_text(encoding="utf-8") == "# A"  # A 기여분 보존
    idx = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "tech/b" in idx and "tech/a" in idx  # 파생 색인 재구성
    assert "<<<<<<<" not in idx  # 충돌 마커 잔존 금지
    assert "ingest/sB" not in _git_out(tmp_path, "branch", "--list", "ingest/*")


def test_approve_same_page_conflict_auto_resolved(tmp_path):
    """두 문서가 같은 페이지를 각각 갱신 → 순차 승인 시 진짜 병합 충돌.
    resolve_conflict 콜백이 두 판을 합쳐 500 없이 해소하고, 양쪽 기여가 보존돼야 한다."""
    init_wiki(tmp_path)
    # A·B 모두 빈 main 기준으로 tech/shared.md를 각각 다르게 작성 (공통 조상 = 없음)
    wiki_ops.stage_changes(tmp_path, "sA", {"tech/shared.md": "# 공유\nA의 내용"}, "ingest: A")
    wiki_ops.stage_changes(tmp_path, "sB", {"tech/shared.md": "# 공유\nB의 내용"}, "ingest: B")
    wiki_ops.approve_branch(tmp_path, "sA", "approve: A")  # A 먼저 → main에 A판

    calls = []

    def _resolver(path, base, ours, theirs):
        calls.append(path)
        return f"# 공유\n{ours.split(chr(10),1)[1]}\n{theirs.split(chr(10),1)[1]}"  # 두 판 합침

    wiki_ops.approve_branch(tmp_path, "sB", "approve: B", resolve_conflict=_resolver)
    merged = (tmp_path / "tech" / "shared.md").read_text(encoding="utf-8")
    assert calls == ["tech/shared.md"]          # 충돌 페이지에 대해 콜백 호출됨
    assert "A의 내용" in merged and "B의 내용" in merged  # 양쪽 보존
    assert "<<<<<<<" not in merged              # 충돌 마커 잔존 금지
    assert "ingest/sB" not in _git_out(tmp_path, "branch", "--list", "ingest/*")
    assert "approve: B" in _git_out(tmp_path, "log", "--oneline", "main")


def test_approve_conflict_without_resolver_still_raises(tmp_path):
    """콜백 미제공 시 종전 동작 유지 — 사람 해결 요구하며 예외, 트리는 깨끗한 main."""
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "sA", {"tech/s.md": "A"}, "ingest: A")
    wiki_ops.stage_changes(tmp_path, "sB", {"tech/s.md": "B"}, "ingest: B")
    wiki_ops.approve_branch(tmp_path, "sA", "approve: A")
    with pytest.raises(RuntimeError, match="병합 충돌"):
        wiki_ops.approve_branch(tmp_path, "sB", "approve: B")
    assert _git_out(tmp_path, "status", "--porcelain") == ""  # 트리 오염 없음
    assert _git_out(tmp_path, "branch", "--show-current").strip() == "main"


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


def test_write_page_commits_on_main(tmp_path):
    init_wiki(tmp_path)
    assert wiki_ops.write_page(tmp_path, "tech/edit.md", "# 편집\n본문", "edit: tech/edit.md")
    assert _git_out(tmp_path, "branch", "--show-current").strip() == "main"
    assert "tech/edit.md" in _git_out(tmp_path, "ls-tree", "-r", "--name-only", "main")
    assert _git_out(tmp_path, "show", "main:tech/edit.md") == "# 편집\n본문"
    assert "edit: tech/edit.md" in _git_out(tmp_path, "log", "--oneline", "main")


def test_write_page_noop_when_unchanged(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.write_page(tmp_path, "tech/x.md", "동일", "m1")
    assert wiki_ops.write_page(tmp_path, "tech/x.md", "동일", "m2") is False  # 변경 없음 → 커밋 생략


def test_write_page_rejects_path_escape(tmp_path):
    init_wiki(tmp_path)
    with pytest.raises(ValueError, match="escapes"):
        wiki_ops.write_page(tmp_path, "../evil.md", "x", "m")


def test_delete_page_removes_and_is_idempotent(tmp_path):
    init_wiki(tmp_path)
    wiki_ops.write_page(tmp_path, "summaries/s1.md", "# 요약", "m")
    assert wiki_ops.delete_page(tmp_path, "summaries/s1.md", "delete: s1") is True
    assert not (tmp_path / "summaries" / "s1.md").exists()
    assert "summaries/s1.md" not in _git_out(tmp_path, "ls-tree", "-r", "--name-only", "main")
    assert wiki_ops.delete_page(tmp_path, "summaries/s1.md", "delete: s1") is False  # 멱등


def test_reset_repo_wipes_history_and_reinitializes(tmp_path):
    """전체 초기화: 페이지·브랜치·git 이력이 사라지고 초기 상태로 재생성된다."""
    init_wiki(tmp_path)
    wiki_ops.stage_changes(tmp_path, "s30", {"tech/x.md": "# X"}, "ingest: 문서")
    wiki_ops.approve_branch(tmp_path, "s30", "approve: 문서")
    assert (tmp_path / "tech" / "x.md").exists()

    wiki_ops.reset_repo(tmp_path)

    assert not (tmp_path / "tech" / "x.md").exists()          # 페이지 삭제
    assert (tmp_path / "index.md").is_file()                  # 초기 구조 재생성
    assert (tmp_path / "schema.md").is_file()
    assert _git_out(tmp_path, "branch", "--list", "ingest/*") == ""   # 브랜치 없음
    log = _git_out(tmp_path, "log", "--oneline", "main").strip().splitlines()
    assert len(log) == 1 and "위키 저장소 초기화" in log[0]   # 이력은 초기 커밋 하나뿐
