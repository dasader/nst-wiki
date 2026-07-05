from pathlib import Path

from scripts.init_wiki import init_wiki
import pipeline.narrative as narrative


def _fake_llm(plan_pages, merged):
    def fake(purpose, contents, schema=None):
        if purpose == "plan_pages":
            return {"pages": plan_pages}
        if purpose == "merge_page":
            return merged
        raise AssertionError(f"unexpected purpose: {purpose}")
    return fake


def test_compile_narrative_builds_files(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/hbm-semiconductor.md", "action": "create", "title": "HBM 반도체"}],
        merged={"content": "---\ntitle: HBM 반도체\n---\n\n본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src1", {"title": "정책문서"}, ["HBM 설명 서사"])
    assert out["files"]["tech/hbm-semiconductor.md"].endswith("본문")
    assert "summaries/src1.md" in out["files"]
    assert out["affected_pages"] == [{"path": "tech/hbm-semiconductor.md", "action": "create"}]
    assert out["contradictions"] == []


def test_compile_narrative_records_contradictions(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "update", "title": "A"}],
        merged={"content": "새 본문", "contradictions": [
            {"summary": "분야 수 불일치", "existing": "12개", "new": "10개"}
        ]},
    ))
    out = narrative.compile_narrative(tmp_path, "src2", {"title": "문서"}, ["서사"])
    assert len(out["contradictions"]) == 1
    assert "분야 수 불일치" in out["files"]["contradictions/log.md"]


def test_contradiction_lands_in_page_frontmatter(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "update", "title": "A"}],
        merged={"content": "---\ntitle: A\n---\n\n본문", "contradictions": [
            {"summary": "분야 수 불일치", "existing": "12개", "new": "10개"}
        ]},
    ))
    out = narrative.compile_narrative(tmp_path, "src5", {"title": "문서"}, ["서사"])
    page = out["files"]["tech/a.md"]
    assert "unresolved_contradictions:" in page.split("---")[1]  # 프론트매터 안
    assert "분야 수 불일치" in page
    assert page.endswith("본문")  # 본문 보존


def test_contradiction_frontmatter_idempotent(tmp_path):
    once = narrative._inject_contradictions(
        "---\ntitle: A\n---\n\n본문", [{"summary": "s", "existing": "e", "new": "n"}])
    twice = narrative._inject_contradictions(
        once, [{"summary": "s", "existing": "e", "new": "n"}])
    assert once.count("unresolved_contradictions:") == 1
    assert twice.count("unresolved_contradictions:") == 1
    assert twice.count('- "s') == 1  # 재병합해도 중복 안 됨


def test_compile_narrative_rejects_bad_paths(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[
            {"path": "../escape.md", "action": "create", "title": "X"},
            {"path": "unknown/a.md", "action": "create", "title": "Y"},
            {"path": "tech/ok.md", "action": "create", "title": "OK"},
        ],
        merged={"content": "본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src4", {"title": "문서"}, ["서사"])
    assert [p["action"] for p in out["affected_pages"]] == ["rejected", "rejected", "create"]
    assert list(f for f in out["files"] if f.endswith("escape.md")) == []


def test_compile_narrative_caps_at_15(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    pages = [{"path": f"tech/t{i}.md", "action": "create", "title": f"T{i}"} for i in range(20)]
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=pages, merged={"content": "본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src3", {"title": "문서"}, ["서사"])
    updated = [p for p in out["affected_pages"] if p["action"] != "suggested"]
    suggested = [p for p in out["affected_pages"] if p["action"] == "suggested"]
    assert len(updated) == 15
    assert len(suggested) == 5
    assert sum(1 for f in out["files"] if f.startswith("tech/")) == 15
