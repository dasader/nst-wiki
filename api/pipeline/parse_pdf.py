"""Gemini 네이티브 PDF 파싱. 2회 호출: 마크다운 + 구조화 표.
두 호출 모두 PDF Part를 맨 앞에 둬 암묵적 캐싱을 활성화한다."""
import json
import logging
from pathlib import Path

import llm
from pipeline.parse import chunk_markdown

log = logging.getLogger(__name__)

MD_PROMPT = (
    "이 정부 정책 PDF 문서 전체를 구조 보존 마크다운으로 변환하라.\n"
    "- 본문 내용을 생략하지 말 것(쪽번호·머리말 반복만 생략).\n"
    "- 표는 전체 그리드 대신 `[표: 간결한제목]` 한 줄 플레이스홀더로만 표기.\n"
    "- 그림·차트·조직도는 `[그림: 한 줄 요약]`으로 본문 흐름에 인라인. 차트 수치는 추정임을 명시.\n"
    "- 숫자·연월일·부처명·연락처는 원문 그대로."
)

TABLES_PROMPT = (
    "이 정부 정책 PDF의 모든 표를 구조화해 추출하라.\n"
    "- table_title: 표 제목(없으면 인접 맥락으로 간결히). page: 쪽번호.\n"
    "- columns: 헤더 행. 없으면 성격에 맞게 명명.\n"
    "- rows: 각 행의 cells(열 순서대로). 병합셀 값 채움. 목차·점선 표 제외.\n"
    "- 숫자·연월일·부처명은 원문 그대로. 셀 개수는 columns 길이에 맞춰라."
)

TABLE_SCHEMA = {
    "type": "object",
    "properties": {"tables": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "table_title": {"type": "string"},
            "page": {"type": "integer"},
            "columns": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": {
                "type": "object",
                "properties": {"cells": {"type": "array", "items": {"type": "string"}}},
                "required": ["cells"]}},
        },
        "required": ["table_title", "columns", "rows"]}}},
    "required": ["tables"],
}


def _fit_cells(rows: list, n: int, title: str = "") -> list:
    """LLM 셀 개수 오차 방어: 부족분 '' 패딩, 초과분 절단. (스파이크선 0건이나 방어적)
    초과 절단은 값 손실이라 조용히 넘기지 않고 경고로 남긴다 (no silent caps)."""
    dropped = sum(len(r) - n for r in rows if len(r) > n)
    if dropped:
        log.warning("표 '%s': columns=%d 초과 셀 %d개 절단됨", title, n, dropped)
    return [(r + [""] * n)[:n] for r in rows]


def parse_pdf(src: Path, out: Path) -> None:
    pdf = llm.pdf_part(src)  # 맨 앞 배치 → 암묵적 캐시

    # Call 1: 마크다운 (그림 인라인, 표 플레이스홀더)
    md = llm.generate("parse_markdown", [pdf, MD_PROMPT])
    if not md or not md.strip():
        raise ValueError(f"빈 마크다운 파싱 결과: {src}")
    (out / "document.md").write_text(md, encoding="utf-8")

    # Call 2: 구조화 표 → Docling과 동일한 tables/*.json 스키마
    data = llm.generate("parse_tables", [pdf, TABLES_PROMPT], schema=TABLE_SCHEMA)
    tables_dir = out / "tables"
    tables_dir.mkdir(exist_ok=True)
    table_chunks = []
    for i, t in enumerate(data.get("tables", []), 1):
        cols = t["columns"]
        rows = _fit_cells([r["cells"] for r in t["rows"]], len(cols), t.get("table_title", ""))
        ref = f"tables/table_{i:03d}.json"
        (out / ref).write_text(
            json.dumps({"table_title": t.get("table_title", ""),
                        "columns": cols, "rows": rows}, ensure_ascii=False),
            encoding="utf-8")
        table_chunks.append({"id": f"t{i:03d}", "type": "table",
                             "page": t.get("page"), "ref": ref})

    # chunks.json: 텍스트 청크(플레이스홀더 보존) + 표 청크
    text_chunks = [
        {"id": f"c{i:03d}", "type": "text", "page": None, "text": s}
        for i, s in enumerate(chunk_markdown(md), 1)
    ]
    (out / "chunks.json").write_text(
        json.dumps(text_chunks + table_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8")
