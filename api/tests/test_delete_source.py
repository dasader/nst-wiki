import os
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql://wiki:devpass@127.0.0.1:5433/llm_wiki")

from app import db


def test_delete_source_removes_all_rows_across_tables():
    """일회용 uuid source_id로 각 테이블에 행을 만들고, delete_source가 전부 지우는지 검증.

    실데이터를 건드리지 않으려고 무작위 source_id만 쓰고 finally에서 정리한다.
    """
    sid = str(uuid.uuid4())
    other_sid = str(uuid.uuid4())
    ids = {}
    try:
        with db.connect() as conn:
            ids["tech"] = conn.execute(
                "INSERT INTO technologies (name, field, source_id) VALUES (%s, '반도체', %s) RETURNING id",
                (f"삭제기술-{sid[:8]}", sid),
            ).fetchone()["id"]
            ids["proj"] = conn.execute(
                "INSERT INTO projects (name, source_id) VALUES (%s, %s) RETURNING id",
                (f"삭제사업-{sid[:8]}", sid),
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO budget_history (project_id, fiscal_year, amount, source_id) "
                "VALUES (%s, 2026, 100, %s)", (ids["proj"], sid),
            )
            conn.execute(
                "INSERT INTO tech_project_mapping (technology_id, project_id, mapping_source) "
                "VALUES (%s, %s, 'llm_inferred')", (ids["tech"], ids["proj"]),
            )
            conn.execute(
                "INSERT INTO policy_events (event_date, event_type, title, source_id) "
                "VALUES ('2026-01-01', 'launch', %s, %s)", (f"이벤트-{sid[:8]}", sid),
            )
            # 다른 소스의 tech은 남아야 한다 (매핑이 없어 고아 삭제 대상 아님)
            ids["other_tech"] = conn.execute(
                "INSERT INTO technologies (name, field, source_id) VALUES (%s, '반도체', %s) RETURNING id",
                (f"타소스기술-{other_sid[:8]}", other_sid),
            ).fetchone()["id"]
            # staging 잔재
            conn.execute(
                "INSERT INTO staging.technologies (name, field, source_id) VALUES (%s, '반도체', %s)",
                (f"staging-{sid[:8]}", sid),
            )

        counts = db.delete_source(sid)
        assert counts["technologies"] == 1
        assert counts["projects"] == 1
        assert counts["policy_events"] == 1
        assert counts["budget_history"] == 1
        assert counts["tech_project_mapping"] == 1
        assert counts["staging"] == 1

        with db.connect() as conn:
            for t in ("technologies", "projects", "policy_events", "budget_history"):
                n = conn.execute(
                    f"SELECT count(*) AS n FROM {t} WHERE source_id=%s", (sid,)
                ).fetchone()["n"]
                assert n == 0, t
            n = conn.execute(
                "SELECT count(*) AS n FROM tech_project_mapping WHERE technology_id=%s",
                (ids["tech"],),
            ).fetchone()["n"]
            assert n == 0
            # 다른 소스는 그대로
            assert conn.execute(
                "SELECT count(*) AS n FROM technologies WHERE id=%s", (ids["other_tech"],)
            ).fetchone()["n"] == 1
    finally:
        with db.connect() as conn:
            conn.execute("DELETE FROM tech_project_mapping WHERE technology_id=%s",
                         (ids.get("tech"),))
            conn.execute("DELETE FROM budget_history WHERE source_id IN (%s, %s)", (sid, other_sid))
            conn.execute("DELETE FROM policy_events WHERE source_id IN (%s, %s)", (sid, other_sid))
            conn.execute("DELETE FROM projects WHERE source_id IN (%s, %s)", (sid, other_sid))
            conn.execute("DELETE FROM technologies WHERE source_id IN (%s, %s)", (sid, other_sid))
            conn.execute("DELETE FROM staging.technologies WHERE source_id IN (%s, %s)", (sid, other_sid))


def test_delete_source_empty_is_noop():
    counts = db.delete_source(str(uuid.uuid4()))  # 존재하지 않는 소스 — 전부 0
    assert all(v == 0 for v in counts.values())
