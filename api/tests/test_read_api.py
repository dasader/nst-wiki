import os

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")
os.environ.setdefault("READONLY_DATABASE_URL",
                      "postgresql://wiki_ro:ro_devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app
from scripts.init_wiki import init_wiki

client = TestClient(app)


def _wiki_with_page(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    import wiki_ops
    wiki_ops.stage_changes(tmp_path, "sR", {"tech/read-test.md": "# 읽기\n\n검색가능한본문"}, "m")
    wiki_ops.approve_branch(tmp_path, "sR", "approve: m")
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))


def test_wiki_list_and_page(tmp_path, monkeypatch):
    _wiki_with_page(tmp_path, monkeypatch)
    r = client.get("/api/v1/wiki")
    assert "tech/read-test.md" in r.json()["pages"]
    r = client.get("/api/v1/wiki/page", params={"path": "tech/read-test.md"})
    assert r.status_code == 200
    body = r.json()
    assert body["content_md"].startswith("# 읽기")
    assert len(body["history"]) >= 1
    assert client.get("/api/v1/wiki/page", params={"path": "../etc/passwd"}).status_code == 404


def test_wiki_search(tmp_path, monkeypatch):
    _wiki_with_page(tmp_path, monkeypatch)
    r = client.get("/api/v1/wiki/search", params={"q": "검색가능한"})
    assert r.status_code == 200
    assert any(x["path"] == "tech/read-test.md" for x in r.json()["results"])
    assert client.get("/api/v1/wiki/search", params={"q": ""}).status_code == 422


def test_wiki_search_leading_dash_is_pattern(tmp_path, monkeypatch):
    _wiki_with_page(tmp_path, monkeypatch)
    # 옵션 주입 시도 — 패턴으로 취급되어 빈 결과(200)여야 하며, 옵션으로 해석되면 안 됨
    r = client.get("/api/v1/wiki/search", params={"q": "-O"})
    assert r.status_code == 200
    assert r.json()["results"] == []


def test_wiki_staged_only_not_visible(tmp_path, monkeypatch):
    init_wiki(tmp_path)
    monkeypatch.setenv("WIKI_REPO_PATH", str(tmp_path))
    import wiki_ops
    wiki_ops.stage_changes(tmp_path, "sX", {"tech/staged-only.md": "# 미승인"}, "m")
    assert "tech/staged-only.md" not in client.get("/api/v1/wiki").json()["pages"]
    r = client.get("/api/v1/wiki/page", params={"path": "tech/staged-only.md"})
    assert r.status_code == 404


def test_data_table_whitelist_and_query():
    assert client.get("/api/v1/data/ingest_tasks").status_code == 404  # 화이트리스트 밖
    assert client.get("/api/v1/data/technologies",
                      params={"sort_by": "없는컬럼"}).status_code == 400
    r = client.get("/api/v1/data/technologies", params={"limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {"rows", "total", "page", "limit"}


def test_data_table_filter_and_sort():
    # 잘못된 필터 컬럼·정렬 방향은 400 (화이트리스트/열거 밖)
    assert client.get("/api/v1/data/technologies",
                      params={"column": "없는컬럼", "q": "x"}).status_code == 400
    assert client.get("/api/v1/data/technologies",
                      params={"sort_by": "name", "order": "sideways"}).status_code == 400
    # 필터: 반환된 모든 행의 field가 검색어를 포함 (ILIKE)
    r = client.get("/api/v1/data/technologies", params={"column": "field", "q": "인공지능"})
    assert r.status_code == 200
    assert all("인공지능" in (row["field"] or "") for row in r.json()["rows"])
    # 정렬: desc는 asc의 역순 (DB 콜레이션에 무관하게 order 파라미터가 반영되는지)
    asc = [r["name"] for r in client.get("/api/v1/data/technologies",
           params={"sort_by": "name", "order": "asc", "limit": 200}).json()["rows"]]
    desc = [r["name"] for r in client.get("/api/v1/data/technologies",
            params={"sort_by": "name", "order": "desc", "limit": 200}).json()["rows"]]
    assert desc == asc[::-1] and len(asc) > 1
