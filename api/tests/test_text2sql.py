import os

import pytest

os.environ.setdefault("READONLY_DATABASE_URL",
                      "postgresql://wiki_ro:ro_devpass@127.0.0.1:5433/llm_wiki")

import text2sql


def test_validate_sql_accepts_select():
    assert text2sql.validate_sql("SELECT name FROM technologies").startswith("SELECT")


def test_validate_sql_appends_limit():
    out = text2sql.validate_sql("SELECT name FROM technologies")
    assert out.rstrip().endswith("LIMIT 100")
    kept = text2sql.validate_sql("SELECT name FROM technologies LIMIT 5")
    assert "LIMIT 5" in kept and "LIMIT 100" not in kept


def test_validate_sql_keeps_limit_offset():
    out = text2sql.validate_sql("SELECT name FROM technologies LIMIT 5 OFFSET 10")
    assert out.rstrip().endswith("OFFSET 10")
    assert "LIMIT 100" not in out


@pytest.mark.parametrize("bad", [
    "DELETE FROM technologies",
    "SELECT 1; DROP TABLE technologies",
    "UPDATE technologies SET name='x'",
    "SELECT 1 -- 주석",
    "WITH x AS (SELECT 1) INSERT INTO ministries (name) SELECT 'a'",
    "SELECT * INTO TEMP evil FROM technologies",
])
def test_validate_sql_rejects(bad):
    with pytest.raises(ValueError):
        text2sql.validate_sql(bad)


def test_validate_sql_literal_limit_still_gets_capped():
    out = text2sql.validate_sql("SELECT name FROM technologies WHERE description LIKE '%LIMIT 5%'")
    assert out.rstrip().endswith("LIMIT 100")


def test_run_data_query_executes(monkeypatch):
    monkeypatch.setattr(text2sql.llm, "generate",
                        lambda *a, **k: {"sql": "SELECT 1 AS one"})
    out = text2sql.run_data_query("아무거나")
    assert out["error"] is None
    assert out["rows"] == [{"one": 1}]


def test_run_data_query_blocks_write(monkeypatch):
    monkeypatch.setattr(text2sql.llm, "generate",
                        lambda *a, **k: {"sql": "DELETE FROM technologies"})
    out = text2sql.run_data_query("전부 지워줘")
    assert out["rows"] == []
    assert out["error"] is not None
