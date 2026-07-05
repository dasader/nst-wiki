// links.js 검증 로직 점검. 실행: node app/links.test.mjs (프레임워크 불필요)
import { linkifyWiki, auditLinks } from "./links.js";

const valid = new Set(["tech/semiconductors.md", "synthesis/ai.md"]);
let fail = 0;
const eq = (label, got, want) => {
  const ok = got === want;
  console.log(ok ? "PASS" : "FAIL", label);
  if (!ok) { console.log(`  got : ${got}\n  want: ${want}`); fail++; }
};

// linkifyWiki
eq("환각 테이블 → dead-data",
  linkifyWiki("[[data:talent_manpower?sector=AI]]", valid),
  "[data:talent_manpower?sector=AI](#dead-data)");
eq("유효 테이블+컬럼 → 필터 딥링크",
  linkifyWiki("[[data:technologies?field=AI]]", valid),
  "[data:technologies?field=AI](/data?table=technologies&column=field&q=AI)");
eq("유효 테이블+없는 컬럼 → 테이블만",
  linkifyWiki("[[data:technologies?sector=AI]]", valid),
  "[data:technologies?sector=AI](/data?table=technologies)");
eq("조건 없는 데이터 링크",
  linkifyWiki("[[data:projects]]", valid),
  "[data:projects](/data?table=projects)");
eq("없는 페이지 → dead-page",
  linkifyWiki("[[policy/national-strategic-technology-plan]]", valid),
  "[policy/national-strategic-technology-plan](#dead-page)");
eq("실존 페이지 → 정상 링크",
  linkifyWiki("[[tech/semiconductors]]", valid),
  "[tech/semiconductors](/wiki/view?path=tech%2Fsemiconductors.md)");
eq("validPaths=null → 낙관적 렌더",
  linkifyWiki("[[nope/x]]", null),
  "[nope/x](/wiki/view?path=nope%2Fx.md)");
eq("한글 값 인코딩",
  linkifyWiki("[[data:technologies?field=반도체]]", valid),
  "[data:technologies?field=반도체](/data?table=technologies&column=field&q=%EB%B0%98%EB%8F%84%EC%B2%B4)");

// auditLinks
const audit = auditLinks(
  "본문 [[data:talent_manpower?sector=AI]] 과 [[data:technologies?sector=AI]] 과 [[tech/semiconductors]] 과 [[policy/x]]",
  valid,
);
eq("감사: 깨진 링크 3건", String(audit.length), "3");
eq("감사: 없는 테이블", audit[0].reason, "없는 테이블: talent_manpower");
eq("감사: 없는 컬럼 warn", `${audit[1].reason}|${audit[1].warn}`, "없는 컬럼: technologies.sector|true");
eq("감사: 없는 페이지", audit[2].reason, "없는 페이지");

if (fail) { console.error(`\n${fail}건 실패`); process.exit(1); }
console.log("\n모두 통과");
