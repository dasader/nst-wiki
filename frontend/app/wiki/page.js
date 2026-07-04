"use client";
import { useEffect, useState } from "react";

export default function WikiList() {
  const [pages, setPages] = useState([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState(null);

  useEffect(() => {
    fetch("/api/v1/wiki").then((r) => r.json()).then((b) => setPages(b.pages || []));
  }, []);

  async function search(e) {
    e.preventDefault();
    if (!q) { setHits(null); return; }
    const r = await fetch(`/api/v1/wiki/search?q=${encodeURIComponent(q)}`);
    setHits((await r.json()).results || []);
  }

  const groups = {};
  for (const p of pages) {
    const dir = p.split("/")[0];
    (groups[dir] ||= []).push(p);
  }

  return (
    <div>
      <h1>위키</h1>
      <form onSubmit={search} style={{ display: "flex", gap: 8 }}>
        <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="전문 검색 (git grep)" />
        <button>검색</button>
      </form>
      {hits && (
        <div className="card">
          <b>검색 결과 {hits.length}건</b>
          <ul>{hits.map((h, i) => (
            <li key={i}>
              <a href={`/wiki/view?path=${encodeURIComponent(h.path)}`}>{h.path}</a>
              <small> — {h.line}</small>
            </li>
          ))}</ul>
        </div>
      )}
      {Object.entries(groups).map(([dir, ps]) => (
        <div className="card" key={dir}>
          <h3>{dir}/</h3>
          <ul>{ps.map((p) => (
            <li key={p}><a href={`/wiki/view?path=${encodeURIComponent(p)}`}>{p.split("/").slice(1).join("/")}</a></li>
          ))}</ul>
        </div>
      ))}
    </div>
  );
}
