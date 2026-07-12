"use client";
import { useRef, useState } from "react";
import { getKey } from "../adminAuth";
import { MAX_MB, validateFile, validateMeta } from "./validate";

const baseName = (name) => name.replace(/\.[^.]+$/, "");

let seq = 0;

export default function UploadPage() {
  const [items, setItems] = useState([]); // {id,file,invalid,title,publisher,tags,publish_date,st,ok,err,done}
  const [force, setForce] = useState(false);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const pick = useRef(null);

  function addFiles(fileList) {
    const next = [...fileList].map((file) => {
      const problem = validateFile(file);
      return {
        id: ++seq, file, invalid: !!problem,
        title: baseName(file.name), publisher: "", tags: "", publish_date: "",
        st: problem || "대기", err: !!problem, ok: false, done: false,
      };
    });
    setItems((prev) => [...prev, ...next]);
  }
  const patch = (id, fields) =>
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...fields } : it)));
  const remove = (id) => setItems((prev) => prev.filter((it) => it.id !== id));

  function uploadOne(item) {
    return new Promise((resolve) => {
      const fd = new FormData();
      fd.append("file", item.file);
      for (const f of ["title", "publisher", "tags", "publish_date"]) fd.append(f, item[f]);
      fd.append("force", force ? "true" : "false");
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/ingest");
      xhr.setRequestHeader("X-Admin-Key", getKey());
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable)
          patch(item.id, { st: `업로드중 ${Math.round((e.loaded / e.total) * 100)}%`, err: false, ok: false });
      };
      xhr.onload = () => {
        if (xhr.status === 200) {
          let tid = "";
          try { tid = (JSON.parse(xhr.responseText).task_id || "").slice(0, 8); } catch {}
          patch(item.id, { st: `✓ 큐등록 (task ${tid})`, ok: true, err: false, done: true });
        } else {
          patch(item.id, { st: `✗ ${xhr.status} ${xhr.responseText.slice(0, 200)}`, err: true, ok: false });
        }
        resolve();
      };
      xhr.onerror = () => { patch(item.id, { st: "✗ 네트워크 오류", err: true, ok: false }); resolve(); };
      xhr.send(fd);
    });
  }

  // ponytail: 순차, 배치 커지면 병렬화
  async function startUpload() {
    if (!getKey()) return alert("관리자 키가 없습니다. 잠금 해제 후 다시 시도하세요.");
    setBusy(true);
    try {
      for (const item of items.filter((it) => !it.invalid && !it.done)) {
        const problem = validateMeta(item);
        if (problem) { patch(item.id, { st: `✗ ${problem}`, err: true, ok: false }); continue; }
        await uploadOne(item);
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <span className="eyebrow">문서 인제스트</span>
      <h1>문서 업로드</h1>
      <p className="subtle" style={{ marginTop: 0 }}>
        PDF·Markdown·XLSX를 업로드하면 파싱·분류 후 승인 대기 큐에 등록됩니다.
      </p>

      <div
        className={`dropzone${drag ? " drag" : ""}`}
        style={{ marginTop: 18 }}
        onClick={() => pick.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
      >
        파일을 여기로 끌어다 놓거나 클릭해 선택
        <input ref={pick} type="file" multiple accept=".pdf,.md,.xlsx" hidden
               onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
      </div>
      <p className="muted" style={{ margin: "8px 2px 0", fontSize: "0.85rem" }}>
        PDF · Markdown · XLSX · 최대 {MAX_MB}MB
      </p>

      {items.map((it) => (
        <div className="card uprow" key={it.id}>
          <button className="btn-ghost btn-sm" onClick={() => remove(it.id)}
                  style={{ position: "absolute", top: 12, right: 12 }} aria-label="제거">✕</button>
          <b>{it.file.name}</b>{" "}
          <span className="muted" style={{ fontSize: "0.85rem" }}>({(it.file.size / 1048576).toFixed(1)}MB)</span>
          {!it.invalid && (
            <div className="stack">
              {[
                ["title", "제목"], ["publisher", "발행기관"],
                ["tags", "태그(쉼표구분)"], ["publish_date", "발행 연도(필수) 예: 2026"],
              ].map(([f, ph]) => (
                <input key={f} placeholder={ph} value={it[f]}
                       onChange={(e) => patch(it.id, { [f]: e.target.value })} />
              ))}
            </div>
          )}
          <div className={`st${it.err ? " err" : it.ok ? " ok" : ""}`} style={{ marginTop: 8 }}>{it.st}</div>
        </div>
      ))}

      {items.length > 0 && (
        <div className="card">
          <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: "0.9rem" }}>
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)}
                   style={{ width: "auto", padding: 0 }} />
            기존 문서 덮어쓰기(force)
          </label>
          <button style={{ width: "100%", marginTop: 12 }} disabled={busy} onClick={startUpload}>
            {busy ? <><span className="spinner" /> 업로드 중…</> : "전체 업로드"}
          </button>
        </div>
      )}
    </div>
  );
}
