# 대시보드 문서 업로드 UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 관리 대시보드(`dashboard.html`)에 여러 파일을 드래그-드롭/선택으로 올리고 파일별 메타데이터·전송 진행률을 다루는 업로드 패널을 추가한다.

**Architecture:** 순수 HTML/JS 단일 파일에 업로드 섹션을 덧붙인다. 백엔드 변경 없음 — 전부 기존 `POST /api/v1/ingest`로 전송. 사전검증 순수 함수 하나만 자체검증 테스트를 갖고, DOM/전송은 수동 확인.

**Tech Stack:** Vanilla JS, `XMLHttpRequest`(업로드 진행률), `FormData`, 네이티브 `<input type="file">` 드래그-드롭. 빌드 스텝·프레임워크 없음.

## Global Constraints

- 대상 파일: `api/app/static/dashboard.html` 단일 파일 (스펙 "목표").
- 백엔드·API 변경 금지 (스펙 "비목표").
- 확장자 화이트리스트 `.pdf`/`.md`/`.xlsx` — 서버 `ALLOWED_EXTS`와 동일 (스펙 "클라이언트 사전검증").
- `MAX_MB` 초깃값 50, `// ponytail: 개인용 기본값, 서버가 거부하면 상향` 주석 필수.
- 전송은 순차, `// ponytail: 순차, 배치 커지면 병렬화` 주석 필수.
- silent drop 금지 — 검증 실패 행은 사유를 상태 칸에 표시.
- admin key는 기존 `#adminkey` 입력값 재사용.
- 전송 필드: `file`, `title`, `publisher`, `publish_date`, `tags`, `force`. `source_type`은 폼에 노출 안 함(서버 기본값).

---

### Task 1: 사전검증 순수 함수 + 자체검증

파일 검증 판정을 DOM과 분리된 순수 함수로 만들고 인라인 `assert`로 검증한다. 이 함수만 유일하게 테스트 대상.

**Files:**
- Modify: `api/app/static/dashboard.html` (`<script>` 안에 함수 추가)
- Test: `api/app/static/upload-validate.test.mjs` (Create) — Node로 실행하는 자체검증

**Interfaces:**
- Produces: `validateFile({name, size}, maxMB)` → `null`(통과) 또는 사유 문자열. 확장자·용량 판정. 뒤 Task들이 이 함수로 행 유효성을 가른다.

- [ ] **Step 1: 검증 함수와 상수를 `export` 가능한 형태로 정의**

`dashboard.html`의 `<script>` 상단(`const $ = ...` 근처)에 추가. 브라우저 인라인 스크립트지만, 같은 로직을 `.test.mjs`가 재선언해 검증하므로 로직을 정확히 일치시킨다.

```javascript
const ALLOWED_EXTS = [".pdf", ".md", ".xlsx"];   // 서버 ALLOWED_EXTS와 동일
const MAX_MB = 50;   // ponytail: 개인용 기본값, 서버가 거부하면 상향
function validateFile(file, maxMB = MAX_MB) {
  const ext = (file.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
  if (!ALLOWED_EXTS.includes(ext)) return `지원하지 않는 형식: ${ext || "(없음)"}`;
  if (file.size > maxMB * 1024 * 1024) return `용량 초과: ${(file.size / 1048576).toFixed(1)}MB > ${maxMB}MB`;
  return null;
}
```

- [ ] **Step 2: 자체검증 테스트 작성 (실패 확인용)**

`api/app/static/upload-validate.test.mjs` 생성. `dashboard.html`에 아직 함수가 없어도 이 파일은 로직을 복제해 검증한다 — 목적은 판정 규칙이 맞는지 고정하는 것.

```javascript
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
```

- [ ] **Step 3: 테스트 실행**

Run: `node api/app/static/upload-validate.test.mjs`
Expected: `ok` 출력, 종료코드 0. (Step 1의 dashboard.html 로직과 문자열까지 일치하는지 눈으로 대조.)

- [ ] **Step 4: 커밋**

```bash
git add api/app/static/dashboard.html api/app/static/upload-validate.test.mjs
git commit -m "feat: 업로드 파일 사전검증 함수 + 자체검증"
```

---

### Task 2: 업로드 패널 UI(드롭존·행 목록)

드롭존과 파일 행 목록을 렌더한다. 아직 전송은 안 함 — 파일을 받아 행으로 쌓고 검증 상태를 보이는 데까지.

**Files:**
- Modify: `api/app/static/dashboard.html` (HTML: `#editor` `<details>` 다음에 업로드 섹션; CSS: 드롭존 스타일; JS: 파일 수집·행 렌더)

**Interfaces:**
- Consumes: `validateFile` (Task 1).
- Produces: `uploadQueue`(배열, 각 원소 `{file, id, statusEl}`), `addFiles(fileList)`, `renderRow(item)`. Task 3이 `uploadQueue`를 순회해 전송한다.

- [ ] **Step 1: HTML 섹션 추가**

`dashboard.html`에서 `#editor` `<details>`(line 30-36) 닫는 `</details>` **다음**, `<div id="tasks">` 앞에 삽입:

```html
  <details id="uploader" open>
    <summary>문서 업로드</summary>
    <div id="dropzone">파일을 여기로 끌어다 놓거나 클릭해 선택
      <input id="filepick" type="file" multiple accept=".pdf,.md,.xlsx" hidden></div>
    <div id="uprows"></div>
    <p id="upctl" hidden>
      <label style="font-size:12px"><input id="force" type="checkbox"> 덮어쓰기(force)</label><br>
      <button id="uploadbtn" onclick="startUpload()" style="background:#2a7d4f;color:#fff;border:0;margin-top:6px">전체 업로드</button>
    </p>
  </details>
```

- [ ] **Step 2: CSS 추가**

`<style>` 블록 안(line 24 `.contradiction` 다음)에 삽입:

```css
  #dropzone { border: 2px dashed #bbb; border-radius: 6px; padding: 18px; text-align: center; color: #666; cursor: pointer; font-size: 13px; margin: 6px 0; }
  #dropzone.drag { border-color: #2a7d4f; background: #f0f8f2; }
  .uprow { border-bottom: 1px solid #eee; padding: 6px 0; font-size: 12px; }
  .uprow input { width: 100%; box-sizing: border-box; margin: 1px 0; padding: 2px 4px; }
  .uprow .st { color: #555; }
  .uprow .st.err { color: #b3403a; } .uprow .st.ok { color: #2a7d4f; }
  .uprow .rm { float: right; cursor: pointer; color: #b3403a; border: 0; background: none; font-size: 14px; }
```

- [ ] **Step 3: JS — 파일 수집·행 렌더**

`<script>` 안, `validateFile`(Task 1) 다음에 삽입:

```javascript
const uploadQueue = [];
let upSeq = 0;
const dz = $("#dropzone"), pick = $("#filepick");
dz.onclick = () => pick.click();
pick.onchange = () => { addFiles(pick.files); pick.value = ""; };
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("drag"); };
dz.ondragleave = () => dz.classList.remove("drag");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("drag"); addFiles(e.dataTransfer.files); };

function addFiles(fileList) {
  for (const file of fileList) {
    const item = { file, id: ++upSeq };
    uploadQueue.push(item);
    renderRow(item);
  }
  $("#upctl").hidden = uploadQueue.length === 0;
}

function baseName(name) { return name.replace(/\.[^.]+$/, ""); }

function renderRow(item) {
  const err = validateFile(item.file);
  const row = document.createElement("div");
  row.className = "uprow";
  row.id = `uprow-${item.id}`;
  row.innerHTML = `
    <button class="rm" title="제거" onclick="removeRow(${item.id})">✕</button>
    <b>${escapeHtml(item.file.name)}</b> <small>(${(item.file.size/1048576).toFixed(1)}MB)</small>
    <input data-f="title" placeholder="제목" value="${escapeHtml(baseName(item.file.name))}">
    <input data-f="publisher" placeholder="발행기관">
    <input data-f="tags" placeholder="태그(쉼표구분)">
    <input data-f="publish_date" placeholder="발행일 예: 2026">
    <div class="st ${err ? "err" : ""}">${err ? escapeHtml(err) : "대기"}</div>`;
  $("#uprows").appendChild(row);
  item.statusEl = row.querySelector(".st");
  item.invalid = !!err;
}

function removeRow(id) {
  const i = uploadQueue.findIndex(x => x.id === id);
  if (i >= 0) uploadQueue.splice(i, 1);
  document.getElementById(`uprow-${id}`)?.remove();
  $("#upctl").hidden = uploadQueue.length === 0;
}
```

- [ ] **Step 4: 수동 확인**

대시보드를 브라우저로 열고(`docker compose up` 후 `http://localhost:8000/`), 파일을 드롭/선택. 확인:
- PDF/MD/XLSX → 각 행에 제목 기본값(파일명), 상태 "대기".
- `.txt` → 상태 칸 빨강 "지원하지 않는 형식".
- ✕ 버튼 → 행 제거, 목록 비면 업로드 컨트롤 숨김.

- [ ] **Step 5: 커밋**

```bash
git add api/app/static/dashboard.html
git commit -m "feat: 업로드 드롭존·파일 행 목록 UI"
```

---

### Task 3: 순차 전송·진행률·결과 처리

행별로 `XMLHttpRequest`로 전송하며 진행률(%)을 갱신하고, 성공/409/오류를 상태 칸에 반영한다.

**Files:**
- Modify: `api/app/static/dashboard.html` (JS: `startUpload`, `uploadOne`)

**Interfaces:**
- Consumes: `uploadQueue`, `item.statusEl`, `item.invalid`(Task 2), `keyInput.value`, `loadList`, `escapeHtml`(기존).
- Produces: 없음(최종 동작).

- [ ] **Step 1: JS — 전송 함수 추가**

`<script>` 안 Task 2 코드 다음에 삽입:

```javascript
function startUpload() {
  if (!keyInput.value) return alert("Admin Key를 먼저 입력하세요.");
  uploadSequential(uploadQueue.filter(x => !x.invalid && !x.done));
}

// ponytail: 순차, 배치 커지면 병렬화
async function uploadSequential(items) {
  $("#uploadbtn").disabled = true;
  try { for (const item of items) await uploadOne(item); }
  finally { $("#uploadbtn").disabled = false; await loadList(); }
}

function uploadOne(item) {
  return new Promise((resolve) => {
    const row = document.getElementById(`uprow-${item.id}`);
    const fd = new FormData();
    fd.append("file", item.file);
    for (const inp of row.querySelectorAll("input[data-f]")) fd.append(inp.dataset.f, inp.value);
    fd.append("force", $("#force").checked ? "true" : "false");
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/v1/ingest");
    xhr.setRequestHeader("X-Admin-Key", keyInput.value);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) item.statusEl.textContent = `업로드중 ${Math.round(e.loaded / e.total * 100)}%`;
    };
    xhr.onload = () => {
      if (xhr.status === 200) {
        const tid = (JSON.parse(xhr.responseText).task_id || "").slice(0, 8);
        item.statusEl.className = "st ok";
        item.statusEl.textContent = `✓ 큐등록 (task ${tid})`;
        item.done = true;
      } else {
        item.statusEl.className = "st err";
        item.statusEl.textContent = `✗ ${xhr.status} ${xhr.responseText.slice(0, 200)}`;
      }
      resolve();
    };
    xhr.onerror = () => { item.statusEl.className = "st err"; item.statusEl.textContent = "✗ 네트워크 오류"; resolve(); };
    item.statusEl.className = "st";
    xhr.send(fd);
  });
}
```

- [ ] **Step 2: 수동 확인 — 정상 업로드**

대시보드에서 PDF 1개 드롭 → 제목/발행기관 채우고 "전체 업로드". 확인:
- 상태 "업로드중 nn%" → "✓ 큐등록 (task xxxxxxxx)".
- 왼쪽 태스크 목록에 새 태스크가 뜨고 배지가 queued→…로 진행(15초 자동새로고침).

- [ ] **Step 3: 수동 확인 — 중복·오류**

- 같은 파일 다시 업로드 → "✗ 409 …이미 인제스트…" 표시.
- 상단 "덮어쓰기(force)" 체크 후 재업로드 → 200 큐등록.
- Admin Key 비우고 업로드 시도 → alert 경고.

- [ ] **Step 4: 커밋**

```bash
git add api/app/static/dashboard.html
git commit -m "feat: 순차 업로드 전송·진행률·409 force 처리"
```

---

## Self-Review 결과

**Spec coverage:** 드롭존·다중선택(Task 2) / 파일별 메타데이터 행(Task 2) / 전송 진행률(Task 3) / 이후 상태는 기존 목록(Task 3 Step 2 확인) / 사전검증(Task 1) / 제거·교체(Task 2 removeRow) / 409 force(Task 3) — 전부 매핑됨.

**Placeholder scan:** 코드 스텝 전부 실제 코드 포함, TBD 없음.

**Type consistency:** `validateFile`·`uploadQueue`·`item.statusEl`·`item.invalid`·`item.done`·`renderRow`·`removeRow`·`addFiles`·`uploadOne` 이름이 Task 간 일치. `escapeHtml`·`keyInput`·`loadList`·`$`는 기존 dashboard.html 심볼(재사용).
