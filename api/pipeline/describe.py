"""그림·차트를 Gemini 멀티모달로 해석해 서사 텍스트로 변환. 설명 파일은 캐시로 재사용."""
from pathlib import Path

import llm

PROMPT = """이 그림은 한국 정책문서 「{title}」에서 추출되었다.
그림이 전달하는 내용을 한국어 2~5문장으로 서술하라.
차트라면 추세·비교 관계를 중심으로 서술하되, 눈금에서 읽은 수치는 추정치임을 명시하라.
추진체계도·조직도라면 구성 요소와 관계를 서술하라."""


def describe_figures(parsed_dir: Path, title: str) -> list[dict]:
    figs = parsed_dir / "figures"
    if not figs.is_dir():
        return []
    results = []
    for png in sorted(figs.glob("fig_*.png")):
        desc_path = figs / f"{png.stem}.desc.md"
        if desc_path.exists():
            text = desc_path.read_text(encoding="utf-8")
        else:
            text = llm.generate(
                "describe_figure", [llm.image_part(png), PROMPT.format(title=title)]
            )
            desc_path.write_text(text, encoding="utf-8")
        results.append({"figure": f"figures/{png.name}", "text": text})
    return results
