import os

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _patch(monkeypatch, route="narrative", chunks=None, data=None):
    from app import query_api
    monkeypatch.setattr(query_api.llm, "generate", _fake_llm(route))
    monkeypatch.setattr(query_api.search, "search_wiki",
                        lambda q, limit=5: chunks if chunks is not None else [])
    monkeypatch.setattr(query_api.text2sql, "run_data_query",
                        lambda q: data or {"sql": None, "rows": [], "error": None})


def _fake_llm(route):
    def fake(purpose, contents, schema=None):
        if purpose == "route_query":
            return {"mode": route}
        if purpose == "synthesize":
            return "합성된 답변"
        raise AssertionError(purpose)
    return fake


def test_query_narrative(monkeypatch):
    _patch(monkeypatch, route="narrative",
           chunks=[{"path": "tech/a.md", "text": "본문", "score": 0.9}])
    r = client.post("/api/v1/query", json={"question": "HBM이 뭐야?"})
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "narrative"
    assert body["answer"] == "합성된 답변"
    assert body["citations"] == [{"path": "tech/a.md"}]
    assert body["sql"] is None


def test_query_data_mode_explicit(monkeypatch):
    _patch(monkeypatch, data={"sql": "SELECT 1", "rows": [{"c": 1}], "error": None})
    r = client.post("/api/v1/query", json={"question": "예산 합계?", "mode": "data"})
    body = r.json()
    assert body["mode"] == "data"
    assert body["sql"] == "SELECT 1"
    assert body["sql_rows"] == [{"c": 1}]


def test_query_citations_filtered_to_used_paths(monkeypatch):
    from app import query_api
    def fake(purpose, contents, schema=None):
        if purpose == "synthesize":
            return "답변입니다 [tech/a.md]"
        raise AssertionError(purpose)
    monkeypatch.setattr(query_api.llm, "generate", fake)
    monkeypatch.setattr(query_api.search, "search_wiki", lambda q, limit=5: [
        {"path": "tech/a.md", "text": "본문", "score": 0.9},
        {"path": "tech/b.md", "text": "다른", "score": 0.5},
        {"path": "tech/a.md", "text": "본문2", "score": 0.4},
    ])
    r = client.post("/api/v1/query", json={"question": "q", "mode": "narrative"})
    assert r.json()["citations"] == [{"path": "tech/a.md"}]  # 사용된 것만, 중복 제거


def test_query_hybrid_with_sql_error(monkeypatch):
    from app import query_api
    captured = {}
    def fake(purpose, contents, schema=None):
        if purpose == "route_query":
            return {"mode": "hybrid"}
        if purpose == "synthesize":
            captured["context"] = contents
            return "합성"
        raise AssertionError(purpose)
    monkeypatch.setattr(query_api.llm, "generate", fake)
    monkeypatch.setattr(query_api.search, "search_wiki",
                        lambda q, limit=5: [{"path": "tech/a.md", "text": "본문", "score": 0.9}])
    monkeypatch.setattr(query_api.text2sql, "run_data_query",
                        lambda q: {"sql": "SELECT bad", "rows": [], "error": "syntax error"})
    r = client.post("/api/v1/query", json={"question": "q"})
    body = r.json()
    assert body["mode"] == "hybrid"
    assert body["sql_error"] == "syntax error"
    assert "오류: syntax error" in captured["context"]
    assert "tech/a.md" in captured["context"]


def test_query_invalid_mode_422():
    assert client.post("/api/v1/query", json={"question": "q", "mode": "foo"}).status_code == 422


def test_query_rate_limited(monkeypatch):
    _patch(monkeypatch, route="narrative", chunks=[])
    from app import query_api
    monkeypatch.setattr(query_api, "RATE_LIMIT", 2)
    query_api._hits.clear()
    for _ in range(2):
        assert client.post("/api/v1/query",
                           json={"question": "q", "mode": "narrative"}).status_code == 200
    assert client.post("/api/v1/query",
                       json={"question": "q", "mode": "narrative"}).status_code == 429
    query_api._hits.clear()
