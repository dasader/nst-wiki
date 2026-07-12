"use client";
import { useEffect, useState } from "react";
import { adminFetch } from "./adminAuth";
import Loading from "../Loading";
import { colLabel, tableLabel } from "../labels";

// 상태 → 필터 그룹 · 사람 라벨 · 칩 색 (dashboard 구버전과 동일 규약)
const GROUP = {
  staged: "staged", approved: "approved",
  queued: "proc", parsing: "proc", classifying: "proc",
  rejected: "rejfail", failed: "rejfail",
};
const STATUS_LABEL = {
  staged: "검토 대기", approved: "승인됨", rejected: "거부", failed: "실패",
  queued: "대기", parsing: "파싱 중", classifying: "분류 중",
};
const label = (s) => STATUS_LABEL[s] || s;
const chipOf = (s) => `st-${GROUP[s] || "proc"}`;

const FILTERS = [
  { k: "all", label: "전체" },
  { k: "staged", label: "승인 대기" },
  { k: "proc", label: "처리 중" },
  { k: "approved", label: "승인됨" },
  { k: "rejfail", label: "거부·실패" },
];

export default function ApprovalQueue() {
  const [tasks, setTasks] = useState([]);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(null); // task_id | null

  async function loadList() {
    const res = await fetch("/api/v1/ingest");
    setTasks((await res.json()).tasks || []);
  }

  useEffect(() => {
    if (selected) return;   // 상세 검토 중엔 목록 폴링 중단 (돌아오면 재개)
    loadList();
    const t = setInterval(loadList, 15000);
    return () => clearInterval(t);
  }, [selected]);

  if (selected)
    return <ReviewDetail taskId={selected} onBack={() => setSelected(null)} onChanged={loadList} />;

  const counts = { all: tasks.length, staged: 0, proc: 0, approved: 0, rejfail: 0 };
  for (const t of tasks) counts[GROUP[t.status]] = (counts[GROUP[t.status]] || 0) + 1;
  const rows = tasks.filter((t) => filter === "all" || GROUP[t.status] === filter);

  return (
    <div>
      <span className="eyebrow">인제스트 검토</span>
      <h1>승인 대기</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        업로드된 문서의 파싱·분류 결과를 검토하고 위키·데이터 반영을 승인합니다.
      </p>

      <div className="row" style={{ margin: "18px 0 12px" }}>
        {FILTERS.map((f) => (
          <button
            key={f.k}
            className={`chip ${filter === f.k ? "" : "neutral"}`}
            style={{ cursor: "pointer", border: "none" }}
            onClick={() => setFilter(f.k)}
          >
            {f.label} · {counts[f.k] || 0}
          </button>
        ))}
      </div>

      {rows.length === 0 ? (
        <div className="empty-state">해당 상태의 태스크가 없습니다.</div>
      ) : (
        <ul className="list card" style={{ padding: "4px 14px" }}>
          {rows.map((t) => (
            <li key={t.task_id}>
              <a role="button" tabIndex={0}
                 onClick={() => setSelected(t.task_id)}
                 onKeyDown={(e) => e.key === "Enter" && setSelected(t.task_id)}
                 style={{ cursor: "pointer", flexWrap: "wrap" }}>
                <span className={`chip ${chipOf(t.status)}`}>{label(t.status)}</span>
                <span style={{ fontWeight: 600 }}>{t.title || "(제목 없음)"}</span>
                <span className="meta" style={{ fontFamily: "var(--mono)" }}>
                  {t.task_id.slice(0, 8)}
                  {t.created_at ? ` · ${t.created_at}` : ""}
                </span>
              </a>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ── 검토 상세 ───────────────────────────────────
function ReviewDetail({ taskId, onBack, onChanged }) {
  const [r, setR] = useState(null);
  const [exclude, setExclude] = useState({}); // { table: Set(id) }
  const [resolutions, setResolutions] = useState({}); // { "src8-1": "replace" }
  const [busy, setBusy] = useState(false);

  const source8 = (r?.source_id || "").slice(0, 8);

  async function load() {
    const res = await fetch(`/api/v1/ingest/${taskId}/review`);
    const data = await res.json();
    setR(data);
    // 모순 기본값: 신규 채택
    const init = {};
    (data.contradictions || []).forEach((_, i) => {
      init[`${(data.source_id || "").slice(0, 8)}-${i + 1}`] = "replace";
    });
    setResolutions(init);
    setExclude({});
  }
  useEffect(() => { load(); }, [taskId]);

  function toggleExclude(table, id) {
    setExclude((prev) => {
      const set = new Set(prev[table] || []);
      set.has(id) ? set.delete(id) : set.add(id);
      return { ...prev, [table]: set };
    });
  }

  async function act(action) {
    setBusy(true);
    try {
      const body = {
        contradiction_resolutions: resolutions,
        exclude: Object.fromEntries(
          Object.entries(exclude).map(([t, set]) => [t, [...set]]).filter(([, ids]) => ids.length)
        ),
      };
      const res = await adminFetch(`/api/v1/ingest/${taskId}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action === "approve" ? JSON.stringify(body) : null,
      });
      alert(`${action}: ${res.status} ${(await res.text()).slice(0, 300)}`);
      await onChanged();
      if (res.ok) await load();
    } finally {
      setBusy(false);
    }
  }

  async function deleteSource() {
    if (!confirm("이 소스의 정식 데이터·원본·태스크를 삭제합니다. 병합된 위키 서술은 남습니다. 계속?")) return;
    const res = await adminFetch(`/api/v1/ingest/${taskId}/source`, { method: "DELETE" });
    alert(`삭제: ${res.status} ${(await res.text()).slice(0, 400)}`);
    await onChanged();
    if (res.ok) onBack();
  }

  async function downloadOriginal() {
    const res = await adminFetch(`/api/v1/ingest/${taskId}/original`);
    if (!res.ok) return alert(`원본 없음 (${res.status})`);
    const cd = res.headers.get("content-disposition") || "";
    const name = decodeURIComponent(
      (cd.match(/filename\*?=(?:UTF-8'')?"?([^\";]+)/) || [])[1] || "original"
    );
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (!r)
    return (
      <div>
        <p style={{ margin: "0 0 14px" }}>
          <a role="button" tabIndex={0} onClick={onBack} style={{ cursor: "pointer" }}>← 승인 대기</a>
        </p>
        <Loading />
      </div>
    );

  const staged = r.staged || {};
  const stagedTables = Object.entries(staged).filter(([k, v]) => k !== "needs_review" && v.length);
  const needsReview = staged.needs_review || [];

  return (
    <div>
      <p style={{ margin: "0 0 14px" }}>
        <a role="button" tabIndex={0} onClick={onBack} style={{ cursor: "pointer" }}>← 승인 대기</a>
      </p>
      <h1 style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <span className={`chip ${chipOf(r.status)}`}>{label(r.status)}</span>
        {r.title || "(제목 없음)"}
      </h1>
      <p className="muted" style={{ fontFamily: "var(--mono)", fontSize: "0.8rem", marginTop: 0 }}>{taskId}</p>

      <div className="row" style={{ margin: "4px 0 8px" }}>
        {r.status === "staged" ? (
          <>
            <button disabled={busy} onClick={() => act("approve")}
                    style={{ background: "var(--success)" }}>승인</button>
            <button disabled={busy} onClick={() => act("reject")}
                    style={{ background: "var(--danger)" }}>거부</button>
          </>
        ) : (
          <>
            <button className="btn-ghost" onClick={downloadOriginal}>원본 다운로드</button>
            <button onClick={deleteSource} style={{ background: "var(--danger)" }}>소스 삭제 (un-ingest)</button>
          </>
        )}
      </div>

      {r.contradictions?.length > 0 && (
        <>
          <h2>모순 ({r.contradictions.length})</h2>
          {r.contradictions.map((c, i) => {
            const rid = `${source8}-${i + 1}`;
            return (
              <div className="card" key={i} style={{ borderLeft: "3px solid var(--amber)", background: "var(--amber-tint)" }}>
                <b>{String(c.summary ?? "")}</b> — {String(c.page ?? "")}
                <p className="muted" style={{ margin: "4px 0 10px", fontSize: "0.85rem" }}>
                  기존: {String(c.existing ?? "")} / 신규: {String(c.new ?? "")}
                  {" · "}신규 출처: 「{String(r.title || "")}」{r.publish_date ? ` (${r.publish_date})` : " (시점 미상)"}
                </p>
                <div className="row">
                  {[["keep", "기존 유지"], ["replace", "신규 채택"], ["both", "병기"]].map(([v, lbl]) => (
                    <label key={v} style={{ display: "inline-flex", alignItems: "center", gap: 5, fontSize: "0.85rem" }}>
                      <input type="radio" name={rid} value={v} checked={resolutions[rid] === v}
                             onChange={() => setResolutions((p) => ({ ...p, [rid]: v }))}
                             style={{ width: "auto", padding: 0 }} />
                      {lbl}
                    </label>
                  ))}
                </div>
              </div>
            );
          })}
        </>
      )}

      {r.suggestions?.length > 0 && (
        <>
          <h2>갱신 제안 (미적용)</h2>
          <ul>
            {r.suggestions.map((s, i) => (
              <li key={i}>{String(s.path)} ({String(s.action)})</li>
            ))}
          </ul>
        </>
      )}

      {stagedTables.map(([table, rows]) => (
        <div key={table}>
          <h3 style={{ marginTop: 22 }}>{tableLabel(table)} <span className="muted" style={{ fontFamily: "var(--mono)", fontWeight: 400, fontSize: "0.8rem" }}>staging.{table}</span> ({rows.length}행)</h3>
          <StagedTable rows={rows} table={table} exclude={exclude} onToggle={toggleExclude} />
        </div>
      ))}

      {needsReview.length > 0 && (
        <>
          <h3 style={{ marginTop: 22 }}>스키마 검토 필요 ({needsReview.length}건)</h3>
          <p className="muted" style={{ fontSize: "0.85rem", marginTop: 0 }}>
            고정 스키마에 매핑되지 않은 표입니다. 연도별(가로형) 수치 표는 metrics로 승격할 수 있습니다.
          </p>
          {needsReview.map((row) => (
            <NeedsReviewCard key={row.id} row={row} taskId={taskId}
                             canPromote={r.status === "staged"} onDone={load} />
          ))}
        </>
      )}

      <h2>위키 diff</h2>
      <pre className="code">{r.wiki_diff || "(없음)"}</pre>
    </div>
  );
}

function cellText(v) {
  return v !== null && typeof v === "object" ? JSON.stringify(v) : String(v ?? "");
}

// map_tables.METRIC_VOCAB 미러 (승격 폼 자동완성용 힌트일 뿐 — 자유 입력 허용)
const METRIC_VOCAB = ["예산", "인력", "목표", "실적", "건수", "비중", "매출", "투자"];

// 검토 대기 표를 실제 표로 보여주고, 연도별 수치 표면 metrics로 승격하는 폼.
function NeedsReviewCard({ row, taskId, canPromote, onDone }) {
  const rd = row.raw_data || {};
  const cols = rd.columns || [];
  const [entityCol, setEntityCol] = useState(cols[0] || "");
  const [metric, setMetric] = useState("");
  const [unit, setUnit] = useState("");
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function promote() {
    setBusy(true); setMsg(null);
    try {
      const res = await adminFetch(`/api/v1/ingest/${taskId}/promote-metrics`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ staging_id: row.id, entity_col: entityCol, metric_name: metric, unit }),
      });
      const b = await res.json().catch(() => ({}));
      if (res.ok) onDone();
      else setMsg(b.detail || `실패 (${res.status})`);
    } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <b>{row.table_title || "(제목 없음)"}</b>
      <div className="table-wrap"><div className="table-scroll">
        <table>
          <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {(rd.rows || []).slice(0, 20).map((r, i) => (
              <tr key={i}>{cols.map((_, j) => <td key={j}>{cellText(r[j])}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div></div>
      {canPromote && (
        <div className="row" style={{ gap: 10, marginTop: 10, alignItems: "flex-end", flexWrap: "wrap" }}>
          <label style={{ fontSize: "0.8rem" }}>대상 컬럼<br />
            <select value={entityCol} onChange={(e) => setEntityCol(e.target.value)}>
              {cols.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </label>
          <label style={{ fontSize: "0.8rem" }}>지표명<br />
            <input list="metric-vocab" value={metric} onChange={(e) => setMetric(e.target.value)}
                   placeholder="예: 예산" />
          </label>
          <datalist id="metric-vocab">{METRIC_VOCAB.map((m) => <option key={m} value={m} />)}</datalist>
          <label style={{ fontSize: "0.8rem" }}>단위<br />
            <input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="백만원"
                   style={{ width: 90 }} />
          </label>
          <button disabled={busy || !entityCol || !metric.trim()} onClick={promote}>metrics로 승격</button>
          {msg && <span style={{ color: "var(--danger)", fontSize: "0.8rem" }}>{msg}</span>}
        </div>
      )}
    </div>
  );
}

// 핵심 staging 표(id 컬럼 보유)만 행 제외 체크박스를 붙인다.
function StagedTable({ rows, table, exclude, onToggle }) {
  if (!rows.length) return null;
  const cols = Object.keys(rows[0]);
  const canExclude = table && "id" in rows[0];
  const excludedSet = exclude?.[table];
  return (
    <div className="table-wrap">
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              {canExclude && <th>제외</th>}
              {cols.map((c) => <th key={c} title={c}>{colLabel(table, c)}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={row.id ?? i}>
                {canExclude && (
                  <td>
                    <input type="checkbox" style={{ width: "auto", padding: 0 }}
                           checked={excludedSet?.has(row.id) || false}
                           onChange={() => onToggle(table, row.id)} />
                  </td>
                )}
                {cols.map((c) => <td key={c}>{cellText(row[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
