"use client";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { getKey, setKey, clearKey, verifyKey } from "./adminAuth";

const TABS = [
  { href: "/admin", label: "승인 대기" },
  { href: "/admin/upload", label: "문서 업로드" },
  { href: "/admin/edit", label: "위키 편집" },
];

export default function AdminLayout({ children }) {
  const path = usePathname();
  const [authed, setAuthed] = useState(null); // null=확인중 · false=게이트 · true=통과
  const [key, setKeyInput] = useState("");
  const [msg, setMsg] = useState("");
  const [checking, setChecking] = useState(false);

  // 저장된 키가 유효하면 바로 통과, 아니면 게이트
  useEffect(() => {
    (async () => {
      const saved = getKey();
      setAuthed(saved && (await verifyKey(saved)) ? true : false);
    })();
  }, []);

  async function submit(e) {
    e.preventDefault();
    const k = key.trim();
    if (!k) return;
    setChecking(true);
    setMsg("");
    if (await verifyKey(k)) {
      setKey(k);
      setAuthed(true);
    } else {
      setMsg("키가 올바르지 않습니다.");
    }
    setChecking(false);
  }

  function lock() {
    clearKey();
    setAuthed(false);
    setKeyInput("");
  }

  if (authed === null)
    return (
      <div className="empty-state">
        <span className="spinner" /> 확인 중…
      </div>
    );

  if (!authed)
    return (
      <div className="gate card">
        <span className="eyebrow">관리자 인증</span>
        <h1 style={{ marginBottom: 4 }}>관리 콘솔</h1>
        <p className="subtle" style={{ marginTop: 0 }}>승인·업로드·편집 권한이 필요한 화면입니다.</p>
        <form onSubmit={submit} className="stack" style={{ gap: 10, marginTop: 18 }}>
          <input
            type="password"
            autoFocus
            autoComplete="off"
            placeholder="관리자 키를 입력하세요"
            value={key}
            onChange={(e) => setKeyInput(e.target.value)}
          />
          <button disabled={checking}>
            {checking ? (
              <>
                <span className="spinner" /> 확인 중
              </>
            ) : (
              "진입"
            )}
          </button>
          {msg && <p style={{ color: "var(--danger)", fontSize: "0.85rem", margin: 0 }}>{msg}</p>}
        </form>
        <p className="muted" style={{ fontSize: "0.8rem", margin: "16px 0 0" }}>
          키는 이 브라우저에만 저장됩니다.
        </p>
      </div>
    );

  return (
    <div>
      <div className="admin-bar">
        <nav className="admin-tabs">
          {TABS.map((t) => (
            <a key={t.href} href={t.href} className={path === t.href ? "active" : ""}>
              {t.label}
            </a>
          ))}
        </nav>
        <button className="btn-ghost btn-sm lock" onClick={lock}>
          잠금
        </button>
      </div>
      {children}
    </div>
  );
}
