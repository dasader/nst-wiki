"use client";
import { useEffect, useState } from "react";

const TABLES = ["technologies", "projects", "policy_events", "ministries",
                "budget_history", "tech_project_mapping"];

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

  async function load(p = page) {
    const params = new URLSearchParams({ page: p, limit });
    if (sortBy) { params.set("sort_by", sortBy); params.set("order", order); }
    if (column && q) { params.set("column", column); params.set("q", q); }
    const r = await fetch(`/api/v1/data/${table}?${params}`);
    if (!r.ok) return;
    const b = await r.json();
    setRows(b.rows); setTotal(b.total); setPage(b.page);
  }

  useEffect(() => { setSortBy(null); setColumn(""); setQ(""); setPage(1); }, [table]);
  useEffect(() => { load(1); }, [table, sortBy, order]);   // eslint-disable-line

  const cols = rows.length ? Object.keys(rows[0]) : [];
  const maxPage = Math.max(1, Math.ceil(total / limit));

  function clickSort(c) {
    if (sortBy === c) setOrder(order === "asc" ? "desc" : "asc");
    else { setSortBy(c); setOrder("asc"); }
  }

  return (
    <div>
      <h1>데이터 탐색기</h1>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
        <select value={table} onChange={(e) => setTable(e.target.value)}>
          {TABLES.map((t) => <option key={t}>{t}</option>)}
        </select>
        <select value={column} onChange={(e) => setColumn(e.target.value)}>
          <option value="">필터 컬럼</option>
          {cols.map((c) => <option key={c}>{c}</option>)}
        </select>
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="검색어" />
        <button onClick={() => load(1)}>적용</button>
        <span style={{ alignSelf: "center" }}>{total}건</span>
      </div>
      <div style={{ overflowX: "auto" }}>
        <table>
          <thead><tr>{cols.map((c) => (
            <th key={c} onClick={() => clickSort(c)}>
              {c}{sortBy === c ? (order === "asc" ? " ↑" : " ↓") : ""}
            </th>
          ))}</tr></thead>
          <tbody>{rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>
          ))}</tbody>
        </table>
      </div>
      <p>
        <button disabled={page <= 1} onClick={() => load(page - 1)}>이전</button>
        {" "}{page}/{maxPage}{" "}
        <button disabled={page >= maxPage} onClick={() => load(page + 1)}>다음</button>
      </p>
    </div>
  );
}
