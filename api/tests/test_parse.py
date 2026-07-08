import datetime as dt
import json

import pytest
from openpyxl import Workbook

from pipeline.parse import chunk_markdown, parse_md, parse_xlsx, run_pipeline


def _xlsx(path, sheets: dict[str, list[list]]):
    """{시트명: [행, ...]} → xlsx 파일. 첫 행이 헤더."""
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    wb.save(path)


def test_chunk_markdown_splits_on_headings():
    md = "머리말\n# 1장\n가나다\n## 1.1\n라마바\n# 2장\n사아자"
    secs = chunk_markdown(md)
    assert secs[0] == "머리말"
    assert secs[1].startswith("# 1장")
    assert any("## 1.1" in s for s in secs)
    assert secs[-1].startswith("# 2장")
    assert "" not in secs  # 빈 섹션 제거


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


def _parse(tmp_path, sheets) -> tuple[list[dict], list[dict]]:
    src = tmp_path / "original.xlsx"
    _xlsx(src, sheets)
    out = tmp_path / "parsed"
    out.mkdir()
    parse_xlsx(src, out)
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    tables = [json.loads((out / c["ref"]).read_text(encoding="utf-8")) for c in chunks]
    return chunks, tables


def test_parse_xlsx_sheets_to_tables(tmp_path):
    chunks, [t] = _parse(tmp_path, {"사업목록": [["사업명", "예산"], ["A사업", 100]]})
    assert t == {"table_title": "사업목록", "columns": ["사업명", "예산"], "rows": [["A사업", 100]]}
    assert chunks == [{"id": "c001", "type": "table", "page": None, "ref": "tables/table_001.json"}]


def test_parse_xlsx_serializes_dates(tmp_path):
    _, [t] = _parse(tmp_path, {"일정": [["사업명", "시작일"], ["A사업", dt.datetime(2026, 1, 1)]]})
    assert t["rows"] == [["A사업", "2026-01-01 00:00:00"]]


def test_parse_xlsx_multiple_sheets(tmp_path):
    chunks, tables = _parse(tmp_path, {"가": [["a"], [1]], "나": [["b"], [2]], "빈": []})
    assert [c["ref"] for c in chunks] == [f"tables/table_{i:03d}.json" for i in (1, 2, 3)]
    assert [t["table_title"] for t in tables] == ["가", "나", "빈"]
    assert tables[2] == {"table_title": "빈", "columns": [], "rows": []}


def test_parse_xlsx_dedupes_and_names_header_cells(tmp_path):
    """중복 컬럼명은 .1 접미로 구분한다 — 안 하면 LLM 표 매핑이 두 열을 구분 못 한다.
    빈 헤더 칸은 Unnamed: N (pandas read_excel과 동일 규약)."""
    _, [t] = _parse(tmp_path, {"S": [["예산", "예산", None, "예산"], [1, 2, 3, 4]]})
    assert t["columns"] == ["예산", "예산.1", "Unnamed: 2", "예산.2"]


def test_parse_xlsx_drops_blank_rows_and_keeps_ints(tmp_path):
    """공백 행은 버리고, 빈칸 섞인 정수 열도 float로 승격시키지 않는다 (pandas 대비 개선)."""
    _, [t] = _parse(tmp_path, {"S": [["명", "예산"], ["A", 100], [None, None], ["B", None]]})
    assert t["rows"] == [["A", 100], ["B", None]]


def test_parse_xlsx_pads_short_rows_to_header_width(tmp_path):
    _, [t] = _parse(tmp_path, {"S": [["a", "b", "c"], [1]]})
    assert t["rows"] == [[1, None, None]]


def test_run_pipeline_dispatches_md(tmp_path):
    (tmp_path / "original.md").write_text("# 하나", encoding="utf-8")
    run_pipeline(tmp_path)
    assert (tmp_path / "parsed" / "chunks.json").exists()


def test_run_pipeline_rejects_unknown_ext(tmp_path):
    (tmp_path / "original.hwp").write_bytes(b"x")
    with pytest.raises(ValueError, match="unsupported"):
        run_pipeline(tmp_path)
