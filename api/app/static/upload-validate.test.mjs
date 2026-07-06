// dashboard.html의 validateFile과 동일 로직 — 판정 규칙 회귀 방지.
import assert from "node:assert";
const ALLOWED_EXTS = [".pdf", ".md", ".xlsx"];
function validateFile(file, maxMB = 50) {
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if (!ALLOWED_EXTS.includes(ext)) return `지원하지 않는 형식: ${ext || "(없음)"}`;
  if (file.size > maxMB * 1024 * 1024) return `용량 초과: ${(file.size / 1048576).toFixed(1)}MB > ${maxMB}MB`;
  return null;
}
assert.strictEqual(validateFile({ name: "a.pdf", size: 1000 }), null, "pdf 통과");
assert.strictEqual(validateFile({ name: "A.XLSX", size: 1000 }), null, "대문자 확장자 통과");
assert.ok(validateFile({ name: "a.txt", size: 1000 }), ".txt 거부");
assert.ok(validateFile({ name: "noext", size: 1000 }), "확장자 없음 거부");
assert.ok(validateFile({ name: "big.pdf", size: 60 * 1048576 }), "용량 초과 거부");
assert.strictEqual(validateFile({ name: "edge.pdf", size: 50 * 1048576 }), null, "정확히 한계는 통과");
console.log("ok");
