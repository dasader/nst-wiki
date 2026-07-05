"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Markdown from "../../Markdown";

function splitFrontmatter(md) {
  const m = md.match(/^---\n([\s\S]*?)\n---\n?/);
  return m ? { front: m[1], body: md.slice(m[0].length) } : { front: null, body: md };
}

function linkifyWiki(md) {
  // [[tech/foo]] → 내부 링크, [[data:...]] → 데이터 탐색기 링크
  return md
    .replace(/\[\[data:([^\]]+)\]\]/g, (_, ref) => `[data:${ref}](/data)`)
    .replace(/\[\[([^\]:]+)\]\]/g, (_, p) =>
      `[${p}](/wiki/view?path=${encodeURIComponent(p.endsWith(".md") ? p : p + ".md")})`);
}

function Viewer() {
  const path = useSearchParams().get("path");
  const [page, setPage] = useState(null);
  const [err, setErr] = useState(null);

  useEffect(() => {
    if (!path) return;
    fetch(`/api/v1/wiki/page?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setPage, (e) => setErr(`페이지를 찾을 수 없습니다 (${e})`));
  }, [path]);

  if (err) return <div className="card">{err}</div>;
  if (!page) return <p>불러오는 중…</p>;
  const { front, body } = splitFrontmatter(page.content_md);
  return (
    <div>
      <p><a href="/wiki">← 위키 목록</a></p>
      <h1>{page.path}</h1>
      {front && <details className="card"><summary>메타데이터</summary><pre>{front}</pre></details>}
      <div className="card">
        <Markdown>{linkifyWiki(body)}</Markdown>
      </div>
      <div className="card">
        <b>변경 이력</b>
        <ul>{page.history.map((h) => (
          <li key={h.hash}><code>{h.hash}</code> {h.date} — {h.subject}</li>
        ))}</ul>
      </div>
    </div>
  );
}

export default function WikiView() {
  return <Suspense fallback={<p>불러오는 중…</p>}><Viewer /></Suspense>;
}
