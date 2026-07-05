# Gemini 네이티브 PDF 파싱 전환 설계서

**작성일:** 2026-07-05 · **브랜치:** `feat/gemini-pdf-parse`

## 1. 개요 (Goal)

PDF 파싱 Stage 1을 로컬 Docling(EasyOCR + TableFormer + torch)에서 **Gemini 멀티모달 네이티브 PDF 파싱**으로 교체한다. v1 공개문서 대상. 다운스트림 계약(`chunks.json`, `tables/*.json`)을 그대로 유지해 classify·map_tables·narrative·events 스테이지는 무수정으로 흡수한다.

## 2. 배경

- **문제:** 13MB 스캔형 PDF(인재확보 전략, 27p)를 Docling으로 파싱하면 EasyOCR 파싱이 워커 메모리를 ~5GB로 스파이크시켜 8GB 호스트에서 **재현성 있게 OOM(SIGKILL)**. 해당 문서는 현 파이프라인으로 재인제스트 불가.
- **스파이크 결과(2026-07-05):** 같은 문서를 `gemini-3.1-flash-lite` 네이티브 파싱 실측 —
  - 35.7초 · 입력 14.5k토큰 · 출력 7.8k토큰 · 워커 메모리 ~0 · 추정 ~0.5센트/문서
  - 표 품질이 Docling ACCURATE보다 깨끗(목차 오인·병합셀 잡음 없음), 숫자 6/6 원문 일치(환각 0)
  - Step1(3회 실행): 앵커 24/24 전 회차, "본문 생략 금지" 프롬프트로 완전성 확보, 비결정성은 형식만 ±5%
  - Step2: `rows:[{cells:[str]}]` 회피 스키마로 구조화 표 출력 작동, 셀 정합 100%, map_tables 형태로 어댑트 가능

## 3. 결정 요약 (브레인스토밍 확정)

1. **그림:** 파싱 시 본문 **인라인 흡수** — 멀티모달이 그림을 보고 마크다운에 `[그림: …]`로 요약. `describe_figure` 스테이지·`figures/` 크롭 삭제.
2. **호출 구조:** **2회 호출**(마크다운 + 구조화 표), 단 두 호출이 같은 PDF를 보내므로 **캐시로 중복 입력 토큰 절감**.
3. **Docling:** **완전 삭제** (v1 집중). v2 민감문서는 추후 별도 트랙(로컬 LLM/redaction)에서 재도입.

## 4. 아키텍처

```
run_pipeline(source_dir)              # parse.py, 포맷 분기 — 무변경
  └─ .pdf → parse_pdf(original, out)  # 재작성: Docling → Gemini 2-call
         Call 1 (마크다운)  → document.md  (그림 인라인, 표는 플레이스홀더)
         Call 2 (구조화 표) → tables/table_NNN.json
         파생            → chunks.json  (텍스트 청크 + 표 청크)

compile.py  (스테이지 오케스트레이션)
  classify.classify_chunks(parsed)     # chunks.json 소비 — 무변경
  [삭제] describe.describe_figures(...) # 그림 인라인화로 불필요
  map_tables.map_and_stage_tables(...)  # tables/*.json 소비 — 무변경
  narrative.compile_narrative(..., texts)
  events.extract_and_stage_events(texts, ...)
```

## 5. 상세 설계

### 5.1 `parse_pdf(src, out)` 재작성

Docling 임포트·`DocumentConverter` 전체 제거. `llm` 모듈과 `google.genai`만 사용. 산출물:

| 파일 | 생성 방법 | 비고 |
|---|---|---|
| `document.md` | Call 1 출력 그대로 | 인간·디버그용, 표는 플레이스홀더 |
| `tables/table_NNN.json` | Call 2 표당 1파일 | `{table_title, columns, rows}` — Docling과 동일 형태 |
| `chunks.json` | 마크다운 파생 + 표 청크 | 아래 5.4 |
| ~~`document.json`~~ | 삭제 | Docling 전용 |
| ~~`figures/`~~ | 삭제 | 그림 인라인화 |

두 호출 모두 `usage_metadata`가 필요(캐시 히트 검증·로깅)하므로 `llm.generate` 대신 **client 직접 호출**(단, 모델·타임아웃·재시도는 `llm` 모듈의 것을 재사용하도록 헬퍼 추가). → `llm.py`에 `generate_with_usage(purpose, contents, schema=None) -> (text_or_dict, usage)` 헬퍼를 추가하고 기존 `generate`는 이를 감싸도록 리팩터(재시도·타임아웃 로직 중복 제거).

### 5.2 Call 1 — 마크다운 (그림 인라인, 표 플레이스홀더)

- 설정: `thinking_level="low"`, 스키마 없음(순수 텍스트).
- 프롬프트 요지:
  - "문서 전체를 구조 보존 마크다운으로. **본문 내용 생략 금지**(쪽번호 반복만 생략)."
  - **표는 전체 그리드 대신 `[표: {간결한 제목}]` 플레이스홀더 한 줄로만** 표기(그리드는 Call 2가 담당). → chunks 텍스트/표 중복 방지.
  - **그림/차트/조직도는 `[그림: {한 줄 요약}]`로 본문 흐름에 인라인**. 차트 수치는 추정임을 명시.
  - 숫자·연월일·부처명·연락처는 원문 그대로.
- 출력을 `document.md`로 저장.

### 5.3 Call 2 — 구조화 표

- 설정: `thinking_level="low"`, `response_mime_type="application/json"`, `response_schema=TABLE_SCHEMA`.
- `TABLE_SCHEMA`(중첩배열 회피):

```python
TABLE_SCHEMA = {
  "type": "object",
  "properties": {"tables": {"type": "array", "items": {
    "type": "object",
    "properties": {
      "table_title": {"type": "string"},
      "page": {"type": "integer"},
      "columns": {"type": "array", "items": {"type": "string"}},
      "rows": {"type": "array", "items": {
        "type": "object",
        "properties": {"cells": {"type": "array", "items": {"type": "string"}}},
        "required": ["cells"]}},
    },
    "required": ["table_title", "columns", "rows"]}}},
  "required": ["tables"]}
```

- 프롬프트 요지: 모든 표를 추출, 헤더 없으면 명명, 병합셀 값 채움, **목차·점선 표 제외**, 셀 개수는 columns 길이에 맞춤.
- 어댑트: 각 표를 `{table_title, columns, rows: [r["cells"] for r in t["rows"]]}`로 변환해 `tables/table_{i:03d}.json` 저장(Docling과 동일 스키마 → map_tables 무변경).
- **정합 가드:** `len(row) != len(columns)`인 행은 부족분 `""` 패딩 / 초과분 절단 후 저장하고, 그런 표는 카운트해 로깅(ponytail: LLM 셀 개수 오차 방어, 스파이크에선 0건이었으나 방어적으로).

### 5.4 `chunks.json` 파생

기존 청크 계약(`classify.py`가 소비): `text`=`{id,type:"text",page,text}`, `table`=`{id,type:"table",page,ref}`.

- **텍스트 청크:** `document.md`를 헤딩(`#`) 단위로 분할(기존 `parse.py:parse_md`의 청킹 로직 재사용/공유). `[표: …]`·`[그림: …]` 플레이스홀더는 텍스트에 **그대로 남겨** narrative가 맥락으로 참조. `page`는 `None`(마크다운은 섹션별 쪽 정보 없음 — 기존 `parse_md`도 None).
- **표 청크:** Call 2 표당 `{id, type:"table", page, ref: "tables/table_NNN.json"}`. `page`는 Call 2가 준 값.
- **id 스킴:** 텍스트 `c001…`, 표 `t001…`(고유성만 충족하면 됨; classify는 type과 id만 사용).
- picture 청크 없음(그림 인라인화).

### 5.5 캐싱

두 호출 모두 **PDF Part를 `contents`의 맨 앞**에 배치 → Gemini **암묵적 캐싱**이 공통 프리픽스(~14.5k토큰)에 자동 할인 적용(코드 0). Call 2가 Call 1 직후 실행되므로 캐시 윈도 내 히트 기대.

- **검증:** 계획 단계에서 두 번째 호출의 `usage_metadata.cached_content_token_count > 0` 확인.
- **업그레이드 경로(ponytail):** 볼륨이 커져 암묵적 캐시로 부족하면 명시적 `client.caches.create`로 PDF를 캐시하고 두 호출이 참조. 지금은 YAGNI.

## 6. 다운스트림 영향

- **`compile.py`:** `describe` 임포트·`describe_figures(...)` 호출 라인 제거. `texts`는 narrative 텍스트 청크만으로 구성(그림 설명은 이미 청크 텍스트에 인라인).
- **삭제 파일:** `api/pipeline/describe.py`, 관련 테스트, `llm_config.json`의 `describe_figure` 항목.
- **무변경:** `classify.py`, `map_tables.py`, `narrative.py`, `events.py` — 계약 동일.

## 7. 의존성·이미지·인프라

- `requirements`에서 `docling`, `docling-core`, `easyocr`(및 이들이 끌고 온 torch/torchvision 등 파싱 전용) 제거. `pypdfium2`는 프로브·후속 대형문서 배치용으로 남길지 계획에서 판단(현재 코드 경로엔 불필요).
- `Dockerfile`: EasyOCR 모델 baking 라인 제거 → **이미지 대폭 경량화·빌드 단축**.
- 효과: 워커 메모리 바닥 ~1.5–2GB 하락, **파싱 OOM 소멸**. [[embedding-resource-constraint]]의 파싱 OOM 항목 해소.

## 8. 에러 처리

- Call 1/Call 2는 `llm` 모듈의 재시도(3회·지수백오프)·타임아웃(문서가 크므로 파싱용 타임아웃은 상향, 예 180s) 적용.
- Call 2 JSON 파싱 실패·스키마 위반은 `response_schema`가 재시도로 방어. 그래도 실패 시 파싱 태스크는 `failed`로(현 인제스트 에러 경로 그대로).
- 표 0개도 정상(표 없는 문서) — `tables/` 빈 디렉토리, map_tables no-op.
- 마크다운 빈 출력은 파싱 실패로 간주(raise) — 빈 위키 생성 방지.

## 9. 테스트 전략

- **단위(`test_parse_pdf.py` 신설):** `llm.generate_with_usage`를 monkeypatch해 캔 마크다운 + 캔 구조화 표를 반환 → 산출물 검증:
  - `tables/table_NNN.json`이 `{table_title, columns, rows}` 형태이고 셀 정합 패딩이 작동
  - `chunks.json`이 text/table 청크를 갖고 `[표:]`/`[그림:]` 플레이스홀더가 텍스트에 보존
  - 셀 개수 불일치 행 패딩/절단 가드
- **회귀:** 기존 `test_map_tables.py`(Docling 표 json을 먹던 것)·classify 관련 테스트는 계약 동일 → 통과 유지.
- **실 API 스모크(수동):** 환경변수 플래그로 인재확보 PDF 1회 파싱해 앵커·표 개수·캐시 히트 육안 확인.

## 10. 계획 단계에서 검증할 항목

1. 그림 인라인 흡수 품질 — 실 문서 1회 파싱해 `[그림:]`이 유의미한지 확인.
2. 암묵적 캐시 실제 히트 — `cached_content_token_count`.
3. 표 플레이스홀더 방식이 narrative 품질을 떨어뜨리지 않는지 — 파싱→narrative까지 한 번 돌려 확인.

## 11. 범위 밖 (Out of Scope)

- **v2 민감문서 redaction 게이트** — 별도 설계 트랙.
- **대형 문서(100p+) 페이지 배치** — 출력 토큰 한계 도달 시 도입. 현재 YAGNI(메모리 문제는 클라우드라 없음).
- **명시적 context caching** — 볼륨 증가 시 업그레이드.
- **XLSX/MD 파싱 경로** — 무변경.

## 12. 리스크

| 리스크 | 완화 |
|---|---|
| LLM 표 셀 오독/누락 | staging + 사람 승인(기존), 셀 정합 가드, 숫자는 스파이크서 6/6 정확 |
| 비결정성(재파싱 시 미세 차이) | 내용 앵커는 안정(형식만 ±5%); 재인제스트 dedup은 기존 [[gemini-native-pdf-parse-spike]] 참조 |
| 그림 정보 손실 | 인라인 흡수로 텍스트 보존; 크롭 이미지가 꼭 필요한 문서는 v2에서 재검토 |
| 대형 문서 출력 잘림 | 계획서 검증 항목 + 범위 밖 배치 처리로 이관 |
