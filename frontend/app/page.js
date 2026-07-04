"use client";
import { useState } from "react";

export default function QueryPage() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  async function ask(e) {
    e.preventDefault();
    setBusy(true); setErr(null); setRes(null);
    try {
      const r = await fetch("/api/v1/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q, mode }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setRes(await r.json());
    } catch (e2) { setErr(String(e2)); }
    setBusy(false);
  }

  return (
    <div>
      <h1>자연어 질의</h1>
      <form onSubmit={ask} style={{ display: "flex", gap: 8 }}>
        <input style={{ flex: 1 }} value={q} onChange={(e) => setQ(e.target.value)}
               placeholder="예: 반도체 분야에 어떤 기술이 있어?" required />
        <select value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="auto">auto</option><option value="narrative">서사</option>
          <option value="data">데이터</option><option value="hybrid">혼합</option>
        </select>
        <button disabled={busy}>{busy ? "질의 중…" : "질문"}</button>
      </form>
      {busy && <p>답변 생성 중입니다 (최초 질의는 모델 로드로 오래 걸릴 수 있음)…</p>}
      {err && <div className="card" style={{ color: "#b3403a" }}>{err}</div>}
      {res && (
        <div>
          <div className="card" style={{ whiteSpace: "pre-wrap" }}>{res.answer}</div>
          {res.citations?.length > 0 && (
            <div className="card">근거:{" "}
              {res.citations.map((c) => (
                <a key={c.path} className="cite" href={`/wiki/view?path=${encodeURIComponent(c.path)}`}>
                  [{c.path}]
                </a>
              ))}
            </div>
          )}
          {res.sql && (
            <div className="card">
              <b>SQL</b> ({res.sql_error ? `오류: ${res.sql_error}` : `${res.sql_rows?.length ?? 0}행`})
              <pre>{res.sql}</pre>
              {res.sql_rows?.length > 0 && <pre>{JSON.stringify(res.sql_rows, null, 2)}</pre>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
