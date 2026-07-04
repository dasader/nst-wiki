import json

import pandas as pd
import pytest

from pipeline.parse import parse_md, parse_xlsx, run_pipeline


def test_parse_md_splits_by_heading(tmp_path):
    src = tmp_path / "original.md"
    src.write_text("# 제목\n\n서론.\n\n## 배경\n\n본문.", encoding="utf-8")
    out = tmp_path / "parsed"
    out.mkdir()
    parse_md(src, out)
    assert (out / "document.md").read_text(encoding="utf-8").startswith("# 제목")
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    assert [c["type"] for c in chunks] == ["text", "text"]
    assert chunks[0]["id"] == "c001"
    assert "배경" in chunks[1]["text"]


def test_parse_xlsx_sheets_to_tables(tmp_path):
    src = tmp_path / "original.xlsx"
    pd.DataFrame({"사업명": ["A사업"], "예산": [100]}).to_excel(
        src, index=False, sheet_name="사업목록"
    )
    out = tmp_path / "parsed"
    out.mkdir()
    parse_xlsx(src, out)
    data = json.loads((out / "tables" / "table_001.json").read_text(encoding="utf-8"))
    assert data["table_title"] == "사업목록"
    assert data["columns"] == ["사업명", "예산"]
    assert data["rows"] == [["A사업", 100]]
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    assert chunks == [{"id": "c001", "type": "table", "page": None, "ref": "tables/table_001.json"}]


def test_run_pipeline_dispatches_md(tmp_path):
    (tmp_path / "original.md").write_text("# 하나", encoding="utf-8")
    run_pipeline(tmp_path)
    assert (tmp_path / "parsed" / "chunks.json").exists()


def test_run_pipeline_rejects_unknown_ext(tmp_path):
    (tmp_path / "original.hwp").write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        run_pipeline(tmp_path)
