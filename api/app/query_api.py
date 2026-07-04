"""자연어 질의: 의도 분류 → 서사(벡터)/데이터(SQL)/혼합 → 합성 (스펙 6.2)."""
import json

from fastapi import APIRouter
from pydantic import BaseModel

import llm
import search
import text2sql

router = APIRouter(prefix="/api/v1")

# ponytail: 인메모리 rate limit — uvicorn 단일 프로세스 전제 (스펙 8.3), 다중 워커 도입 시 redis로
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

RATE_LIMIT = int(os.environ.get("QUERY_RATE_LIMIT", "10"))  # 분당 IP별
_hits: dict[str, deque] = defaultdict(deque)


def _check_rate(ip: str) -> None:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        raise HTTPException(status_code=429, detail="rate limit exceeded")
    q.append(now)


ROUTE_SCHEMA = {
    "type": "object",
    "properties": {"mode": {"type": "string", "enum": ["narrative", "data", "hybrid"]}},
    "required": ["mode"],
}

ROUTE_PROMPT = """질문의 유형을 분류하라.
- narrative: 배경·이유·맥락·설명을 묻는 질문
- data: 목록·수치·집계·필터링을 묻는 질문
- hybrid: 둘 다 필요한 질문

질문: {question}"""

SYNTH_PROMPT = """다음 자료만 근거로 질문에 한국어로 답하라. 자료에 없는 내용은 모른다고 답하라.
서사 자료를 인용할 때는 문장 끝에 [경로] 형식으로 출처를 표기하라.

{context}

질문: {question}"""


class QueryBody(BaseModel):
    question: str
    mode: str = "auto"


@router.post("/query")
def query(body: QueryBody, request: Request):
    _check_rate(request.client.host if request.client else "unknown")
    mode = body.mode
    if mode == "auto":
        mode = llm.generate("route_query", ROUTE_PROMPT.format(question=body.question),
                            schema=ROUTE_SCHEMA)["mode"]
    chunks, data = [], {"sql": None, "rows": [], "error": None}
    if mode in ("narrative", "hybrid"):
        chunks = search.search_wiki(body.question)
    if mode in ("data", "hybrid"):
        data = text2sql.run_data_query(body.question)

    context = ""
    if chunks:
        context += "## 서사 자료 (위키)\n" + "\n\n".join(
            f"[{c['path']}]\n{c['text']}" for c in chunks
        )
    if data["sql"]:
        context += (f"\n\n## 데이터 자료 (SQL: {data['sql']})\n"
                    + (f"오류: {data['error']}" if data["error"]
                       else json.dumps(data["rows"], ensure_ascii=False, default=str)))
    if not context:
        context = "(자료 없음)"
    answer = llm.generate("synthesize",
                          SYNTH_PROMPT.format(context=context, question=body.question))
    return {
        "answer": answer,
        "mode": mode,
        "citations": [{"path": c["path"]} for c in chunks],
        "sql": data["sql"],
        "sql_rows": data["rows"],
        "sql_error": data["error"],
    }
