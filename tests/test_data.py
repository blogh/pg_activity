import time

import pytest
from psycopg2.errors import WrongObjectType

from pgactivity.data import Data


def wait_for_data(fct, timeout: int = 2):
    count = int(timeout / 0.1)
    for _ in range(count):
        time.sleep(0.1)
        data = fct()

        if not data:
            continue
        break
    else:
        raise AssertionError("timeout")
    return data


@pytest.fixture
def data(postgresql):
    return Data.pg_connect(
        1,
        host=postgresql.info.host,
        port=postgresql.info.port,
        database=postgresql.info.dbname,
        user=postgresql.info.user,
    )


def test_pg_is_local(postgresql, data):
    assert data.pg_is_local()


def test_pg_is_local_access(postgresql, data):
    assert data.pg_is_local_access()


def test_pg_get_db_info(data):
    dbinfo = data.pg_get_db_info(None)
    assert set(dbinfo) == {
        "timestamp",
        "no_xact",
        "total_size",
        "max_length",
        "tps",
        "size_ev",
    }


def test_activities(postgresql, data, execute):
    with postgresql.cursor() as cur:
        cur.execute("SELECT pg_sleep(1)")
    (running,) = data.pg_get_activities()
    assert "pg_sleep" in running.query
    assert running.state == "idle in transaction"
    if postgresql.server_version >= 100000:
        assert running.wait == "ClientRead"
    else:
        assert running.wait is None
    assert not running.is_parallel_worker


def test_blocking_waiting(postgresql, data, execute):
    with postgresql.cursor() as cur:
        cur.execute("CREATE TABLE t (s text)")
    postgresql.commit()
    execute("INSERT INTO t VALUES ('init')", commit=True)
    execute("UPDATE t SET s = 'blocking'")
    execute("UPDATE t SET s = 'waiting'", commit=True)
    (blocking,) = wait_for_data(data.pg_get_blocking)
    (waiting,) = data.pg_get_waiting()
    assert "blocking" in blocking.query
    assert "waiting" in waiting.query
    if postgresql.server_version >= 100000:
        assert blocking.wait == "ClientRead"


def test_cancel_backend(postgresql, data, execute):
    with postgresql.cursor() as cur:
        cur.execute("SELECT pg_sleep(1)")
    (running,) = data.pg_get_activities()
    assert data.pg_cancel_backend(running.pid)


def test_terminate_backend(postgresql, data, execute):
    with postgresql.cursor() as cur:
        cur.execute("SELECT pg_sleep(1)")
    (running,) = data.pg_get_activities()
    assert data.pg_terminate_backend(running.pid)
    assert not data.pg_get_activities()


def test_pg_get_active_connections(data, execute):
    assert data.pg_get_active_connections() == 1
    execute("select pg_sleep(2)")
    nbconn = wait_for_data(data.pg_get_active_connections)
    assert nbconn == 2


def test_encoding(postgresql, data, execute):
    """Test for issue #149"""
    postgresql.set_session(autocommit=True)
    with postgresql.cursor() as cur:
        """plateform specific locales"""
        try:
            cur.execute(
                "CREATE DATABASE latin1 ENCODING 'latin1' TEMPLATE template0 LC_COLLATE 'fr_FR.latin1' LC_CTYPE 'fr_FR.latin1'"
            )
        except WrongObjectType:
            cur.execute(
                "CREATE DATABASE latin1 ENCODING 'latin1' TEMPLATE template0 LC_COLLATE 'fr_FR.88591' LC_CTYPE 'fr_FR.88591'"
            )

    postgresql.set_session(autocommit=False)
    execute("CREATE TABLE tbl(s text)", dbname="latin1", commit=True)
    execute(
        "INSERT INTO tbl(s) VALUES ('initilialized éléphant')",
        dbname="latin1",
        commit=True,
    )
    execute("UPDATE tbl SET s = 'blocking éléphant'", dbname="latin1")
    execute("UPDATE tbl SET s = 'waiting éléphant'", dbname="latin1", commit=True)
    running = wait_for_data(data.pg_get_activities)
    assert "éléphant" in running[0].query
    (waiting,) = data.pg_get_waiting()
    assert "éléphant" in waiting.query
    (blocking,) = data.pg_get_blocking()
    assert "éléphant" in blocking.query
