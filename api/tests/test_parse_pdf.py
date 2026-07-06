import json
from pathlib import Path

import pipeline.parse_pdf as ppdf


def _fake_generate(purpose, contents, schema=None):
    # contents[0]은 PDF Part여야 함(캐시 조건) — 타입 존재만 확인
    assert contents and hasattr(contents[0], "inline_data")
    if purpose == "parse_markdown":
        return "# 제목\n본문 [표: 예산] 과 [그림: 조직도] 포함\n## 2절\n내용 문단"
    return {"tables": [{
        "table_title": "예산", "page": 3,
        "columns": ["항목", "금액"],
        "rows": [{"cells": ["연구", "100"]}, {"cells": ["운영"]},        # 셀 부족 행
                 {"cells": ["관리", "50", "초과"]}],                    # 셀 초과 행
    }]}


def test_parse_pdf_produces_contract(tmp_path, monkeypatch):
    monkeypatch.setattr(ppdf.llm, "generate", _fake_generate)
    src = tmp_path / "original.pdf"
    src.write_bytes(b"%PDF-1.4")
    out = tmp_path / "parsed"
    out.mkdir()
    ppdf.parse_pdf(src, out)

    # 표 파일: Docling과 동일 스키마 + 셀 정합 가드(부족분 '' 패딩)
    t = json.loads((out / "tables" / "table_001.json").read_text(encoding="utf-8"))
    assert t == {"table_title": "예산", "columns": ["항목", "금액"],
                 "rows": [["연구", "100"], ["운영", ""],   # 부족분 '' 패딩
                          ["관리", "50"]]}                 # 초과분 절단

    # chunks.json: 텍스트 청크(플레이스홀더 보존) + 표 청크
    chunks = json.loads((out / "chunks.json").read_text(encoding="utf-8"))
    texts = [c for c in chunks if c["type"] == "text"]
    tables = [c for c in chunks if c["type"] == "table"]
    assert any("[표: 예산]" in c["text"] for c in texts)
    assert any("[그림: 조직도]" in c["text"] for c in texts)
    assert tables == [{"id": "t001", "type": "table", "page": 3,
                       "ref": "tables/table_001.json"}]
    assert (out / "document.md").read_text(encoding="utf-8").startswith("# 제목")


def test_parse_pdf_rejects_empty_markdown(tmp_path, monkeypatch):
    monkeypatch.setattr(ppdf.llm, "generate",
                        lambda p, c, schema=None: "" if p == "parse_markdown" else {"tables": []})
    src = tmp_path / "original.pdf"; src.write_bytes(b"%PDF")
    out = tmp_path / "parsed"; out.mkdir()
    import pytest
    with pytest.raises(ValueError, match="빈 마크다운"):
        ppdf.parse_pdf(src, out)
