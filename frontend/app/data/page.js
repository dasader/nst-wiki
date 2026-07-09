"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { TABLE_LABELS, tableLabel, colLabel, MONEY_COLS, pageSlug, wikiViewHref } from "../labels";
import Loading from "../Loading";

const TABLES = Object.keys(TABLE_LABELS);
const LIMIT = 50;
const isNum = (v) => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v));

function Cell({ col, value }) {
  if (value === null || value === "" || value === undefined)
    return <span className="empty">—</span>;
  if (col === "wiki_page_path")
    return <a href={wikiViewHref(value)}>{pageSlug(value)}</a>;
  if (MONEY_COLS.has(col))
    return <>{Number(value).toLocaleString("ko-KR")}<span className="muted"> 백만원</span></>;
  return String(value);
}

function DataExplorer() {
  const sp = useSearchParams();   // 위키 [[data:테이블?컬럼=값]] 딥링크
  const [table, setTable] = useState(() => (TABLE_LABELS[sp.get("table")] ? sp.get("table") : "technologies"));
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState(null);
  const [order, setOrder] = useState("asc");
  const [column, setColumn] = useState(() => sp.get("column") || "");
  const [q, setQ] = useState(() => sp.get("q") || "");
  // 실제 조회에 반영된 필터 — 입력(column/q)과 분리해 "적용"을 눌러야 조회된다
  const [applied, setApplied] = useState(() => ({ column: sp.get("column") || "", q: sp.get("q") || "" }));

  // 조회는 이 효과 하나로 — table·page·정렬·적용된 필터가 바뀔 때만 (딥링크는 초기 상태에 반영됨)
  useEffect(() => {
    const params = new URLSearchParams({ page, limit: LIMIT });
    if (sortBy) { params.set("sort_by", sortBy); params.set("order", order); }
    if (applied.column && applied.q) { params.set("column", applied.column); params.set("q", applied.q); }
    let cancelled = false;
    (async () => {
      const r = await fetch(`/api/v1/data/${table}?${params}`);
      if (!r.ok) {
        // 400 = 이 테이블에 없는 컬럼(딥링크). 필터만 버리고 재조회. 그 외(500·타임아웃)는 입력 보존
        if (r.status === 400 && applied.column && applied.q) {
          setColumn(""); setQ(""); setApplied({ column: "", q: "" });
        }
        return;
      }
      const b = await r.json();
      if (!cancelled) { setRows(b.rows); setTotal(b.total); }
    })();
    return () => { cancelled = true; };
  }, [table, page, sortBy, order, applied]);

  const cols = rows.length ? Object.keys(rows[0]) : [];
  const maxPage = Math.max(1, Math.ceil(total / LIMIT));

  function changeTable(t) {   // 테이블 전환: 정렬·필터·페이지 초기화 (배치되어 효과는 1회 실행)
    setTable(t); setPage(1); setSortBy(null); setOrder("asc");
    setColumn(""); setQ(""); setApplied({ column: "", q: "" });
  }
  const applyFilter = () => { setPage(1); setApplied({ column, q }); };

  function clickSort(c) {
    setPage(1);
    if (sortBy === c) setOrder(order === "asc" ? "desc" : "asc");
    else { setSortBy(c); setOrder("asc"); }
  }

  return (
    <div>
      <span className="eyebrow">정형 데이터</span>
      <h1>데이터 탐색기</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        기술·사업·예산·정책 데이터를 표로 조회하고 정렬·검색합니다.
      </p>

      <div className="row" style={{ margin: "18px 0 4px" }}>
        {TABLES.map((t) => (
          <button key={t} type="button" className={`btn-sm ${table === t ? "" : "btn-ghost"}`}
                  onClick={() => changeTable(t)}>
            {tableLabel(t)}
          </button>
        ))}
      </div>

      <div className="row" style={{ margin: "14px 0" }}>
        <select value={column} onChange={(e) => setColumn(e.target.value)} aria-label="필터 컬럼">
          <option value="">전체 컬럼</option>
          {cols.map((c) => <option key={c} value={c}>{colLabel(table, c)}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && applyFilter()}
               placeholder="검색어" aria-label="검색어" style={{ flex: 1, minWidth: 160 }} />
        <button className="btn-sm" onClick={applyFilter}>적용</button>
        <span className="chip neutral">{total.toLocaleString("ko-KR")}건</span>
      </div>

      {rows.length === 0 ? (
        <div className="empty-state">조회된 데이터가 없습니다.</div>
      ) : (
        <div className="table-wrap">
          <div className="table-scroll">
            <table className="table-stack">
              <thead><tr>{cols.map((c) => (
                <th key={c} className="sortable" onClick={() => clickSort(c)}>
                  {colLabel(table, c)}{sortBy === c ? (order === "asc" ? " ↑" : " ↓") : ""}
                </th>
              ))}</tr></thead>
              <tbody>{rows.map((r, i) => (
                <tr key={i}>{cols.map((c) => (
                  <td key={c} data-label={colLabel(table, c)}
                      className={isNum(r[c]) || MONEY_COLS.has(c) ? "num" : ""}>
                    <Cell col={c} value={r[c]} />
                  </td>
                ))}</tr>
              ))}</tbody>
            </table>
          </div>
        </div>
      )}

      <div className="pager">
        <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>← 이전</button>
        <span>{page} / {maxPage}</span>
        <button className="btn-ghost btn-sm" disabled={page >= maxPage} onClick={() => setPage(page + 1)}>다음 →</button>
      </div>
    </div>
  );
}

export default function DataPage() {
  return <Suspense fallback={<Loading />}><DataExplorer /></Suspense>;
}
