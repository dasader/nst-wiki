"use client";
import { useEffect, useState } from "react";
import { auditLinks } from "../../links";
import { wikiViewHref as view } from "../../labels";
import Loading from "../../Loading";

export default function WikiAudit() {
  const [st, setSt] = useState({ loading: true });

  useEffect(() => {
    (async () => {
      const list = await fetch("/api/v1/wiki?pages_only=1").then((r) => r.json());
      const valid = new Set(list.pages);
      const results = [];
      // ponytail: 프론트에서 페이지별로 fetch. 페이지 수가 커지면 백엔드 /wiki/audit 엔드포인트로 옮긴다.
      await Promise.all((list.pages || []).map(async (path) => {
        const pg = await fetch(`/api/v1/wiki/page?path=${encodeURIComponent(path)}`)
          .then((r) => (r.ok ? r.json() : null));
        if (!pg) return;
        const broken = auditLinks(pg.content_md, valid);
        if (broken.length) results.push({ path, broken });
      }));
      results.sort((a, b) => a.path.localeCompare(b.path));
      setSt({ loading: false, results, total: (list.pages || []).length });
    })();
  }, []);

  const count = st.results?.reduce((n, r) => n + r.broken.length, 0) ?? 0;

  return (
    <div>
      <p style={{ margin: "0 0 14px" }}><a href="/wiki">← 위키 목록</a></p>
      <span className="eyebrow">링크 무결성</span>
      <h1>링크 전수 감사</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        전체 위키 페이지의 내부 링크(<code>[[…]]</code>)를 실존 페이지·데이터 테이블·컬럼과 대조합니다.
      </p>

      {st.loading ? (
        <Loading label="전체 페이지 검사 중…" />
      ) : count === 0 ? (
        <div className="card"><p className="muted" style={{ margin: 0 }}>
          검사한 {st.total.toLocaleString("ko-KR")}개 페이지에서 깨진 링크가 없습니다. ✓
        </p></div>
      ) : (
        <>
          <div className="row" style={{ margin: "14px 0" }}>
            <span className="chip">{count}개 깨진 링크</span>
            <span className="chip neutral">{st.results.length}개 페이지 · 전체 {st.total}개</span>
          </div>
          {st.results.map((r) => (
            <div className="card" key={r.path}>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 8 }}>
                <a href={view(r.path)} style={{ fontWeight: 600 }}>{r.path}</a>
                <span className="chip neutral">{r.broken.length}</span>
              </div>
              <ul className="list">
                {r.broken.map((b, i) => (
                  <li key={i} style={{ justifyContent: "space-between" }}>
                    <code style={{ fontFamily: "var(--mono)", fontSize: "0.85rem" }}>[[{b.raw}]]</code>
                    <span className={`chip ${b.warn ? "neutral" : ""}`} style={b.warn ? {} : { background: "var(--danger-tint)", color: "var(--danger)" }}>
                      {b.reason}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </div>
  );
}
