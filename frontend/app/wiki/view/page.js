"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Markdown from "../../Markdown";
import Loading from "../../Loading";
import { pageSlug } from "../../labels";
import { linkifyWiki } from "../../links";

function splitFrontmatter(md) {
  const m = md.match(/^---\n([\s\S]*?)\n---\n?/);
  return m ? { front: m[1], body: md.slice(m[0].length) } : { front: null, body: md };
}

// 프론트매터에서 표시에 쓸 값만 얕게 추출 (본격 YAML 파싱 불필요)
const field = (front, key) => front?.match(new RegExp(`^${key}:\\s*(.+)$`, "m"))?.[1].trim().replace(/^["']|["']$/g, "");

const TYPE_LABELS = {
  tech_concept: "기술 개념", policy_entity: "정책 엔티티", policy_event: "정책 변화",
  synthesis: "종합 분석", source_summary: "소스 요약",
};

function Viewer() {
  const params = useSearchParams();
  const path = params.get("path");
  const fromQ = params.get("q");   // 검색에서 왔으면 그 검색으로 되돌아간다
  const backHref = fromQ ? `/wiki?q=${encodeURIComponent(fromQ)}` : "/wiki";
  const backLabel = fromQ ? `← ‘${fromQ}’ 검색 결과로` : "← 위키 목록";
  const [page, setPage] = useState(null);
  const [err, setErr] = useState(null);
  const [validPaths, setValidPaths] = useState(null);   // 링크 유효성 검사용 실존 페이지 Set

  useEffect(() => {
    fetch("/api/v1/wiki?pages_only=1")   // 링크 검증엔 경로 목록만 필요 (제목 불필요)
      .then((r) => (r.ok ? r.json() : null))
      .then((b) => b && setValidPaths(new Set(b.pages)));
  }, []);

  useEffect(() => {
    if (!path) return;
    setPage(null); setErr(null);
    fetch(`/api/v1/wiki/page?path=${encodeURIComponent(path)}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.status)))
      .then(setPage, (e) => setErr(`페이지를 찾을 수 없습니다 (${e})`));
  }, [path]);

  if (err) return <div className="card error">{err}</div>;
  if (!page) return <Loading />;

  const { front, body } = splitFrontmatter(page.content_md);
  const title = field(front, "title") || pageSlug(page.path);
  const type = field(front, "type");
  const nextField = field(front, "next_field");

  return (
    <div>
      <p style={{ margin: "0 0 14px" }}><a href={backHref}>{backLabel}</a></p>

      <div className="row" style={{ gap: 8, marginBottom: 6 }}>
        {type && <span className="chip">{TYPE_LABELS[type] || type}</span>}
        {nextField && <span className="chip neutral">분야 · {nextField}</span>}
      </div>
      <h1 style={{ marginBottom: 4 }}>{title}</h1>
      <p className="muted" style={{ margin: "0 0 20px", fontSize: "0.85rem", fontFamily: "var(--mono)" }}>{page.path}</p>

      <div className="card">
        <Markdown>{linkifyWiki(body, validPaths)}</Markdown>
      </div>

      {front && (
        <details className="card reveal">
          <summary>메타데이터 (프론트매터)</summary>
          <pre className="code" style={{ marginTop: 10 }}>{front}</pre>
        </details>
      )}

      <div className="card">
        <div className="card-label" style={{ marginBottom: 10 }}>변경 이력</div>
        <ul className="timeline">
          {page.history.map((h) => (
            <li key={h.hash}>
              <span className="hash">{h.hash.slice(0, 7)}</span>
              <span>{h.subject}</span>
              <span className="date" style={{ marginLeft: "auto" }}>{h.date}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default function WikiView() {
  return <Suspense fallback={<Loading />}><Viewer /></Suspense>;
}
