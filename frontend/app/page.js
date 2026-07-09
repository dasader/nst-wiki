"use client";
import { useState } from "react";
import Markdown from "./Markdown";
import { pageSlug, wikiViewHref } from "./labels";

const MODES = [
  { v: "auto", label: "자동" },
  { v: "narrative", label: "서사" },
  { v: "data", label: "데이터" },
  { v: "hybrid", label: "혼합" },
];

const EXAMPLES = [
  "반도체 분야에 어떤 기술이 있어?",
  "부처별 R&D 예산 규모를 알려줘",
  "12개 분야가 10개로 재편된 이유는?",
];

export default function QueryPage() {
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("auto");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  async function ask(question) {
    if (!question.trim()) return;
    setBusy(true); setErr(null); setRes(null);
    try {
      const r = await fetch("/api/v1/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question, mode }),
      });
      if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
      setRes(await r.json());
    } catch (e2) { setErr(String(e2)); }
    setBusy(false);
  }

  const rows = res?.sql_rows ?? [];
  const cols = rows.length ? Object.keys(rows[0]) : [];

  return (
    <div>
      <section className="hero">
        <span className="eyebrow">NEXT · 정책 인텔리전스</span>
        <h1>무엇이든 물어보세요</h1>
        <p>국가전략기술 정책 지식을 자연어로 질의하세요.</p>

        <form className="ask" onSubmit={(e) => { e.preventDefault(); ask(q); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)}
                 placeholder="예: 반도체 분야에 어떤 기술이 있어?" aria-label="질문" required />
          <select value={mode} onChange={(e) => setMode(e.target.value)} aria-label="검색 방식">
            {MODES.map((m) => <option key={m.v} value={m.v}>{m.label}</option>)}
          </select>
          <button disabled={busy}>{busy ? <><span className="spinner" /> 질의 중</> : "질문"}</button>
        </form>

        {!res && !busy && (
          <div className="examples">
            {EXAMPLES.map((ex) => (
              <button key={ex} type="button" onClick={() => { setQ(ex); ask(ex); }}>{ex}</button>
            ))}
          </div>
        )}
      </section>

      {busy && (
        <div className="card subtle">
          <span className="spinner" /> 답변을 생성하고 있습니다. 최초 질의는 모델 로드로 다소 걸릴 수 있습니다.
        </div>
      )}
      {err && <div className="card error">{err}</div>}

      {res && (
        <div>
          <div className="card"><Markdown>{res.answer}</Markdown></div>

          {res.citations?.length > 0 && (
            <div className="card">
              <div className="card-label" style={{ marginBottom: 10 }}>근거 문서</div>
              <div className="row">
                {res.citations.map((c) => (
                  <a key={c.path} className="chip" href={wikiViewHref(c.path)}>
                    {pageSlug(c.path)}
                  </a>
                ))}
              </div>
            </div>
          )}

          {res.sql && (
            <div className="card">
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 12 }}>
                <div className="card-label">데이터 조회 결과</div>
                <span className="chip neutral">
                  {res.sql_error ? `오류: ${res.sql_error}` : `${rows.length}행`}
                </span>
              </div>
              {rows.length > 0 && (
                <div className="table-wrap">
                  <div className="table-scroll">
                    <table>
                      <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
                      <tbody>{rows.map((r, i) => (
                        <tr key={i}>{cols.map((c) => <td key={c}>{String(r[c] ?? "")}</td>)}</tr>
                      ))}</tbody>
                    </table>
                  </div>
                </div>
              )}
              <details className="reveal" style={{ marginTop: 12 }}>
                <summary>실행된 SQL 쿼리 보기</summary>
                <pre className="code">{res.sql}</pre>
              </details>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
