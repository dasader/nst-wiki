// validate.js의 판정 규칙 회귀 방지. 실행: node frontend/app/admin/upload/validate.test.mjs
import assert from "node:assert";
import { validateFile, validateMeta } from "./validate.js";

assert.strictEqual(validateFile({ name: "a.pdf", size: 1000 }), null, "pdf 통과");
assert.strictEqual(validateFile({ name: "A.XLSX", size: 1000 }), null, "대문자 확장자 통과");
assert.ok(validateFile({ name: "a.txt", size: 1000 }), ".txt 거부");
assert.ok(validateFile({ name: "noext", size: 1000 }), "확장자 없음 거부");
assert.ok(validateFile({ name: "big.pdf", size: 60 * 1048576 }), "용량 초과 거부");
assert.strictEqual(validateFile({ name: "edge.pdf", size: 50 * 1048576 }), null, "정확히 한계는 통과");

assert.ok(validateMeta({ publish_date: "" }), "발행 연도 빈값 거부");
assert.ok(validateMeta({ publish_date: "   " }), "공백만 거부");
assert.ok(validateMeta({}), "발행 연도 누락 거부");
assert.strictEqual(validateMeta({ publish_date: "2026" }), null, "연도 있으면 통과");
console.log("ok");
