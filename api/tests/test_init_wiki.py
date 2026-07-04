import subprocess

from scripts.init_wiki import DIRS, init_wiki


def test_init_creates_structure_and_git(tmp_path):
    assert init_wiki(tmp_path) is True
    for d in DIRS:
        assert (tmp_path / d).is_dir()
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "schema.md").exists()
    assert (tmp_path / "contradictions" / "log.md").exists()
    log = subprocess.run(
        ["git", "-C", str(tmp_path), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    )
    assert "위키 저장소 초기화" in log.stdout
    branch = subprocess.run(
        ["git", "-C", str(tmp_path), "branch", "--show-current"],
        capture_output=True, text=True, check=True,
    )
    assert branch.stdout.strip() == "main"


def test_init_is_idempotent(tmp_path):
    init_wiki(tmp_path)
    assert init_wiki(tmp_path) is False
