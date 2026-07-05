// 위키 [[...]] 링크의 변환·유효성 검사 단일 소스. 렌더(wiki/view)와 감사(wiki/audit)가 공유한다.
// 대상이 실존하지 않으면 #dead-page/#dead-data 센티넬 href로 바꿔 Markdown이 비활성 표식으로 렌더한다.
import { TABLE_LABELS, COLUMNS } from "./labels.js";

const VALID_TABLES = new Set(Object.keys(TABLE_LABELS));
const COND_RE = /^\s*([\w가-힣]+)\s*[=:]\s*(.+?)\s*$/;   // 조건 "컬럼=값" / "컬럼:값" 하나만 해석

function splitData(ref) {
  const i = ref.indexOf("?");
  return i < 0 ? [ref.trim(), ""] : [ref.slice(0, i).trim(), ref.slice(i + 1)];
}

// [[data:테이블?조건]] → /data 딥링크. 조건이 유효 컬럼이면 필터까지, 아니면 테이블만.
function dataHref(table, cond) {
  const p = new URLSearchParams({ table });
  const m = cond.match(COND_RE);
  if (m && (COLUMNS[table] || []).includes(m[1])) {
    p.set("column", m[1]);
    p.set("q", m[2]);
  }
  return `/data?${p}`;
}

// validPaths: 실존 페이지 경로 Set(예: "tech/foo.md"). null이면 아직 미로딩 → 페이지 링크는 낙관적으로 렌더.
export function linkifyWiki(md, validPaths) {
  return md
    .replace(/\[\[data:([^\]]+)\]\]/g, (_, ref) => {
      const [table, cond] = splitData(ref);
      const label = `data:${ref}`;
      return VALID_TABLES.has(table)
        ? `[${label}](${dataHref(table, cond)})`
        : `[${label}](#dead-data)`;
    })
    .replace(/\[\[([^\]:]+)\]\]/g, (_, p) => {
      const path = p.endsWith(".md") ? p : p + ".md";
      return validPaths && !validPaths.has(path)
        ? `[${p}](#dead-page)`
        : `[${p}](/wiki/view?path=${encodeURIComponent(path)})`;
    });
}

// 한 페이지의 깨진 링크 목록(감사용). 잘못된 컬럼은 warn으로 구분.
export function auditLinks(md, validPaths) {
  const out = [];
  let m;
  const dataRe = /\[\[data:([^\]]+)\]\]/g;
  while ((m = dataRe.exec(md))) {
    const [table, cond] = splitData(m[1]);
    if (!VALID_TABLES.has(table)) {
      out.push({ raw: `data:${m[1]}`, kind: "data", reason: `없는 테이블: ${table}` });
      continue;
    }
    const cm = cond.match(COND_RE);
    if (cm && !(COLUMNS[table] || []).includes(cm[1]))
      out.push({ raw: `data:${m[1]}`, kind: "data", reason: `없는 컬럼: ${table}.${cm[1]}`, warn: true });
  }
  const pageRe = /\[\[([^\]:]+)\]\]/g;
  while ((m = pageRe.exec(md))) {
    const path = m[1].endsWith(".md") ? m[1] : m[1] + ".md";
    if (validPaths && !validPaths.has(path))
      out.push({ raw: m[1], kind: "page", reason: "없는 페이지" });
  }
  return out;
}
