"use client";
import { useEffect, useState } from "react";
import { TABLE_LABELS, tableLabel, colLabel, MONEY_COLS } from "../labels";

const TABLES = Object.keys(TABLE_LABELS);
const isNum = (v) => typeof v === "number" || (typeof v === "string" && /^-?\d+(\.\d+)?$/.test(v));

function Cell({ col, value }) {
  if (value === null || value === "" || value === undefined)
    return <span className="empty">—</span>;
  if (col === "wiki_page_path")
    return <a href={`/wiki/view?path=${encodeURIComponent(value)}`}>{value.split("/").pop().replace(/\.md$/, "")}</a>;
  if (MONEY_COLS.has(col))
    return <>{Number(value).toLocaleString("ko-KR")}<span className="muted"> 백만원</span></>;
  return String(value);
}

export default function DataExplorer() {
  const [table, setTable] = useState("technologies");
  const [rows, setRows] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState(null);
  const [order, setOrder] = useState("asc");
  const [column, setColumn] = useState("");
  const [q, setQ] = useState("");
  const limit = 50;

  async function load(p = page, opts = {}) {
    const col = opts.column ?? column, qq = opts.q ?? q;
    const sb = "sortBy" in opts ? opts.sortBy : sortBy, od = opts.order ?? order;
    const params = new URLSearchParams({ page: p, limit });
    if (sb) { params.set("sort_by", sb); params.set("order", od); }
    if (col && qq) { params.set("column", col); params.set("q", qq); }
    const r = await fetch(`/api/v1/data/${table}?${params}`);
    if (!r.ok) return;
    const b = await r.json();
    setRows(b.rows); setTotal(b.total); setPage(b.page);
  }

  useEffect(() => {
    setSortBy(null); setColumn(""); setQ("");
    load(1, { column: "", q: "", sortBy: null });
  }, [table]);   // eslint-disable-line
  useEffect(() => { if (sortBy !== null) load(1); }, [sortBy, order]);   // eslint-disable-line

  const cols = rows.length ? Object.keys(rows[0]) : [];
  const maxPage = Math.max(1, Math.ceil(total / limit));

  function clickSort(c) {
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
                  onClick={() => setTable(t)}>
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
               onKeyDown={(e) => e.key === "Enter" && load(1)}
               placeholder="검색어" aria-label="검색어" style={{ flex: 1, minWidth: 160 }} />
        <button className="btn-sm" onClick={() => load(1)}>적용</button>
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
        <button className="btn-ghost btn-sm" disabled={page <= 1} onClick={() => load(page - 1)}>← 이전</button>
        <span>{page} / {maxPage}</span>
        <button className="btn-ghost btn-sm" disabled={page >= maxPage} onClick={() => load(page + 1)}>다음 →</button>
      </div>
    </div>
  );
}
