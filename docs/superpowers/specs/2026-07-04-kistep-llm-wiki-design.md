# KISTEP LLM Wiki 하이브리드 아키텍처 설계서 (확정본)

- 작성일: 2026-07-04
- 상태: 승인됨 (v1 범위 확정)
- 목적: 국가전략기술 정책 지식을 지속적으로 컴파일하고, 정형·비정형 데이터를 통합 관리하는 웹 기반 LLM Wiki 시스템의 아키텍처 설계

## 1. 개요

### 1.1 배경: LLM Wiki 개념

2026년 4월 Andrej Karpathy가 제안한 LLM Wiki는 기존 RAG 패턴과 다른 지식 관리 패러다임이다. 기존 RAG는 매 질의 시점에 원본 문서에서 검색→합성을 반복하는 반면, LLM Wiki는 새 문서를 인제스트할 때 LLM이 읽고, 이해하고, 기존 지식 베이스에 통합한다. 기존 페이지를 업데이트하고, 새 정보와 기존 지식 사이의 모순을 기록하며, 필요한 개념 페이지를 생성하고, 위키 전체의 관계를 강화한다.

원본 소스는 입력 재료, 위키는 컴파일된 산출물, 스키마는 컴파일 규칙서에 해당한다.

### 1.2 KISTEP 업무 맥락에서의 필요성

- NEXT 체계(10개 분야, 55개 우선기술)의 지속적 변화 추적
- 사업목록, 예산, 부처별 R&D 정보 등 정형 데이터의 필터링·집계 수요
- 정책변화의 맥락과 논리(왜 12개 분야가 10개로 재편되었는가)에 대한 서사적 이해 수요
- 해외 S&T 정책 동향, DART 공시, NTIS 사업목록 등 다양한 소스의 통합 필요

순수 LLM Wiki(서사 중심)로는 정형 집계 수요를 충족할 수 없으므로, 서사 레이어(LLM Wiki)와 데이터 레이어(구조화 DB)를 결합한 하이브리드 구조를 채택한다.

### 1.3 핵심 설계 원칙

1. **서사와 데이터의 분리**: 정책 맥락·논리는 Wiki 페이지로, 사업·예산·KPI는 정형 DB로 분리하되 상호참조로 연결
2. **인제스트 시점 컴파일**: 질의 시점이 아니라 문서 유입 시점에 지식을 구조화
3. **사람이 승인 게이트**: LLM은 지식 베이스를 직접 수정하지 않는다. 모든 인제스트 결과는 스테이징 상태에 머물고, 사람의 승인을 거쳐야 본 지식 베이스(위키 main + DB 본 테이블)에 반영된다
4. **Git 기반 이력 관리**: 위키 페이지의 모든 변경을 Git으로 추적하여 "어떤 소스가 어떤 지식을 변경했는가" 감사 가능
5. **고정 스키마**: DB 스키마는 사람이 관리한다. LLM은 표를 기존 스키마에 매핑만 하고, DDL을 생성하거나 테이블을 확장하지 않는다
6. **하이브리드 검색**: 자연어 질의 시 벡터 검색(서사) + Text-to-SQL(데이터)을 병렬 실행

### 1.4 v1 범위

- **대상 자료**: 공개 자료 전용(공개 정책문서, 국가전략기술 사업·정책기관 목록 등 공개 데이터셋, 해외 공개 보고서). 비공개·민감문서와 온프레미스 LLM 라우팅은 v2 확장점으로만 남긴다
- **입력 방식**: 전량 수동 업로드. PDF가 기본이고, MD(사용자가 Docling 등으로 직접 변환한 문서)와 XLSX(사업·기관 목록 등 데이터셋)를 지원한다. HWP는 미지원이며, DART·NTIS 등 외부 API 자동 연동은 두지 않는다
- **사용자**: 개인 단독 사용. 로그인 체계 없이 리버스 프록시(NPM) 수준 접근 제한 + admin key로 보호
- **LLM**: Gemini API 단일 공급자 (상세는 9절)

## 2. 전체 아키텍처

시스템은 5개 레이어로 구성된다.

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Sources (immutable)                           │
│  정책문서(PDF/MD) │ 엑셀 데이터셋(사업·기관 목록) │ 보고서 │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Ingest Pipeline (LLM-powered)                 │
│  포맷 분기(PDF/MD/XLSX) → Docling 파싱 → LLM 분류기      │
│  → [서사 추출 | 표→고정 스키마 매핑] → 스테이징           │
└────────────┬───────────────────────────────┬────────────┘
             ▼                               ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 3: Storage                                       │
│  Knowledge Wiki      │  Vector DB  │  Data Store        │
│  (Markdown/Git,      │  (Qdrant)   │  (PostgreSQL,      │
│   스테이징 브랜치)     │             │   staging 스키마)   │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 4: API Layer (FastAPI)                           │
│  Wiki CRUD │ Query(RAG+SQL) │ Ingest │ Review │ Lint    │
└──────────────────────────┬──────────────────────────────┘
                           ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 5: Web Frontend (Next.js)                        │
│  승인 대시보드 │ Wiki 브라우저 │ 자연어 질의 │ 데이터 탐색기│
└─────────────────────────────────────────────────────────┘
```

## 3. Layer 1: Sources (불변 원본 저장소)

원본 문서는 절대 수정하지 않으며, 다음 경로 체계로 저장한다.

```
/sources/
├── {source_id}/
│   ├── original.pdf          # 원본 파일 (pdf/md/xlsx) — 불변
│   ├── metadata.json         # 수집 메타데이터
│   ├── ingest_log.json       # 인제스트 이력
│   └── parsed/               # Stage 1 파싱 산출물 (PDF 입력 시 생성)
│       ├── document.json     # DoclingDocument 전체 직렬화 (페이지·좌표·요소 관계 보존)
│       ├── document.md       # 마크다운 표현 (열람·참고용)
│       ├── chunks.json       # 청크 매니페스트: id, 유형, 페이지/좌표, 표·그림 파일 참조
│       ├── tables/
│       │   └── table_001.json    # 표별 구조 데이터 (셀 병합 포함) — 정형 경로 입력
│       └── figures/
│           ├── fig_001.png       # 추출된 그림·차트 이미지
│           └── fig_001.desc.md   # Gemini 멀티모달이 생성한 그림 설명 (Stage 3에서 생성)
```

파싱 산출물의 역할:

- **document.json**: MD 변환 시 소실되는 프로버넌스(페이지 번호, 좌표, 캡션-표 관계)를 보존한다. 승인 화면의 원문 대조와 질의 응답의 인용이 이 정보에 의존한다
- **document.md**: 사람이 읽기 위한 표현. 파이프라인의 작업 데이터로는 쓰지 않는다
- **chunks.json**: Stage 2 분류기의 입력 단위이자 "MD 구간 ↔ 표 파일 ↔ 이미지 파일"을 잇는 색인
- **tables/**: TableFormer가 복원한 원 구조(셀 병합·다단 헤더 포함). 정형 표 경로(스키마 매핑)는 MD 표가 아니라 이 파일을 입력으로 받는다
- **figures/**: 추출 이미지는 Gemini 멀티모달 해석의 입력이 되고, 생성된 설명(`*.desc.md`)은 서사 경로로 위키에 반영된다. 설명 파일을 parsed/에 보존해 재인제스트 시 이미지 해석 호출을 재사용한다
- 차트에서 판독한 수치는 눈금 추정치이므로 정형 DB에는 넣지 않는다. DB에는 표·본문에 명시된 값만 적재한다
- MD로 직접 업로드된 소스는 parsed/ 산출물 없이 텍스트 분류 단순 경로로 처리된다(표 구조·이미지 손실). 표·그림이 중요한 문서는 PDF 업로드를 권장한다

메타데이터 스키마:

```json
{
  "source_id": "uuid",
  "source_type": "policy_doc | dataset_excel | foreign_report",
  "title": "문서 제목",
  "publisher": "발행기관",
  "publish_date": "2026-04-15",
  "ingest_date": "2026-07-04T09:00:00Z",
  "tags": ["NEXT", "반도체", "예산"],
  "file_hash": "sha256:..."
}
```

입력은 전량 수동 업로드이며, 자동/반자동 수집기는 두지 않는다. 포맷별 처리 경로:

| 입력 포맷 | 대상 자료 | 처리 경로 |
|---|---|---|
| PDF (기본) | 정책문서, 해외 보고서 | Docling 파싱 → LLM 분류기 |
| MD | 사용자가 Docling 등으로 직접 변환한 문서 | 파싱 생략 → LLM 분류기 |
| XLSX | 국가전략기술 사업 목록, 정책기관 목록 등 데이터셋 | 정형 표 경로 직행 (스키마 매핑) |

HWP는 v1에서 지원하지 않는다. HWP 원본은 사용자가 사전에 PDF 또는 MD로 변환해 업로드한다.

## 4. Layer 2: Ingest Pipeline

### 4.1 파이프라인 흐름

```
원본 문서 업로드 (PDF / MD / XLSX)
    ▼
[Stage 0] 입력 포맷 분기
    │  PDF  → Stage 1 (Docling)
    │  MD   → Stage 1 생략, Stage 2로 직행
    │  XLSX → Stage 1·2 생략, 정형 표 경로 [3b]로 직행
    ▼
[Stage 1] Docling 파싱 (TableFormer ACCURATE + OCR) — PDF만
    ▼
[Stage 2] LLM 콘텐츠 분류기 (청크 단위: 서사 vs 정형 표 판별)
    │
    ├──→ 서사 경로                      ├──→ 정형 표 경로
    │    ▼                             │    ▼
    │  [3a] 개념·엔티티 추출             │  [3b] 고정 스키마 매핑
    │    │  기술명, 부처, 정책 등         │    │  표 → 기존 테이블·컬럼 대응
    │    ▼                             │    ▼
    │  [4a] 위키 페이지 갱신 (상한 적용)  │  [4b] 정규화 + 검증
    │    │  기존 페이지 diff + merge     │    │  셀병합 해제, 중복 제거
    │    ▼                             │    ▼
    │  [5a] 교차참조 업데이트            │  [5b] staging 스키마에 적재
    │    ▼                             │    │  매핑 불가 표 → staging_tables
    │  ingest/{source_id} 브랜치 커밋    │    ▼
    │                                  │  PostgreSQL (staging)
    └──────────┬────────────────────────┘
               ▼
        [Stage 6] 승인 대기 (대시보드에 diff·매핑 결과·모순 표출)
               ▼  사람이 승인
        [Stage 7] main 병합(squash) + DB 본 테이블 upsert
               ▼
        [Stage 8] Vector 임베딩 (BGE-M3 → Qdrant)
```

핵심 변경점 (초안 대비):

- **직접 커밋 금지**: 서사 경로는 `ingest/{source_id}` 브랜치에만 커밋하고, 정형 경로는 PostgreSQL `staging` 스키마에만 적재한다
- **승인 단위는 소스 1건 전체**: 위키와 DB가 서로 다른 승인 상태에 놓이는 불일치를 방지한다. 승인 시 브랜치 squash 병합과 DB upsert가 함께 실행되고, 거부 시 브랜치 폐기와 staging 데이터 삭제가 함께 실행된다
- **임베딩은 승인 후**: 미승인 지식이 검색 결과에 노출되지 않도록 Stage 8은 병합 이후에 수행한다

### 4.2 LLM 콘텐츠 분류기

한국 정부문서의 현실적 특성을 고려한 분류 전략:

- **분류 단위**: 페이지가 아닌 청크(chunk) 단위. 하나의 페이지에 서사와 표가 혼재하는 경우가 빈번함
- **표 내부 분할**: 표의 각주/제목은 위키 페이지로, 표 본체는 DB로 분리
- 한국 정부문서 특유의 셀 병합, 다단 헤더, 비정규화 구조 처리를 위한 전처리 로직 포함

분류 카테고리:

- `NARRATIVE`: 정책 배경, 논리, 맥락 설명 → Wiki 페이지로 라우팅
- `TABLE_STRUCTURED`: 사업목록, 예산표, 기술분류표 등 → DB로 라우팅
- `TABLE_WITH_CONTEXT`: 표 + 설명 혼합 → 분리 후 각각 라우팅
- `METADATA`: 문서 서지정보 → 소스 메타데이터에 추가

### 4.3 서사 경로 상세

1. **개념·엔티티 추출**: 새 소스에서 기술명, 부처명, 정책명, 사업명 등 핵심 엔티티를 추출
2. **위키 페이지 매칭**: 기존 위키의 페이지 인덱스와 대조하여 갱신/생성 대상 판별
3. **페이지 갱신**: 기존 페이지와 새 정보를 LLM이 diff+merge. 모순 발생 시 별도 기록
4. **갱신 범위 상한**: 소스 1건당 자동 갱신 페이지 수 상한(기본 15개, 설정 가능). 초과분은 자동 갱신하지 않고 "갱신 제안 목록"으로 승인 화면에 표시한다. 비용과 오류 전파 반경을 통제하기 위한 장치다
5. **교차참조**: 관련 페이지 간 링크 업데이트
6. **브랜치 커밋**: `ingest/{source_id}` 브랜치에 커밋. 커밋 메시지에 source_id를 포함해 추적성 확보

### 4.4 정형 표 경로 상세

1. **고정 스키마 매핑**: LLM이 표의 컬럼을 기존 핵심 테이블(5.2절)의 컬럼에 대응시킨다. 매핑 결과는 대상 테이블, 컬럼 대응표, 신뢰도 점수로 구성된다
2. **정규화**: 셀 병합 해제, 반복 헤더 제거, 데이터 타입 변환
3. **staging 적재**: 매핑 신뢰도가 임계값(기본 0.8, 설정 가능) 이상이면 `staging` 스키마의 동일 구조 테이블에 적재. 임계값 미만이거나 대응 테이블이 없으면 `staging_tables`에 원본 구조(JSONB)로 보존하고 "스키마 검토 필요"로 표시
4. **승인 시 upsert**: 승인 시점에 본 테이블과 비교하여 삽입/갱신. 충돌은 승인 화면에 표출

**LLM은 DDL을 생성하지 않는다.** 스키마 확장이 필요하다고 판단되는 경우는 대시보드에 제안으로만 표시되고, 사람이 마이그레이션 파일을 작성해 반영한다.

### 4.5 모순 처리 워크플로

- 페이지 갱신 중 기존 지식과 신규 정보가 충돌하면 모순 레코드를 생성한다 (대상 페이지, 기존 주장, 신규 주장, 각 출처)
- 모순은 `contradictions/log.md` 기록과 함께 **승인 화면에 표출**되어, 승인자가 그 자리에서 "기존 유지 / 신규 채택 / 병기" 중 선택한다
- 미해결 모순은 승인을 막지 않되, 대시보드에 잔존 카운트로 표시되고 해당 페이지 프론트매터에 플래그가 남는다

## 5. Layer 3: Storage

### 5.1 Knowledge Wiki (Git 저장소)

```
/wiki/
├── index.md                          # 전체 위키 색인
├── schema.md                         # 위키 운영 규칙 (LLM용)
├── tech/                             # 기술별 개념 페이지
├── entity/                           # 정책 엔티티 페이지 (부처, 기관)
├── events/                           # 정책변화 이력
├── synthesis/                        # 종합·비교 분석
├── summaries/                        # 소스별 요약 ({source_id}.md)
└── contradictions/                   # 모순·충돌 기록 (log.md)
```

브랜치 전략: `main`은 승인된 지식만 담는다. 인제스트는 `ingest/{source_id}` 브랜치에서 작업하고, 승인 시 squash 병합, 거부 시 브랜치 삭제.

페이지 프론트매터 표준:

```yaml
---
title: HBM 반도체
type: tech_concept
next_field: 반도체
related_pages:
  - tech/quantum-computing
  - entity/과기정통부
data_refs:
  - "technologies?name=HBM"
  - "projects?tech=HBM"
sources:
  - source_id: "abc123"
    last_updated: "2026-07-04"
unresolved_contradictions: []          # 미해결 모순 ID 목록
---
```

위키 페이지에서 DB를 참조하는 문법 `[[data:technologies?field=반도체]]`를 정의하여 두 레이어를 연결한다.

### 5.2 Data Store (PostgreSQL)

스키마는 사람이 관리하는 고정 스키마다. 본 테이블은 `public` 스키마, 승인 대기 데이터는 `staging` 스키마(동일 구조)에 둔다.

```sql
-- NEXT 기술 테이블
CREATE TABLE technologies (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    field VARCHAR(50) NOT NULL,        -- 10개 NEXT 분야
    sub_field VARCHAR(100),
    lead_ministry VARCHAR(50),
    trl_level INTEGER,
    description TEXT,
    wiki_page_path VARCHAR(200),       -- Wiki 페이지 참조
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    source_id VARCHAR(100)             -- 출처 소스
);

-- R&D 사업 테이블
CREATE TABLE projects (
    id SERIAL PRIMARY KEY,
    project_code VARCHAR(50) UNIQUE,
    name VARCHAR(200) NOT NULL,
    lead_ministry VARCHAR(50),
    budget_total BIGINT,               -- 총사업비 (백만원)
    budget_annual BIGINT,              -- 연간예산 (백만원)
    start_year INTEGER,
    end_year INTEGER,
    status VARCHAR(20),                -- 진행중/완료/예비타당성
    source_id VARCHAR(100)
);

-- 기술-사업 매핑 테이블
CREATE TABLE tech_project_mapping (
    technology_id INTEGER REFERENCES technologies(id),
    project_id INTEGER REFERENCES projects(id),
    relevance_score FLOAT,
    mapping_source VARCHAR(20),        -- manual/llm_inferred
    PRIMARY KEY (technology_id, project_id)
);

-- 예산 이력 테이블
CREATE TABLE budget_history (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id),
    fiscal_year INTEGER NOT NULL,
    amount BIGINT NOT NULL,
    source_id VARCHAR(100)
);

-- 정책 이벤트 로그
CREATE TABLE policy_events (
    id SERIAL PRIMARY KEY,
    event_date DATE NOT NULL,
    event_type VARCHAR(50),            -- reform/announcement/law/summit
    title VARCHAR(200),
    description TEXT,
    affected_fields TEXT[],
    wiki_page_path VARCHAR(200),
    source_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
);

-- 부처 테이블
CREATE TABLE ministries (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    abbreviation VARCHAR(20),
    wiki_page_path VARCHAR(200)
);

-- 매핑 불가 표 보존 (스키마 검토 대기)
CREATE TABLE staging_tables (
    id SERIAL PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    table_title TEXT,                  -- 표 제목/캡션
    raw_data JSONB NOT NULL,           -- 정규화된 원본 표 (행 배열)
    suggested_mapping JSONB,           -- LLM의 매핑 제안 (참고용)
    mapping_confidence FLOAT,
    status VARCHAR(20) DEFAULT 'needs_review',  -- needs_review/mapped/discarded
    created_at TIMESTAMP DEFAULT NOW()
);

-- 인제스트 태스크 상태
CREATE TABLE ingest_tasks (
    task_id VARCHAR(100) PRIMARY KEY,
    source_id VARCHAR(100) NOT NULL,
    status VARCHAR(30) NOT NULL,       -- queued/parsing/classifying/staged/
                                       -- approved/rejected/failed
    branch_name VARCHAR(200),
    affected_pages JSONB,              -- 갱신된 페이지 + 갱신 제안 목록
    affected_tables JSONB,
    contradictions JSONB,
    error TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    reviewed_at TIMESTAMP
);
```

### 5.3 Vector DB (Qdrant)

- 임베딩 모델: BGE-M3 (한국어 다국어 지원, 밀집+희소 하이브리드)
- 컬렉션: `wiki_pages` (위키 페이지 청크), `db_records` (정형 데이터의 자연어 설명)
- 하이브리드 검색: 밀집 벡터(semantic) + 희소 벡터(lexical) 결합으로 한국어 검색 품질 확보
- **승인된 지식만 색인한다.** 임베딩은 승인·병합 이후에 수행하고, 거부된 인제스트는 색인되지 않는다

## 6. Layer 4: API Layer (FastAPI)

### 6.1 엔드포인트

`[admin]` 표시는 `X-Admin-Key` 헤더 필수를 의미한다.

```
POST /api/v1/ingest                          [admin]
    - 새 소스 업로드 → 비동기 인제스트 파이프라인 실행
    - Request: multipart/form-data (파일 + 메타데이터)
    - Response: { task_id, status: "queued" }

GET  /api/v1/ingest/{task_id}/status
    - 인제스트 진행 상태 조회
    - Response: { status, affected_pages[], affected_tables[], contradictions[] }

GET  /api/v1/ingest/{task_id}/review
    - 승인용 상세 조회: 위키 diff, DB staging 레코드, 모순, 갱신 제안 목록
    - Response: { wiki_diffs[], staged_records[], contradictions[], update_suggestions[] }

POST /api/v1/ingest/{task_id}/approve        [admin]
    - 승인: 브랜치 squash 병합 + DB upsert + 임베딩 수행
    - Request: { contradiction_resolutions: {id: "keep|replace|both"}[] }

POST /api/v1/ingest/{task_id}/reject         [admin]
    - 거부: 브랜치 삭제 + staging 데이터 삭제

POST /api/v1/query
    - 자연어 질의 → 하이브리드 검색 + LLM 합성
    - Request: { question, mode: "auto|narrative|data|hybrid" }
    - Response: { answer, citations[], sql_results?, wiki_refs[] }

GET  /api/v1/wiki/{path}
    - 위키 페이지 직접 조회 (main 기준)
    - Response: { content_md, frontmatter, git_history[] }

GET  /api/v1/wiki/search?q=...
    - 위키 내 전문 검색

GET  /api/v1/data/{table}
    - 정형 데이터 테이블 조회 (필터링, 정렬, 페이지네이션)

POST /api/v1/lint                            [admin]
    - 위키 일관성 점검 트리거
    - Response: { issues[], suggestions[] }

GET  /api/v1/stats
    - 시스템 통계 (위키 페이지 수, DB 레코드 수, 승인 대기 수, 미해결 모순 수)
```

### 6.2 질의 처리 흐름

```
사용자 질의 입력
    ▼
[의도 분류] LLM이 질의 유형 판별
    ├─ 서사 질문 ("왜 NEXT가 재편되었나?")
    │   └→ 벡터 검색 → 위키 페이지 조회 → LLM 합성 + 인용
    ├─ 데이터 질문 ("반도체 분야 사업 예산 합계는?")
    │   └→ Text-to-SQL → DB 쿼리 → 결과 포매팅 + 시각화
    └─ 혼합 질문 ("반도체 예산이 늘어난 정책적 배경은?")
        └→ 양쪽 병렬 실행 → LLM이 데이터+서사 합성
```

Text-to-SQL은 읽기 전용 DB 계정으로 실행하고, `public` 스키마의 화이트리스트된 테이블만 접근한다.

## 7. Layer 5: Web Frontend (Next.js)

4가지 핵심 뷰 (우선순위 순):

1. **승인 대시보드**: 승인 대기 인제스트 목록, 페이지별 Git diff 뷰, staging 데이터 미리보기, 모순 해결 UI(기존 유지/신규 채택/병기 선택), 갱신 제안 목록, 승인/거부 버튼. 이 시스템의 신뢰 모델을 완성하는 핵심 화면
2. **Wiki 브라우저**: 마크다운 렌더링, 교차참조 링크 네비게이션, Git 이력 뷰, 페이지 의존성 그래프 시각화
3. **자연어 질의 인터페이스**: 대화형 UI, 답변에 위키 페이지와 DB 쿼리 결과를 인용으로 첨부, 실행된 SQL 투명 표시
4. **데이터 탐색기**: AG Grid 기반 테이블 뷰, 필터링·정렬·집계, 기술-사업 매핑 시각화, 예산 추이 차트

## 8. 인프라 및 배포 구성

### 8.1 Docker Compose

```yaml
services:
  api:
    build: ./api
    ports: ["8000:8000"]              # NPM이 프록시할 포트
    environment:
      - DATABASE_URL=postgresql://wiki:pass@postgres:5432/llm_wiki
      - QDRANT_URL=http://qdrant:6333
      - WIKI_REPO_PATH=/data/wiki
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - ADMIN_API_KEY=${ADMIN_API_KEY}
      - REQUESTS_CA_BUNDLE=/certs/kistep-ca.pem   # KISTEP 네트워크에서 접속 시
    volumes:
      - wiki-data:/data/wiki
      - sources-data:/data/sources
    depends_on: [postgres, qdrant, redis]

  ingest-worker:
    build: ./api
    command: celery -A tasks worker --concurrency=2
    environment:
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - REQUESTS_CA_BUNDLE=/certs/kistep-ca.pem
    volumes:
      - wiki-data:/data/wiki
      - sources-data:/data/sources
    depends_on: [redis, postgres, qdrant]

  frontend:
    build: ./frontend
    ports: ["3000:3000"]              # NPM이 프록시할 포트
    environment:
      - NEXT_PUBLIC_API_URL=http://api:8000

  postgres:
    image: postgres:16
    environment:
      - POSTGRES_DB=llm_wiki
      - POSTGRES_USER=wiki
      - POSTGRES_PASSWORD=pass
    volumes:
      - pg-data:/var/lib/postgresql/data

  qdrant:
    image: qdrant/qdrant:latest
    volumes:
      - qdrant-data:/qdrant/storage

  redis:
    image: redis:7-alpine

volumes:
  wiki-data:
  sources-data:
  pg-data:
  qdrant-data:
```

### 8.2 Proxmox 환경 통합

- Nginx Proxy Manager(NPM): 기존 인스턴스에서 `nst-wiki.mem.photos` 서브도메인을 frontend(3000)/api(8000) 포트로 라우팅
- Cloudflare 와일드카드 SSL: 기존 설정 활용
- GPU 가용 시: Docling TableFormer ACCURATE 모드용 별도 LXC/VM

### 8.3 접근 제어

개인 사용 전제로 로그인 체계는 두지 않는다.

- **리버스 프록시 경계**: 서비스는 NPM 뒤 `nst-wiki.mem.photos`로 노출된다. 인터넷에서 접근 가능한 주소이므로, 필요 시 NPM의 Access List(IP 제한) 또는 Basic Auth로 접근을 제한한다
- **admin key**: 변경성 엔드포인트(`POST /ingest`, approve/reject, lint 트리거)는 `X-Admin-Key` 헤더 필수. 키는 환경변수 `ADMIN_API_KEY`로 주입
- 조회성 엔드포인트(위키 열람, 질의, 데이터 탐색)는 앱 수준 인증 없음. 단, `POST /query`는 LLM 호출 비용을 유발하므로 IP 기준 rate limit을 둔다

## 9. LLM 전략

v1은 Gemini API 단일 공급자를 사용한다.

| 용도 | 모델 | 설정 |
|---|---|---|
| 콘텐츠 분류 | `gemini-3.1-flash-lite` | `thinking_level: high` |
| 개념·엔티티 추출 | `gemini-3.1-flash-lite` | `thinking_level: high` |
| 위키 페이지 합성 | `gemini-3.1-flash-lite` | `thinking_level: high` |
| 표 스키마 매핑 | `gemini-3.1-flash-lite` | `thinking_level: high`, 구조화 출력 |
| Text-to-SQL | `gemini-3.1-flash-lite` | `thinking_level: high`, 구조화 출력 |
| 임베딩 | BGE-M3 (로컬) | 비용 없음 |

- **용도별 모델명은 config로 분리**하고 하드코딩하지 않는다. 특정 용도(예: 위키 합성)의 품질이 부족하면 해당 용도만 상위 모델로 개별 승격한다
- v1은 공개자료 전용이므로 온프레미스 LLM은 사용하지 않는다. v2에서 민감문서를 다루게 될 경우를 대비해 LLM 호출 계층을 공급자 중립 인터페이스로 감싼다

## 10. LLM Wiki 3대 오퍼레이션 + 승인

1. **Ingest**: 새 소스를 읽고 스테이징(위키 브랜치 + DB staging)까지 통합하는 작업 (4절)
2. **Review/Approve**: 사람이 diff·매핑·모순을 검토하고 승인/거부하는 작업. 승인 시 병합·upsert·임베딩이 수행된다 (4.1, 4.5절)
3. **Query**: 자연어 질의에 대해 위키(서사)와 DB(데이터)를 모두 검색하여 합성 답변 생성 (6.2절)
4. **Lint**: 위키 내부 일관성 점검 — 교차참조 무결성(깨진 링크), 모순 탐지(페이지 간 불일치), 오래된 정보 표시, DB-Wiki 참조 정합성, 커버리지 분석(55개 NEXT 기술 중 페이지 미생성 항목)

## 11. 구현 단계

각 Phase 완료 시 실제 공개 정책문서 2~3건으로 관통 테스트를 수행한다.

| 단계 | 구성요소 | 예상 기간 | 이유 |
|---|---|---|---|
| Phase 1 | 저장소 골격: wiki repo 구조 + PostgreSQL 고정 스키마(staging 포함) + docker-compose 기동 | 1-2주 | 이후 모든 단계의 검증 기반 |
| Phase 2 | 인제스트 파이프라인: 포맷 분기(PDF/MD/XLSX) → Docling → 분류 → 서사/표 경로 → 스테이징 | 3-4주 | 시스템의 핵심 |
| Phase 3 | 승인 워크플로 + 최소 대시보드 (diff 뷰, 모순 해결, 승인/거부) | 2주 | 신뢰 모델 완성, 이때부터 실사용 가능 |
| Phase 4 | 벡터 임베딩 + 하이브리드 질의 API (Text-to-SQL 포함) | 2주 | 질의 서비스화 |
| Phase 5 | 프론트엔드 확장: Wiki 브라우저, 질의 UI, 데이터 탐색기 | 3-4주 | 사용자 인터페이스 |
| Phase 6 | Lint + 모니터링 + 백업 | 2주 | 운영 안정화 |

## 12. 핵심 설계 판단

### 12.1 왜 마크다운인가

- LLM이 읽고 쓰기 가장 자연스러운 포맷
- Git diff가 텍스트 기반으로 동작하여 변경 추적이 명확
- 프론트엔드 렌더링이 간단하고 Obsidian 등 기존 도구와 호환

### 12.2 왜 표를 DB로 분리하는가

마크다운 표로는 "반도체 분야 전체 사업 예산 합계"를 SQL로 뽑을 수 없다. 정형 데이터의 쿼리 가능성을 보존하려면 관계형 DB가 필수다. Wiki 페이지의 `[[data:...]]` 참조 문법으로 두 레이어를 연결한다.

### 12.3 왜 스테이징 브랜치 + 승인인가

LLM이 사람 검토 없이 지식 베이스를 직접 수정하면 오류가 조용히 누적되고, 문서 하나가 10-15개 페이지에 영향을 주는 구조에서는 오염 반경이 크다. 자동 커밋(사후 감사)은 운영 부담이 적지만 오류가 먼저 반영되고, 전건 수동 승인은 병목이 된다. 소스 단위 일괄 승인은 감사 가능성과 운영 부담의 균형점이다.

### 12.4 왜 고정 스키마인가

LLM이 표마다 테이블을 생성·확장하면 유사 테이블이 난립하고(스키마 드리프트) Text-to-SQL 품질이 함께 무너진다. 핵심 테이블을 사람이 관리하고 LLM은 매핑만 하게 제한하면, 매핑 불가 표를 잃지 않으면서(staging_tables 보존) 스키마 일관성을 지킬 수 있다.

## 13. v2 확장점 (v1 범위 외)

- 비공개·민감문서 처리: 온프레미스 LLM 라우팅, 문서 민감도 분류
- 팀 공유: 로그인·권한 체계, 승인 권한 분리, KISTEP SSO 연동
- 자동 수집기: DART·NTIS API 연동, RSS, 웹 클리퍼
- HWP 직접 입력 지원 (변환 파이프라인)
