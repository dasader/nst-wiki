"""Stage 2~5 오케스트레이션: 분류 → 그림 설명 → 표 매핑 → 서사 → 위키 브랜치 커밋."""
import json
from pathlib import Path

from pipeline import classify, describe, map_tables, narrative
import wiki_ops


def compile_source(source_dir: Path, source_id: str, wiki_root: Path) -> dict:
    meta = json.loads((source_dir / "metadata.json").read_text(encoding="utf-8"))
    parsed = source_dir / "parsed"
    cls = classify.classify_chunks(parsed)
    chunks = {c["id"]: c for c in json.loads((parsed / "chunks.json").read_text(encoding="utf-8"))}
    texts = [chunks[i]["text"] for i in cls["narrative_ids"] if i in chunks]
    texts += [f"[그림 {d['figure']}] {d['text']}"
              for d in describe.describe_figures(parsed, meta.get("title", ""))]
    affected_tables = map_tables.map_and_stage_tables(parsed, source_id)
    branch = None
    affected_pages, contradictions = [], []
    if texts:
        nar = narrative.compile_narrative(wiki_root, source_id, meta, texts)
        branch = wiki_ops.stage_changes(
            wiki_root, source_id, nar["files"],
            f"ingest: {meta.get('title', source_id)} (source: {source_id})",
        )
        affected_pages, contradictions = nar["affected_pages"], nar["contradictions"]
    return {"branch": branch, "affected_pages": affected_pages,
            "affected_tables": affected_tables, "contradictions": contradictions}
