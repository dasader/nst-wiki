"use client";
import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { WIKI_DIRS, pageSlug as slug, wikiViewHref as view } from "../labels";
import Loading from "../Loading";

const dirOf = (p) => p.split("/")[0];

// 검색어 낱말(연산자·따옴표 제외)을 스니펫에서 하이라이트
function highlight(text, query) {
  const terms = (query.match(/"[^"]+"|\S+/g) || [])
    .map((t) => t.replace(/^"|"$/g, ""))
    .filter((t) => t && t !== "|" && t.toUpperCase() !== "OR")
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  if (!terms.length) return text;
  const re = new RegExp(`(${terms.join("|")})`, "gi");
  return text.split(re).map((part, i) =>
    re.test(part) ? <mark key={i}>{part}</mark> : part);
}

function WikiListInner() {
  const router = useRouter();
  const urlQ = useSearchParams().get("q") || "";
  const [pages, setPages] = useState([]);
  const [titles, setTitles] = useState({});
  const [input, setInput] = useState(urlQ);
  const [hits, setHits] = useState(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    fetch("/api/v1/wiki").then((r) => r.json()).then((b) => {
      setPages(b.pages || []);
      setTitles(b.titles || {});
    });
  }, []);

  // URL의 q가 바뀌면(직접 방문·뒤로가기 포함) 그 검색을 재현한다
  useEffect(() => {
    setInput(urlQ);
    if (!urlQ) { setHits(null); return; }
    setSearching(true);
    fetch(`/api/v1/wiki/search?q=${encodeURIComponent(urlQ)}`)
      .then((r) => r.json())
      .then((b) => setHits(b.results || []))
      .finally(() => setSearching(false));
  }, [urlQ]);

  const titleOf = (p, fallbackTitle) => fallbackTitle || titles[p] || slug(p);

  function submit(e) {
    e.preventDefault();
    const q = input.trim();
    router.push(q ? `/wiki?q=${encodeURIComponent(q)}` : "/wiki");
  }
  function clearSearch() { router.push("/wiki"); }

  const groups = {};
  for (const p of pages) (groups[dirOf(p)] ||= []).push(p);
  const order = Object.keys(WIKI_DIRS).filter((d) => groups[d])
    .concat(Object.keys(groups).filter((d) => !WIKI_DIRS[d]));

  return (
    <div>
      <span className="eyebrow">지식 위키</span>
      <h1>위키 브라우저</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        국가전략기술 정책 지식이 주제별로 정리된 문서입니다. 승인된 페이지만 표시됩니다.
      </p>

      <form className="row" onSubmit={submit} style={{ margin: "18px 0 4px" }}>
        <input style={{ flex: 1, minWidth: 200 }} value={input} onChange={(e) => setInput(e.target.value)}
               placeholder="문서 전문 검색" aria-label="위키 검색" />
        <button>검색</button>
        {urlQ && <button type="button" className="btn-ghost" onClick={clearSearch}>지우기</button>}
      </form>
      <p className="search-hint">
        <b>공백</b> = 모두 포함(AND) · <b>|</b> = 둘 중 하나(OR) · <b>&quot;따옴표&quot;</b> = 정확한 구문
        {" · "}<a href="/wiki/audit">링크 감사</a>
      </p>

      {urlQ && (
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", marginBottom: hits?.length ? 10 : 0 }}>
            <div className="card-label">
              {searching ? <><span className="spinner" /> 검색 중…</>
                         : <>‘{urlQ}’ 검색 결과 {hits?.length ?? 0}건</>}
            </div>
            <button type="button" className="btn-ghost btn-sm" onClick={clearSearch}>← 전체 목록</button>
          </div>
          {!searching && hits?.length === 0 && (
            <p className="muted" style={{ margin: 0 }}>일치하는 문서가 없습니다. 낱말을 줄이거나 <b>|</b>(OR)로 넓혀 보세요.</p>
          )}
          {hits?.length > 0 && (
            <ul className="list">
              {hits.map((h, i) => (
                <li key={i}>
                  <a href={view(h.path, urlQ)} style={{ flexDirection: "column", alignItems: "flex-start", gap: 3 }}>
                    <span style={{ fontWeight: 600 }}>{titleOf(h.path, h.title)}</span>
                    <span className="result-snippet">{highlight(h.line, urlQ)}</span>
                    <span className="muted" style={{ fontSize: "0.75rem", fontFamily: "var(--mono)" }}>{h.path}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {!urlQ && pages.length === 0 && <div className="empty-state">아직 등록된 문서가 없습니다.</div>}

      {!urlQ && order.map((dir) => {
        const info = WIKI_DIRS[dir] || { label: dir, hint: "" };
        return (
          <div className="card" key={dir}>
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 6 }}>
              <h3 style={{ margin: 0 }}>{info.label}</h3>
              <span className="chip neutral">{groups[dir].length}</span>
            </div>
            {info.hint && <p className="muted" style={{ margin: "0 0 6px", fontSize: "0.85rem" }}>{info.hint}</p>}
            <ul className="list">
              {groups[dir].map((p) => (
                <li key={p}>
                  <a href={view(p)}>
                    <span>{titleOf(p)}</span>
                    <span className="meta">{slug(p)}</span>
                  </a>
                </li>
              ))}
            </ul>
          </div>
        );
      })}
    </div>
  );
}

export default function WikiList() {
  return <Suspense fallback={<Loading />}><WikiListInner /></Suspense>;
}
