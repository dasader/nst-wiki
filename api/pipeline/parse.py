"""Stage 0 포맷 분기 + MD/XLSX 파싱. PDF는 parse_pdf 모듈(도클링)로 위임."""
import json
from pathlib import Path


def run_pipeline(source_dir: Path) -> None:
    original = next(source_dir.glob("original.*"), None)
    if original is None:
        raise ValueError(f"no original.* file in {source_dir}")
    out = source_dir / "parsed"
    out.mkdir(exist_ok=True)
    ext = original.suffix.lower()
    if ext == ".pdf":
        from pipeline.parse_pdf import parse_pdf  # docling 임포트는 무거워서 지연

        parse_pdf(original, out)
    elif ext == ".md":
        parse_md(original, out)
    elif ext == ".xlsx":
        parse_xlsx(original, out)
    else:
        raise ValueError(f"unsupported format: {ext}")


def _write_chunks(out: Path, chunks: list[dict]) -> None:
    (out / "chunks.json").write_text(
        json.dumps(chunks, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_md(src: Path, out: Path) -> None:
    text = src.read_text(encoding="utf-8")
    (out / "document.md").write_text(text, encoding="utf-8")
    sections, cur = [], []
    for line in text.splitlines():
        if line.startswith("#") and cur:
            sections.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    chunks = [
        {"id": f"c{i:03d}", "type": "text", "page": None, "text": s}
        for i, s in enumerate((s for s in sections if s), 1)
    ]
    _write_chunks(out, chunks)


def parse_xlsx(src: Path, out: Path) -> None:
    import pandas as pd

    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)
    chunks = []
    for i, (sheet, df) in enumerate(pd.read_excel(src, sheet_name=None).items(), 1):
        ref = f"tables/table_{i:03d}.json"
        payload = {
            "table_title": str(sheet),
            "columns": [str(c) for c in df.columns],
            "rows": df.astype(object).where(df.notna(), None).values.tolist(),
        }
        (out / ref).write_text(
            json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
        )
        chunks.append({"id": f"c{i:03d}", "type": "table", "page": None, "ref": ref})
    _write_chunks(out, chunks)
