import json
import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from scripts.init_wiki import init_wiki
import pipeline.compile as compile_mod


def test_compile_source_full_flow(tmp_path, monkeypatch):
    wiki = tmp_path / "wiki"
    init_wiki(wiki)
    src = tmp_path / "src"
    parsed = src / "parsed"
    parsed.mkdir(parents=True)
    (src / "metadata.json").write_text(json.dumps({"title": "테스트 문서"}), encoding="utf-8")
    (parsed / "chunks.json").write_text(json.dumps([
        {"id": "c001", "type": "text", "page": 1, "text": "정책 서사"},
    ], ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(compile_mod.classify, "classify_chunks",
                        lambda p: {"narrative_ids": ["c001"], "table_ids": [], "picture_ids": []})
    monkeypatch.setattr(compile_mod.describe, "describe_figures", lambda p, t: [])
    monkeypatch.setattr(compile_mod.map_tables, "map_and_stage_tables",
                        lambda p, s: {"staged": [], "needs_review": 0})
    monkeypatch.setattr(compile_mod.narrative, "compile_narrative",
                        lambda root, sid, meta, texts: {
                            "files": {"tech/a.md": "본문"},
                            "affected_pages": [{"path": "tech/a.md", "action": "create"}],
                            "contradictions": [],
                        })
    source_id = str(uuid.uuid4())
    out = compile_mod.compile_source(src, source_id, wiki)
    assert out["branch"] == f"ingest/{source_id}"
    assert out["affected_pages"] == [{"path": "tech/a.md", "action": "create"}]
    assert out["affected_tables"] == {"staged": [], "needs_review": 0}
