from pathlib import Path

from scripts.init_wiki import init_wiki
import pipeline.narrative as narrative


def test_group_resolutions_maps_cid_to_page():
    contradictions = [
        {"page": "tech/a.md", "summary": "s1", "existing": "e1", "new": "n1"},
        {"page": "tech/a.md", "summary": "s2", "existing": "e2", "new": "n2"},
        {"page": "tech/b.md", "summary": "s3", "existing": "e3", "new": "n3"},
    ]
    resolutions = {"abcd1234-1": "replace", "abcd1234-2": "keep"}  # 3번은 미선택
    by_page = narrative.group_resolutions("abcd1234ffff", contradictions, resolutions)
    assert set(by_page) == {"tech/a.md"}                     # b.md는 결정 없어 제외
    assert [d["action"] for d in by_page["tech/a.md"]] == ["replace", "keep"]
    assert by_page["tech/a.md"][0]["new"] == "n1"            # 원 모순 필드 보존


def _fake_llm(plan_pages, merged, summary="## 개요\n요약 본문"):
    def fake(purpose, contents, schema=None):
        if purpose == "plan_pages":
            return {"pages": plan_pages}
        if purpose == "merge_page":
            return merged
        if purpose == "summarize_source":
            return summary
        raise AssertionError(f"unexpected purpose: {purpose}")
    return fake


def test_compile_narrative_builds_files(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/hbm-semiconductor.md", "action": "create", "title": "HBM 반도체"}],
        merged={"content": "---\ntitle: HBM 반도체\n---\n\n본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src1", {"title": "정책문서"}, ["HBM 설명 서사"])
    assert out["files"]["tech/hbm-semiconductor.md"].endswith("본문")
    assert "summaries/src1.md" in out["files"]
    assert out["affected_pages"] == [{"path": "tech/hbm-semiconductor.md", "action": "create"}]
    assert out["contradictions"] == []


def test_compile_narrative_inlines_unmapped_tables(tmp_path, monkeypatch):
    """티어 3: 매핑 실패 표 마크다운이 요약 페이지 부록으로 인라인된다."""
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/hbm-semiconductor.md", "action": "create", "title": "HBM"}],
        merged={"content": "본문", "contradictions": []},
    ))
    tables = ["**비교표**\n\n| 항목 | 값 |\n| --- | --- |\n| A | 1 |"]
    out = narrative.compile_narrative(tmp_path, "src1", {"title": "정책문서"},
                                      ["서사"], inline_tables=tables)
    summary = out["files"]["summaries/src1.md"]
    assert "## 부록: 미분류 표" in summary
    assert "| 항목 | 값 |" in summary and "비교표" in summary
    # inline_tables 없으면 부록 섹션도 없다
    out2 = narrative.compile_narrative(tmp_path, "src2", {"title": "정책문서"}, ["서사"])
    assert "부록: 미분류 표" not in out2["files"]["summaries/src2.md"]


def test_pure_table_doc_still_preserves_tables(tmp_path, monkeypatch):
    """서사 청크가 없는(순수 표) 문서도 미분류 표는 요약 페이지에 보존된다 — LLM 호출 없이."""
    init_wiki(tmp_path)
    # 서사가 없으면 plan_pages·merge_page·summarize를 부르면 안 된다 — 부르면 테스트 실패
    def _boom(*a, **k):
        raise AssertionError("서사 없는 문서에서 LLM을 호출하면 안 됨")
    monkeypatch.setattr(narrative.llm, "generate", _boom)
    tables = ["**비교표**\n\n| 항목 | 값 |\n| --- | --- |\n| A | 1 |"]
    out = narrative.compile_narrative(tmp_path, "src9", {"title": "표만 있는 문서"},
                                      [], inline_tables=tables)
    summary = out["files"]["summaries/src9.md"]
    assert "## 부록: 미분류 표" in summary and "| 항목 | 값 |" in summary
    # 토픽 페이지는 만들어지지 않는다 — 요약 페이지 하나뿐
    assert list(out["files"].keys()) == ["summaries/src9.md"]


def test_compile_narrative_records_contradictions(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "update", "title": "A"}],
        merged={"content": "새 본문", "contradictions": [
            {"summary": "분야 수 불일치", "existing": "12개", "new": "10개"}
        ]},
    ))
    out = narrative.compile_narrative(tmp_path, "src2", {"title": "문서"}, ["서사"])
    assert len(out["contradictions"]) == 1
    assert "분야 수 불일치" in out["files"]["contradictions/log.md"]


def test_contradiction_lands_in_page_frontmatter(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "update", "title": "A"}],
        merged={"content": "---\ntitle: A\n---\n\n본문", "contradictions": [
            {"summary": "분야 수 불일치", "existing": "12개", "new": "10개"}
        ]},
    ))
    out = narrative.compile_narrative(tmp_path, "src5", {"title": "문서"}, ["서사"])
    page = out["files"]["tech/a.md"]
    assert "unresolved_contradictions:" in page.split("---")[1]  # 프론트매터 안
    assert "분야 수 불일치" in page
    assert page.endswith("본문")  # 본문 보존


def test_contradiction_frontmatter_idempotent(tmp_path):
    once = narrative._inject_contradictions(
        "---\ntitle: A\n---\n\n본문", [{"summary": "s", "existing": "e", "new": "n"}])
    twice = narrative._inject_contradictions(
        once, [{"summary": "s", "existing": "e", "new": "n"}])
    assert once.count("unresolved_contradictions:") == 1
    assert twice.count("unresolved_contradictions:") == 1
    assert twice.count('- "s') == 1  # 재병합해도 중복 안 됨


def test_compile_narrative_rejects_bad_paths(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[
            {"path": "../escape.md", "action": "create", "title": "X"},
            {"path": "unknown/a.md", "action": "create", "title": "Y"},
            {"path": "tech/ok.md", "action": "create", "title": "OK"},
        ],
        merged={"content": "본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src4", {"title": "문서"}, ["서사"])
    assert [p["action"] for p in out["affected_pages"]] == ["rejected", "rejected", "create"]
    assert list(f for f in out["files"] if f.endswith("escape.md")) == []


def test_compile_narrative_caps_at_15(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    pages = [{"path": f"tech/t{i}.md", "action": "create", "title": f"T{i}"} for i in range(20)]
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=pages, merged={"content": "본문", "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src3", {"title": "문서"}, ["서사"])
    updated = [p for p in out["affected_pages"] if p["action"] != "suggested"]
    suggested = [p for p in out["affected_pages"] if p["action"] == "suggested"]
    assert len(updated) == 15
    assert len(suggested) == 5
    assert sum(1 for f in out["files"] if f.startswith("tech/")) == 15


def test_compile_narrative_prunes_dead_links(tmp_path, monkeypatch):
    """LLM이 만든 '없는 페이지' 링크는 평문으로 낮춘다 (schema.md: 깨진 링크 금지).
    실존/동일배치 생성 페이지 링크와 [[data:...]] 참조는 보존한다."""
    init_wiki(tmp_path)
    content = ("본문 [[tech/a]] [[laws/국가전략기술육성법]] [[tech/없는페이지]] "
               "[[data:technologies?field=반도체]]")
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "create", "title": "A"}],
        merged={"content": content, "contradictions": []},
    ))
    out = narrative.compile_narrative(tmp_path, "src4", {"title": "문서"}, ["서사"])
    md = out["files"]["tech/a.md"]
    assert "[[tech/a]]" in md                            # 같은 배치에서 생성 → 유지
    assert "[[data:technologies?field=반도체]]" in md      # data 참조는 그대로
    assert "[[laws/국가전략기술육성법]]" not in md          # 없는 디렉토리 → 평문화
    assert "laws/국가전략기술육성법" in md
    assert "[[tech/없는페이지]]" not in md                 # 없는 페이지 → 평문화
    assert "tech/없는페이지" in md


def test_summary_page_uses_llm_summary(tmp_path, monkeypatch):
    """요약 페이지 본문은 원문 앞부분 복사가 아니라 LLM 요약이다."""
    init_wiki(tmp_path)
    long_narr = "\n".join(f"line{i:02d}" + "x" * 100 for i in range(40))  # >2000자
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[], merged={"content": "", "contradictions": []},
        summary="## 개요\n국가전략기술 12대 분야를 선정한다.",
    ))
    page = narrative.compile_narrative(
        tmp_path, "src5", {"title": "문서"}, [long_narr])["files"]["summaries/src5.md"]
    assert "국가전략기술 12대 분야를 선정한다." in page
    assert "xxxxx" not in page      # 원문 발췌가 아님
    assert "발췌" not in page       # 절단 표시도 없음


def test_summary_falls_back_to_excerpt_when_llm_fails(tmp_path, monkeypatch):
    """요약 호출이 실패해도 인제스트를 죽이지 않고 원문 발췌로 대체한다."""
    init_wiki(tmp_path)
    long_narr = "\n".join(f"line{i:02d}" + "x" * 100 for i in range(40))

    def fake(purpose, contents, schema=None):
        if purpose == "summarize_source":
            raise RuntimeError("gemini down")
        return {"pages": []} if purpose == "plan_pages" else {"content": "", "contradictions": []}

    monkeypatch.setattr(narrative.llm, "generate", fake)
    page = narrative.compile_narrative(
        tmp_path, "src6", {"title": "문서"}, [long_narr])["files"]["summaries/src6.md"]
    assert "발췌" in page and "line00" in page


def test_excerpt_cuts_on_line_boundary_and_marks_truncation():
    """발췌는 줄 경계에서 자르고, 잘렸으면 명시한다 (no silent caps)."""
    text = "\n".join(f"line{i:02d}" + "x" * 100 for i in range(40))   # 각 줄 106자
    out = narrative._excerpt(text)
    assert "발췌" in out
    for ln in out.split("\n\n_(", 1)[0].splitlines():
        assert len(ln) == 106, f"줄이 중간에서 잘림: {ln[-20:]!r}"


def test_excerpt_leaves_short_text_untouched():
    assert narrative._excerpt("짧은 서사입니다.") == "짧은 서사입니다."


def test_compile_narrative_unwraps_dead_data_refs_keeping_value(tmp_path, monkeypatch):
    """없는 테이블의 [[data:...]] 참조는 조건의 '값'을 평문으로 남긴다.

    LLM은 숫자를 참조 안에 넣어 산문에 인라인으로 쓴다 — 통째로 지우면 문장이 깨진다.
    실존 테이블 참조는 링크 그대로 보존한다.
    """
    init_wiki(tmp_path)
    content = ("주요국은 [[data:전략기술?범위=10~20]]개 내외를 선정했고, "
               "우리는 [[data:전략기술?분야수=12]]대 기술을 골랐다. "
               "값 없는 참조 [[data:전략기술]]는 지운다. "
               "예산은 [[data:budget_history?fiscal_year=2024]] 참조.")
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "create", "title": "A"}],
        merged={"content": content, "contradictions": []},
    ))
    md = narrative.compile_narrative(
        tmp_path, "src7", {"title": "문서"}, ["서사"])["files"]["tech/a.md"]

    assert "주요국은 10~20개 내외를 선정했고" in md      # 숫자 보존, 공백 정상
    assert "우리는 12대 기술을 골랐다" in md
    assert "값 없는 참조는 지운다" in md                 # 값 없으면 앞 공백까지 제거
    assert "전략기술?" not in md and "[[data:전략기술" not in md
    assert "[[data:budget_history?fiscal_year=2024]]" in md   # 실존 테이블 → 링크 유지


def test_unpopulated_table_refs_are_not_offered_or_kept(tmp_path, monkeypatch):
    """적재 경로가 없어 항상 비는 테이블은 프롬프트에서 광고하지도, 링크로 남기지도 않는다.

    링크가 남으면 검증은 통과하고 빈 테이블로 안내한다 — 사용자에겐 깨진 링크와 구분되지 않는다.
    """
    assert "tech_project_mapping" not in narrative.DATA_TARGETS
    init_wiki(tmp_path)
    monkeypatch.setattr(narrative.llm, "generate", _fake_llm(
        plan_pages=[{"path": "tech/a.md", "action": "create", "title": "A"}],
        merged={"content": "연관도는 [[data:tech_project_mapping?relevance_score=0.9]] 수준.",
                "contradictions": []},
    ))
    md = narrative.compile_narrative(
        tmp_path, "src8", {"title": "문서"}, ["서사"])["files"]["tech/a.md"]
    assert "연관도는 0.9 수준" in md
    assert "tech_project_mapping" not in md
