"use client";
import { useState } from "react";
import { verifyKey } from "../adminAuth";

// 초기화로 사라지는 것 — 백엔드 POST /api/v1/admin/reset과 순서를 맞춰 적는다.
const WIPED = [
  ["위키 저장소", "모든 페이지와 git 이력 (되돌릴 수 없음)"],
  ["정형 데이터", "기술·과제·예산·정책이벤트 + 승인 대기 중인 staging 행"],
  ["인제스트 태스크", "업로드 이력과 승인 대기 큐 전체"],
  ["업로드 원본", "서버에 보관된 원본 PDF·XLSX·MD 파일"],
  ["벡터 색인", "Qdrant의 위키 임베딩"],
  ["LLM 비용 기록", "지금까지 누적된 토큰 사용량·지출 이력"],
];

export default function ResetPage() {
  const [key, setKey] = useState("");
  const [verified, setVerified] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [blocked, setBlocked] = useState("");   // 409 사유 — 나오면 강제 옵션 노출
  const [done, setDone] = useState(null);

  async function check(e) {
    e.preventDefault();
    setMsg("");
    if (!key.trim()) return;
    setBusy(true);
    const ok = await verifyKey(key.trim());
    setVerified(ok);
    setMsg(ok ? "" : "키가 올바르지 않습니다.");
    setBusy(false);
  }

  async function reset(force) {
    if (!verified) return;
    setBusy(true);
    setMsg("");
    setBlocked("");
    try {
      const r = await fetch("/api/v1/admin/reset", {
        method: "POST",
        headers: { "X-Admin-Key": key.trim(), "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      });
      const body = await r.json().catch(() => ({}));
      if (r.status === 409) setBlocked(body.detail || "처리 중인 태스크가 있습니다.");
      else if (!r.ok) setMsg(`실패: ${r.status} ${body.detail || ""}`);
      else {
        setDone(body);
        setVerified(false);   // 재실행하려면 키를 다시 넣어야 한다
        setKey("");
      }
    } catch {
      setMsg("네트워크 오류");
    }
    setBusy(false);
  }

  if (done) {
    const rows = Object.entries(done.db || {}).filter(([, n]) => n > 0);
    return (
      <div>
        <span className="eyebrow">위험 구역</span>
        <h1>초기화 완료</h1>
        <div className="card">
          <p>위키·데이터베이스·업로드 원본·벡터 색인이 새 배포 상태로 되돌아갔습니다.</p>
          <p className="muted" style={{ fontSize: "0.85rem" }}>
            삭제된 원본 {done.sources_removed}건
            {rows.length > 0 && ` · ${rows.map(([t, n]) => `${t} ${n}행`).join(", ")}`}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <span className="eyebrow">위험 구역</span>
      <h1>전체 초기화</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        시스템을 방금 배포한 상태로 되돌립니다. <b>되돌릴 수 없습니다.</b>
      </p>

      <div className="card error" style={{ marginTop: 18 }}>
        <div className="card-label" style={{ color: "var(--danger)" }}>삭제되는 항목</div>
        <ul style={{ margin: "10px 0 0", paddingLeft: 18 }}>
          {WIPED.map(([what, detail]) => (
            <li key={what} style={{ marginBottom: 4 }}>
              <b>{what}</b> — {detail}
            </li>
          ))}
        </ul>
        <p style={{ marginBottom: 0, fontSize: "0.85rem" }}>
          DB 스키마와 부처 시드 목록은 보존됩니다.
        </p>
      </div>

      <form className="card" onSubmit={check} style={{ marginTop: 14 }}>
        <div className="card-label">확인을 위해 관리자 키를 다시 입력하세요</div>
        <div className="stack" style={{ marginTop: 10 }}>
          <input
            type="password" value={key} placeholder="관리자 키" autoComplete="off"
            onChange={(e) => { setKey(e.target.value); setVerified(false); }}
          />
          <button type="submit" className="btn-ghost" disabled={busy || !key.trim()}>
            키 확인
          </button>
        </div>
        {verified && (
          <p className="muted" style={{ margin: "8px 0 0", fontSize: "0.85rem" }}>
            ✓ 키 확인됨 — 아래 버튼이 활성화되었습니다.
          </p>
        )}
        {msg && <p style={{ margin: "8px 0 0", color: "var(--danger)" }}>{msg}</p>}
      </form>

      {blocked && (
        <div className="card error" style={{ marginTop: 14 }}>
          <b>초기화가 거부되었습니다.</b>
          <p style={{ marginBottom: 8 }}>{blocked}</p>
          <button className="btn-danger" disabled={busy || !verified} onClick={() => reset(true)}>
            {busy ? <><span className="spinner" /> 초기화 중…</> : "무시하고 강제 초기화"}
          </button>
        </div>
      )}

      <button
        className="btn-danger"
        style={{ width: "100%", marginTop: 14 }}
        disabled={!verified || busy}
        onClick={() => reset(false)}
      >
        {busy ? <><span className="spinner" /> 초기화 중…</> : "전체 초기화 — 되돌릴 수 없음"}
      </button>
    </div>
  );
}
