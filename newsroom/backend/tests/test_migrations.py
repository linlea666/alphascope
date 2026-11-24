import subprocess
import sys

from sqlalchemy import text

from nr.events.states import TRANSITIONS
from tests.conftest import BACKEND, TEST_DB_URL


def test_upgrade_downgrade_roundtrip(db_engine):
    for args in (["downgrade", "base"], ["upgrade", "head"], ["downgrade", "0002"], ["upgrade", "head"]):
        r = subprocess.run([sys.executable, "-m", "alembic", "-x", f"db_url={TEST_DB_URL}", *args], cwd=BACKEND,
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    with db_engine.connect() as c:
        assert c.execute(text("SELECT version_num FROM alembic_version")).scalar() == "0014"


def test_state_machine_matches_seed(sf):
    with sf() as s:
        seeded = {tuple(r) for r in s.execute(text("SELECT from_state, to_state FROM event_state_transition"))}
    assert seeded == TRANSITIONS


def test_append_only_tables(sf):
    import pytest
    with sf() as s:
        s.execute(text("INSERT INTO audit_log (actor_kind, action, object_type) VALUES ('system', 'x', 'y')"))
        s.commit()
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("UPDATE audit_log SET action = 'z'"))
        s.rollback()
        with pytest.raises(Exception, match="append-only"):
            s.execute(text("DELETE FROM audit_log"))
