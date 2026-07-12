"""정형 표 경로: 고정 스키마 매핑(LLM) → staging 적재. LLM은 DDL을 만들지 않는다."""
import json
import re
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
    # ponytail: project_code 컬럼은 staging.budget_history에 없고(마이그레이션 금지 상태) 승인 시
    # project_id로 해소할 자리가 없어 fiscal_year+amount만 적재, project_id는 NULL로 둔다.
    # project별 예산 연결은 staging.budget_history.project_code 컬럼 추가(마이그레이션) 후 upsert JOIN으로.
    "budget_history": ["fiscal_year", "amount"],
}
INT_COLS = {"trl_level", "budget_total", "budget_annual", "start_year", "end_year",
            "fiscal_year", "amount"}

# ponytail: 정책문서 표의 선행 서식(목록 기호·항목 번호·<n>) 제거 휴리스틱 —
# 숫자+공백/구두점 패턴만 제거하므로 "5G"처럼 숫자로 시작하는 명칭은 보존.
# 새 서식 유형이 나타나면 패턴 추가로 대응 (완전한 파서는 YAGNI)
_PREFIX_RE = re.compile(r"^(?:[◯○●◦□■▷▶·•§]\s*|[①-⑳㉑-㉟]\s*|\d+[.)]\s*|\d+\s+|<\d+>\s*|\(\d+\)\s*|[-–]\s+)")
# PDF 추출 시 가운뎃점 계열(·ㆍ‧) 주변에 끼어드는 잘못된 공백 제거 + 계열 문자를 ·로 통일.
# 가운뎃점은 정당한 구분자이므로 보존하되 변종을 하나로 정규화.
_MIDDOT_RE = re.compile(r"\s*[·ㆍ‧]\s*")

# 국가전략기술 12대 분야 정규 표기. field 값을 여기에 맞춰 정규화(공백 무시 매칭).
FIELD_VOCAB = [
    "반도체·디스플레이", "이차전지", "첨단모빌리티", "차세대원자력",
    "첨단바이오", "우주항공·해양", "수소", "사이버보안",
    "인공지능", "차세대통신", "첨단로봇·제조", "양자",
]
# 매칭 시 공백·가운뎃점 계열을 모두 제거 → "우주항공 해양"(·대신 공백)도 정규 표기로 매핑
_FIELD_NORM = re.compile(r"[\s·ㆍ‧]")
_FIELD_LOOKUP = {_FIELD_NORM.sub("", f): f for f in FIELD_VOCAB}

# 세부주제·별칭 → 12분야. 매핑이 명확한 것만. 애매한 태그(연구데이터·국가전략기술)는 넣지 않는다.
_FIELD_SYNONYMS = {
    "6G": "차세대통신", "오픈랜": "차세대통신", "오픈RAN": "차세대통신",
    "자율주행": "첨단모빌리티", "자율주행시스템": "첨단모빌리티", "UAM": "첨단모빌리티",
    "전기차": "첨단모빌리티",
    "로봇": "첨단로봇·제조",
    "반도체": "반도체·디스플레이", "HBM": "반도체·디스플레이", "디스플레이": "반도체·디스플레이",
    "양자컴퓨팅": "양자", "양자컴퓨터": "양자",
}
# 조회 키는 표기 정규화(공백·가운뎃점 제거) 후 casefold — "6g"·"hbm"·"uam" 대소문자 무관 매칭
_SYN_LOOKUP = {_FIELD_NORM.sub("", k).casefold(): v for k, v in _FIELD_SYNONYMS.items()}

# 티어 2(metrics) 통제 어휘. metric_name을 여기에 맞춰 정규화(공백·가운뎃점 무시).
# 없으면 원문 유지하되, 새 지표가 반복 등장하면 수요 기반으로 여기 추가 — EAV 붕괴 방지용
# 최소 어휘다(설계서 4.4/12.4).
METRIC_VOCAB = ["예산", "인력", "목표", "실적", "건수", "비중", "매출", "투자"]
_METRIC_LOOKUP = {_FIELD_NORM.sub("", m): m for m in METRIC_VOCAB}


def _clean_str(s: str) -> str:
    s = s.strip()
    prev = None
    while prev != s:
        prev = s
        s = _PREFIX_RE.sub("", s).strip()
    return _MIDDOT_RE.sub("·", s).strip()


def canon_field(s: str) -> str:
    norm = _FIELD_NORM.sub("", s)
    if norm in _FIELD_LOOKUP:            # 12분야 정규 표기 우선
        return _FIELD_LOOKUP[norm]
    return _SYN_LOOKUP.get(norm.casefold(), s)  # 세부주제 별칭, 없으면 원문 그대로


def canon_metric(s: str) -> str:
    return _METRIC_LOOKUP.get(_FIELD_NORM.sub("", s or ""), _clean_str(s or ""))


def _num(val):
    """지표 값 → float. value 컬럼이 NUMERIC이라 소수·퍼센트를 보존(amount의 int 절삭과 다름)."""
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (ValueError, AttributeError):
        return None


_MAP_ITEM = {
    "table": {"type": "string", "enum": list(CORE_TABLES) + ["metrics", "none"]},
    "confidence": {"type": "number"},
    "column_mapping": {                  # 티어 1(엔티티 테이블)용 1:1 대응
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"src": {"type": "string"}, "dst": {"type": "string"}},
            "required": ["src", "dst"],
        },
    },
    # 티어 2(metrics)용 — table=="metrics"일 때만 채운다 (column_mapping은 비움)
    "entity_col": {"type": "string"},    # 대상 라벨이 든 표 컬럼명
    "metric_name": {"type": "string"},   # 지표명 (예: 예산, 인력)
    "unit": {"type": "string"},          # 단위 (백만원, 명 등)
}
_REQUIRED = ["table", "confidence", "column_mapping"]  # 티어 2 필드는 선택

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "mappings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"index": {"type": "integer"}, **_MAP_ITEM},
                "required": ["index", *_REQUIRED],
            },
        }
    },
    "required": ["mappings"],
}

# ponytail: 표 8개를 한 프롬프트에 — 24개 표 문서 기준 LLM 호출 24→3회.
# 프롬프트가 커져 매핑 품질이 떨어지면 줄이고, 토큰 기반 분할이 필요해지면 그때 도입.
BATCH_SIZE = 8

PROMPT = """한국 정책문서에서 추출한 표들을 DB 스키마에 매핑하라.

대상 테이블과 컬럼:
{schema_desc}

표 목록:
{tables}

각 표마다 mappings에 항목을 하나씩 반환하라. index는 위 표 번호를 그대로 쓴다.
표가 위 테이블 중 하나에 대응하면 table에 테이블명, column_mapping에 [{{"src": "표 컬럼명", "dst": "DB 컬럼명"}}, ...] 목록을 반환하라.

위 테이블엔 안 맞지만 **연도별 수치**가 반복되는 표(예: 연도별 예산·인력·목표·실적, 컬럼 헤더가 '24·2025 같은 연도)는
table에 "metrics"를 반환하고 다음을 채워라 (column_mapping은 비운다):
- entity_col: 지표 대상(사업명·기술명·분야 등)이 든 표 컬럼명
- metric_name: 지표명 — 가능하면 {metric_vocab} 중 하나로
- unit: 단위 (백만원, 명, 건, % 등)

셋 중 어디에도 안 맞으면 table에 "none"을 반환하라. confidence는 매핑 확신도(0~1)."""


def _render(payloads: list[dict]) -> str:
    return "\n\n".join(
        f"[{i}] 제목: {p.get('table_title', '')}\n"
        f"    컬럼: {p['columns']}\n"
        f"    샘플 행 (최대 5개): {p['rows'][:5]}"
        for i, p in enumerate(payloads)
    )


def _map_batch(payloads: list[dict], schema_desc: str) -> dict[int, dict]:
    """표 묶음을 LLM 호출 1회로 매핑. index로 표와 정렬한다(누락·재정렬 방어)."""
    out = llm.generate("map_table", PROMPT.format(
        schema_desc=schema_desc, tables=_render(payloads),
        metric_vocab=", ".join(METRIC_VOCAB),
    ), schema=BATCH_SCHEMA)
    return {m["index"]: m for m in out.get("mappings", [])
            if isinstance(m, dict) and isinstance(m.get("index"), int)}


# 연도 토큰: 4자리(2024) 또는 2자리 약식('24). 4자리 우선 매칭.
_YEAR_RE = re.compile(r"'?(\d{4}|\d{2})")


def _years(val) -> list[int]:
    """'24~'28, 2024-2028, '24 등에서 연도를 뽑아 4자리로. 1990~2100만."""
    out = []
    for t in _YEAR_RE.findall(str(val)):
        y = int(t)
        y = y + 2000 if y < 100 else y
        if 1990 <= y <= 2100:
            out.append(y)
    return out


def _coerce(col: str, val):
    if val is None or val == "":
        return None
    # ponytail: 기간 문자열은 start_year→첫 연도, end_year→끝 연도. 단일 기간 컬럼이 두
    # 연도를 다 담아도 매핑이 1:1이라 한쪽만 채워짐 — 행 단위 분리는 필요해지면 추가.
    if col in ("start_year", "end_year"):
        ys = _years(val)
        if not ys:
            return None
        return ys[0] if col == "start_year" else ys[-1]
    if col in INT_COLS:
        try:
            return int(float(str(val).replace(",", "")))
        except ValueError:
            return None
    s = _clean_str(str(val))
    return canon_field(s) if col == "field" else s


def _table_to_md(payload: dict) -> str:
    """표 payload를 GFM 마크다운 표로. 티어 3: 스키마에 안 맞는 표를 위키에 원형 보존."""
    cols = [str(c) for c in payload.get("columns", [])]
    if not cols:
        return ""
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ").strip()
    lines = ["| " + " | ".join(esc(c) for c in cols) + " |",
             "| " + " | ".join("---" for _ in cols) + " |"]
    for row in payload.get("rows", []):
        lines.append("| " + " | ".join(
            esc(row[i]) if i < len(row) else "" for i in range(len(cols))) + " |")
    title = str(payload.get("table_title", "")).strip()
    return (f"**{esc(title)}**\n\n" if title else "") + "\n".join(lines)


def _stash_for_review(payload: dict, suggestion: dict, confidence: float,
                      source_id: str, result: dict) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO staging_tables (source_id, table_title, raw_data, "
            "suggested_mapping, mapping_confidence) VALUES (%s, %s, %s, %s, %s)",
            (source_id, payload.get("table_title", ""),
             json.dumps(payload, ensure_ascii=False),
             json.dumps(suggestion, ensure_ascii=False), confidence),
        )
    result["needs_review"] += 1
    md = _table_to_md(payload)   # 티어 3: 원본은 staging_tables에, 표시는 위키 요약 페이지에
    if md:
        result["inline_md"].append(md)


def _melt_metrics(payload: dict, out: dict) -> list[tuple]:
    """와이드 표(대상 + 연도 컬럼들)를 (entity, metric_name, year, value, unit) 롱포맷으로 melt.
    부적합(대상 컬럼 없음·연도 컬럼 없음·지표명 없음)하면 [] — 호출부가 검토 대기로 넘긴다.
    ponytail: 연도가 컬럼 헤더인 와이드 표만 지원. 이미 롱포맷인 표는 []→검토 대기(티어 3).
    반복되면 롱포맷 감지 추가."""
    cols = payload["columns"]
    entity_col = out.get("entity_col")
    metric = canon_metric(out.get("metric_name", ""))
    unit = _clean_str(out.get("unit") or "") or None
    year_cols = [(i, _years(c)[0]) for i, c in enumerate(cols) if _years(c)]
    if entity_col not in cols or not year_cols or not metric:
        return []
    ei = cols.index(entity_col)
    rows = []
    for row in payload["rows"]:
        if ei >= len(row):
            continue
        entity = _clean_str(str(row[ei]))
        if not entity:
            continue
        for ci, yr in year_cols:
            if ci >= len(row):
                continue
            val = _num(row[ci])
            if val is not None:
                rows.append((entity, metric, yr, val, unit))
    return rows


def _stage_metrics(payload: dict, out: dict, source_id: str, result: dict) -> None:
    rows = _melt_metrics(payload, out) if out["confidence"] >= CONFIDENCE_THRESHOLD else []
    if not rows:
        _stash_for_review(payload, out, out["confidence"], source_id, result)
        return
    params = [list(r) + [source_id] for r in rows]
    with db.connect() as conn:
        conn.cursor().executemany(
            "INSERT INTO staging.metrics (entity, metric_name, year, value, unit, source_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)", params,
        )
    result["staged"].append({"table": "metrics", "rows": len(params)})


def _stage_one(payload: dict, out: dict, source_id: str, result: dict) -> None:
    """매핑 결과 하나를 staging에 적재하거나, 확신도가 낮으면 검토 대기로 넘긴다."""
    table = out["table"]
    if table == "metrics":               # 티어 2: 롱포맷 melt 경로
        _stage_metrics(payload, out, source_id, result)
        return
    raw_mapping = {m["src"]: m["dst"] for m in out.get("column_mapping", [])
                   if isinstance(m, dict) and "src" in m and "dst" in m}
    mapping = {s: d for s, d in raw_mapping.items()
               if table in CORE_TABLES and d in CORE_TABLES.get(table, [])
               and s in payload["columns"]}
    if not (table in CORE_TABLES and out["confidence"] >= CONFIDENCE_THRESHOLD and mapping):
        _stash_for_review(payload, out, out["confidence"], source_id, result)
        return
    col_idx = {c: i for i, c in enumerate(payload["columns"])}
    dst_cols = list(mapping.values()) + ["source_id"]
    params = []
    for row in payload["rows"]:
        values = [
            _coerce(dst, row[col_idx[src]]) if col_idx[src] < len(row) else None
            for src, dst in mapping.items()
        ]
        if all(v is None for v in values):
            continue
        params.append(values + [source_id])
    if params:
        with db.connect() as conn:
            conn.cursor().executemany(
                f"INSERT INTO staging.{table} ({', '.join(dst_cols)}) "
                f"VALUES ({', '.join(['%s'] * len(dst_cols))})",
                params,
            )
    result["staged"].append({"table": table, "rows": len(params)})


def map_and_stage_tables(parsed_dir: Path, source_id: str) -> dict:
    tables_dir = parsed_dir / "tables"
    result: dict = {"staged": [], "needs_review": 0, "inline_md": []}
    if not tables_dir.is_dir():
        return result
    schema_desc = "\n".join(f"- {t}: {', '.join(cols)}" for t, cols in CORE_TABLES.items())
    payloads = [json.loads(tf.read_text(encoding="utf-8"))
                for tf in sorted(tables_dir.glob("table_*.json"))]

    for start in range(0, len(payloads), BATCH_SIZE):
        batch = payloads[start:start + BATCH_SIZE]
        try:  # 배치 호출 실패는 그 묶음의 표만 검토 대기로 — 인제스트는 계속된다
            outs = _map_batch(batch, schema_desc)
            batch_err = None
        except Exception as e:
            outs, batch_err = {}, str(e)
        for i, payload in enumerate(batch):
            out = outs.get(i)
            if out is None:  # 배치가 실패했거나 LLM이 이 표를 빠뜨림
                _stash_for_review(
                    payload, {"error": batch_err or "LLM 응답에 이 표의 매핑이 없음"},
                    0.0, source_id, result)
                continue
            try:  # 표 하나의 적재 실패가 나머지를 막지 않게
                _stage_one(payload, out, source_id, result)
            except Exception as e:
                _stash_for_review(payload, {"error": str(e)}, 0.0, source_id, result)
    return result
