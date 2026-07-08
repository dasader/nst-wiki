"use client";
import { useEffect, useState } from "react";
import { adminFetch } from "../adminAuth";

const usd = (v) => (v == null ? "단가 미등록" : `$${v < 0.01 ? v.toFixed(5) : v.toFixed(4)}`);
const num = (n) => (n ?? 0).toLocaleString();
const pct = (v, total) => (total > 0 && v != null ? `${((v / total) * 100).toFixed(1)}%` : "—");

// 토큰 열은 어디서 돈이 새는지 보이게 — 사고 토큰은 출력 단가로 과금된다
const TOKEN_COLS = [
  ["prompt_tokens", "입력"], ["cached_tokens", "캐시"],
  ["output_tokens", "출력"], ["thought_tokens", "사고"],
];

function Table({ head, rows, total, keyOf, label }) {
  if (!rows.length) return <p className="muted">기록 없음</p>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
        <thead>
          <tr>
            <th style={{ textAlign: "left" }}>{head}</th>
            <th style={{ textAlign: "right" }}>호출</th>
            {TOKEN_COLS.map(([, l]) => <th key={l} style={{ textAlign: "right" }}>{l}</th>)}
            <th style={{ textAlign: "right" }}>비용</th>
            <th style={{ textAlign: "right" }}>비중</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={keyOf(r)} style={{ borderTop: "1px solid var(--line)" }}>
              <td>{label(r)}</td>
              <td style={{ textAlign: "right" }}>{num(r.calls)}</td>
              {TOKEN_COLS.map(([c]) => (
                <td key={c} style={{ textAlign: "right" }}>{num(r[c])}</td>
              ))}
              <td style={{ textAlign: "right" }}>{usd(r.cost_usd)}</td>
              <td style={{ textAlign: "right" }} className="muted">{pct(r.cost_usd, total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function UsagePage() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    adminFetch("/api/v1/admin/usage")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status}`))))
      .then(setData)
      .catch((e) => setErr(`불러오지 못했습니다 (${e.message})`));
  }, []);

  if (err) return <div className="card error">{err}</div>;
  if (!data) return <p className="muted">불러오는 중…</p>;

  const total = data.total_usd || 0;
  const since = data.since ? new Date(data.since).toLocaleString("ko-KR") : null;

  return (
    <div>
      <span className="eyebrow">운영</span>
      <h1>LLM 비용</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        {since
          ? <>측정 시작: {since} — 그 이전 호출은 토큰 기록이 없어 집계되지 않습니다.</>
          : <>아직 기록된 호출이 없습니다. 문서를 인제스트하거나 질문을 하면 쌓입니다.</>}
      </p>

      {data.unpriced_models?.length > 0 && (
        <div className="card error" style={{ marginTop: 14 }}>
          <b>단가 미등록 모델:</b> {data.unpriced_models.join(", ")}
          <p style={{ marginBottom: 0, fontSize: "0.85rem" }}>
            이 모델의 호출은 총액에서 <b>빠져</b> 있습니다. <code>api/llm_pricing.json</code>에 단가를 추가하세요.
          </p>
        </div>
      )}

      <div className="card" style={{ marginTop: 14 }}>
        <div className="card-label">누적 비용</div>
        <div style={{ fontSize: "2rem", fontWeight: 700, margin: "6px 0" }}>
          ${total.toFixed(4)}
        </div>
        <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>
          호출 {num(data.total_calls)}회 · 사고(thinking) 토큰은 출력 단가로 과금됩니다
        </p>
      </div>

      <h2 style={{ marginTop: 24 }}>용도별</h2>
      <div className="card">
        <Table head="purpose" rows={data.by_purpose} total={total}
               keyOf={(r) => `${r.purpose}/${r.model}`} label={(r) => r.purpose} />
      </div>

      <h2 style={{ marginTop: 24 }}>문서별 (인제스트)</h2>
      <div className="card">
        <Table head="문서" rows={data.by_source} total={total}
               keyOf={(r) => `${r.source_id}/${r.model}`}
               label={(r) => r.title || r.source_id.slice(0, 8)} />
      </div>

      <h2 style={{ marginTop: 24 }}>질의응답 (문서 귀속 없음)</h2>
      <div className="card">
        <Table head="모델" rows={data.query_side} total={total}
               keyOf={(r) => r.model} label={(r) => r.model} />
      </div>
    </div>
  );
}
