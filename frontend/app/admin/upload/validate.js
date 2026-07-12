// 업로드 파일 판정 규칙 단일 소스. upload/page.js(클라)와 validate.test.mjs가 공유한다.
// 서버 ALLOWED_EXTS와 동일하게 유지할 것.
export const ALLOWED_EXTS = [".pdf", ".md", ".xlsx"];
export const MAX_MB = 50; // ponytail: 개인용 기본값, 서버가 거부하면 상향

export function validateFile(file, maxMB = MAX_MB) {
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if (!ALLOWED_EXTS.includes(ext)) return `지원하지 않는 형식: ${ext || "(없음)"}`;
  if (file.size > maxMB * 1024 * 1024)
    return `용량 초과: ${(file.size / 1048576).toFixed(1)}MB > ${maxMB}MB`;
  return null;
}

// 업로드 전 메타 필드 검증. 서버(ingest_api)와 동일하게 publish_date를 의무화한다 —
// 시점 정합성(연도 기준 병합·모순 판정)의 전제.
export function validateMeta(item) {
  if (!String(item?.publish_date || "").trim()) return "발행 연도는 필수입니다";
  return null;
}
