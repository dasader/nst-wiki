import os
import uuid as _uuid

os.environ.setdefault("ADMIN_API_KEY", "testkey")
os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_wiki_edit_requires_admin():
    r = client.put("/api/v1/wiki/page", headers={"X-Admin-Key": "wrong"},
                   json={"path": "tech/a.md", "content_md": "x"})
    assert r.status_code == 401


def test_wiki_edit_rejects_bad_path(monkeypatch):
    from app import read_api
    monkeypatch.setattr(read_api, "_main_pages", lambda root: [])
    r = client.put("/api/v1/wiki/page", headers={"X-Admin-Key": "testkey"},
                   json={"path": "../evil.md", "content_md": "x"})
    assert r.status_code == 400


def test_wiki_edit_writes_new_page(monkeypatch):
    from app import read_api
    import tasks as tasks_mod
    monkeypatch.setattr(read_api, "_main_pages", lambda root: [])  # 새 페이지
    written = {}
    monkeypatch.setattr(read_api.wiki_ops, "write_page",
                        lambda root, rel, content, msg: written.update(rel=rel, c=content, m=msg) or True)
    embed = []
    monkeypatch.setattr(tasks_mod.embed_pages, "delay", lambda paths: embed.append(paths))
    r = client.put("/api/v1/wiki/page", headers={"X-Admin-Key": "testkey"},
                   json={"path": "tech/새기술.md", "content_md": "# 새\n본문"})
    assert r.status_code == 200
    body = r.json()
    assert body["committed"] is True and body["created"] is True
    assert written["rel"] == "tech/새기술.md"
    assert embed == [["tech/새기술.md"]]


def test_wiki_edit_existing_page_no_embed_when_unchanged(monkeypatch):
    from app import read_api
    import tasks as tasks_mod
    monkeypatch.setattr(read_api, "_main_pages", lambda root: ["tech/a.md"])
    monkeypatch.setattr(read_api.wiki_ops, "write_page",
                        lambda root, rel, content, msg: False)  # 변경 없음
    embed = []
    monkeypatch.setattr(tasks_mod.embed_pages, "delay", lambda paths: embed.append(paths))
    r = client.put("/api/v1/wiki/page", headers={"X-Admin-Key": "testkey"},
                   json={"path": "tech/a.md", "content_md": "동일"})
    assert r.status_code == 200
    assert r.json()["committed"] is False
    assert embed == []  # 변경 없으면 재색인 안 함
