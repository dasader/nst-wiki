import pipeline.describe as describe


def test_describe_writes_desc_and_returns(tmp_path, monkeypatch):
    figs = tmp_path / "figures"
    figs.mkdir(parents=True)
    (figs / "fig_001.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setattr(describe.llm, "image_part", lambda p: {"img": str(p)})
    monkeypatch.setattr(describe.llm, "generate", lambda *a, **k: "추진체계 다이어그램이다.")
    out = describe.describe_figures(tmp_path, "테스트 문서")
    assert out == [{"figure": "figures/fig_001.png", "text": "추진체계 다이어그램이다."}]
    assert (figs / "fig_001.desc.md").read_text(encoding="utf-8") == "추진체계 다이어그램이다."


def test_describe_reuses_existing_desc(tmp_path, monkeypatch):
    figs = tmp_path / "figures"
    figs.mkdir(parents=True)
    (figs / "fig_001.png").write_bytes(b"x")
    (figs / "fig_001.desc.md").write_text("기존 설명", encoding="utf-8")
    def boom(*a, **k):
        raise AssertionError("재사용 시 LLM 호출 금지")
    monkeypatch.setattr(describe.llm, "generate", boom)
    out = describe.describe_figures(tmp_path, "제목")
    assert out == [{"figure": "figures/fig_001.png", "text": "기존 설명"}]


def test_describe_no_figures_dir(tmp_path):
    assert describe.describe_figures(tmp_path, "제목") == []
