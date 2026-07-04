"""Stage 2: text 청크를 NARRATIVE/METADATA/SKIP으로 분류. 표·그림은 type으로 즉시 라우팅."""
import json
from pathlib import Path

import llm

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "classifications": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "category": {"type": "string", "enum": ["NARRATIVE", "METADATA", "SKIP"]},
                },
                "required": ["id", "category"],
            },
        }
    },
    "required": ["classifications"],
}

PROMPT = """다음은 한국 정책문서에서 추출한 텍스트 청크 목록이다. 각 청크를 분류하라.

- NARRATIVE: 정책 배경·논리·맥락, 기술·사업 설명, 추진 체계 등 지식 위키에 반영할 서사
- METADATA: 표지, 목차, 서지정보, 발간 정보
- SKIP: 머리글/바닥글, 쪽번호, 의미 없는 조각

청크 목록:
{chunks}"""


def classify_chunks(parsed_dir: Path) -> dict:
    chunks = json.loads((parsed_dir / "chunks.json").read_text(encoding="utf-8"))
    result = {
        "narrative_ids": [],
        "table_ids": [c["id"] for c in chunks if c["type"] == "table"],
        "picture_ids": [c["id"] for c in chunks if c["type"] == "picture"],
    }
    text_chunks = [c for c in chunks if c["type"] == "text"]
    if text_chunks:
        listing = "\n".join(f"[{c['id']}] {c['text'][:500]}" for c in text_chunks)
        out = llm.generate("classify", PROMPT.format(chunks=listing), schema=CLASSIFY_SCHEMA)
        result["narrative_ids"] = [
            x["id"] for x in out["classifications"] if x["category"] == "NARRATIVE"
        ]
    (parsed_dir / "classification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result
