"""정형 표 경로: 고정 스키마 매핑(LLM) → staging 적재. LLM은 DDL을 만들지 않는다."""
import json
from pathlib import Path

import llm
from app import db

CONFIDENCE_THRESHOLD = 0.8

# LLM 직접 매핑 대상과 허용 컬럼 (FK 연결이 필요한 테이블은 제외 — staging_tables로)
CORE_TABLES = {
    "technologies": ["name", "field", "sub_field", "lead_ministry", "trl_level", "description"],
    "projects": ["project_code", "name", "lead_ministry", "budget_total", "budget_annual",
                 "start_year", "end_year", "status"],
    "policy_events": ["event_date", "event_type", "title", "description"],
    "ministries": ["name", "abbreviation"],
}
INT_COLS = {"trl_level", "budget_total", "budget_annual", "start_year", "end_year"}

MAP_SCHEMA = {
    "type": "object",
    "properties": {
        "table": {"type": "string", "enum": list(CORE_TABLES) + ["none"]},
        "confidence": {"type": "number"},
        "column_mapping": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["table", "confidence", "column_mapping"],
}

PROMPT = """한국 정책문서에서 추출한 표를 DB 스키마에 매핑하라.

대상 테이블과 컬럼:
{schema_desc}

표 제목: {title}
표 컬럼: {columns}
샘플 행 (최대 5개): {sample}

이 표가 위 테이블 중 하나에 대응하면 table에 테이블명, column_mapping에 {{"표 컬럼명": "DB 컬럼명"}}을,
대응하지 않으면 table에 "none"을 반환하라. confidence는 매핑 확신도(0~1)."""


def _coerce(col: str, val):
    if val is None or val == "":
        return None
    if col in INT_COLS:
        try:
            return int(float(str(val).replace(",", "")))
        except ValueError:
            return None
    return str(val)


def map_and_stage_tables(parsed_dir: Path, source_id: str) -> dict:
    tables_dir = parsed_dir / "tables"
    result = {"staged": [], "needs_review": 0}
    if not tables_dir.is_dir():
        return result
    schema_desc = "\n".join(f"- {t}: {', '.join(cols)}" for t, cols in CORE_TABLES.items())
    for tf in sorted(tables_dir.glob("table_*.json")):
        payload = json.loads(tf.read_text(encoding="utf-8"))
        try:
            out = llm.generate("map_table", PROMPT.format(
                schema_desc=schema_desc,
                title=payload.get("table_title", ""),
                columns=payload["columns"],
                sample=payload["rows"][:5],
            ), schema=MAP_SCHEMA)
            table = out["table"]
            mapping = {s: d for s, d in out["column_mapping"].items()
                       if table in CORE_TABLES and d in CORE_TABLES.get(table, [])
                       and s in payload["columns"]}
            if table in CORE_TABLES and out["confidence"] >= CONFIDENCE_THRESHOLD and mapping:
                col_idx = {c: i for i, c in enumerate(payload["columns"])}
                dst_cols = list(mapping.values()) + ["source_id"]
                n = 0
                with db.connect() as conn:
                    for row in payload["rows"]:
                        values = [
                            _coerce(dst, row[col_idx[src]]) if col_idx[src] < len(row) else None
                            for src, dst in mapping.items()
                        ]
                        if all(v is None for v in values):
                            continue
                        conn.execute(
                            f"INSERT INTO staging.{table} ({', '.join(dst_cols)}) "
                            f"VALUES ({', '.join(['%s'] * len(dst_cols))})",
                            values + [source_id],
                        )
                        n += 1
                result["staged"].append({"table": table, "rows": n})
            else:
                with db.connect() as conn:
                    conn.execute(
                        "INSERT INTO staging_tables (source_id, table_title, raw_data, "
                        "suggested_mapping, mapping_confidence) VALUES (%s, %s, %s, %s, %s)",
                        (source_id, payload.get("table_title", ""),
                         json.dumps(payload, ensure_ascii=False),
                         json.dumps(out, ensure_ascii=False), out["confidence"]),
                    )
                result["needs_review"] += 1
        except Exception as e:
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO staging_tables (source_id, table_title, raw_data, "
                    "suggested_mapping, mapping_confidence) VALUES (%s, %s, %s, %s, %s)",
                    (source_id, payload.get("table_title", ""),
                     json.dumps(payload, ensure_ascii=False),
                     json.dumps({"error": str(e)}, ensure_ascii=False), 0.0),
                )
            result["needs_review"] += 1
    return result
