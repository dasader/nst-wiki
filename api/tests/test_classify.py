import json

import pipeline.classify as classify


def _write_chunks(parsed_dir, chunks):
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (parsed_dir / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False), encoding="utf-8"
    )


def test_classify_routes_types_and_llm_narrative(tmp_path, monkeypatch):
    _write_chunks(tmp_path, [
        {"id": "c001", "type": "text", "page": 1, "text": "정책 배경 설명"},
        {"id": "c002", "type": "text", "page": 1, "text": "목차"},
        {"id": "c003", "type": "table", "page": 2, "ref": "tables/table_001.json"},
        {"id": "c004", "type": "picture", "page": 3, "ref": "figures/fig_001.png"},
    ])
    monkeypatch.setattr(classify.llm, "generate", lambda *a, **k: {
        "classifications": [
            {"id": "c001", "category": "NARRATIVE"},
            {"id": "c002", "category": "METADATA"},
        ]
    })
    result = classify.classify_chunks(tmp_path)
    assert result["narrative_ids"] == ["c001"]
    assert result["table_ids"] == ["c003"]
    assert result["picture_ids"] == ["c004"]


def test_classify_no_text_chunks_skips_llm(tmp_path, monkeypatch):
    _write_chunks(tmp_path, [{"id": "c001", "type": "table", "page": None, "ref": "tables/table_001.json"}])
    def boom(*a, **k):
        raise AssertionError("LLM 호출되면 안 됨")
    monkeypatch.setattr(classify.llm, "generate", boom)
    result = classify.classify_chunks(tmp_path)
    assert result == {"narrative_ids": [], "table_ids": ["c001"], "picture_ids": []}
