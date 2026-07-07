"use client";
import { useState } from "react";
import { adminFetch } from "../adminAuth";

export default function EditPage() {
  const [path, setPath] = useState("");
  const [body, setBody] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);

  async function loadPage() {
    const p = path.trim();
    if (!p) return;
    const res = await fetch(`/api/v1/wiki/page?path=${encodeURIComponent(p)}`);
    setBody(res.ok ? (await res.json()).content_md : "");
    setLoaded(true);
    if (!res.ok) alert("새 페이지 (기존 없음) — 내용을 입력해 저장하세요");
  }

  async function savePage() {
    setBusy(true);
    try {
      const res = await adminFetch("/api/v1/wiki/page", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: path.trim(), content_md: body }),
      });
      alert(`저장: ${res.status} ${(await res.text()).slice(0, 200)}`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <span className="eyebrow">위키 직접 편집</span>
      <h1>위키 페이지 편집</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        경로의 페이지를 불러와 Markdown을 수정합니다. 저장 시 <b>main에 직접 커밋</b>됩니다.
      </p>

      <div className="row" style={{ margin: "18px 0 0" }}>
        <input style={{ flex: 1, minWidth: 200 }} placeholder="tech/hbm.md"
               value={path} onChange={(e) => setPath(e.target.value)}
               onKeyDown={(e) => e.key === "Enter" && loadPage()} />
        <button className="btn-ghost" onClick={loadPage}>불러오기</button>
      </div>

      {loaded && (
        <div style={{ marginTop: 14 }}>
          <p className="card-label" style={{ margin: "0 0 6px" }}>내용 (Markdown)</p>
          <textarea style={{ width: "100%", height: 380, fontFamily: "var(--mono)", fontSize: "0.85rem" }}
                    value={body} onChange={(e) => setBody(e.target.value)} />
          <button style={{ width: "100%", marginTop: 10 }} disabled={busy} onClick={savePage}>
            {busy ? <><span className="spinner" /> 저장 중…</> : "저장 (main에 직접 커밋)"}
          </button>
        </div>
      )}
    </div>
  );
}
