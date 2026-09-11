from kotwica import db


def test_init_schema_is_idempotent(tmp_path):
    c = db.connect(str(tmp_path / "k.db"))
    db.init_schema(c)
    db.init_schema(c)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"vessels", "positions", "vessel_state", "alerts", "scenes", "slicks",
            "slick_suspects", "reports", "meta"} <= tables


def test_wal_enabled(conn):
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_meta_roundtrip(conn):
    assert db.get_meta(conn, "missing", "x") == "x"
    db.set_meta(conn, "k", 1)
    db.set_meta(conn, "k", 2)
    assert db.get_meta(conn, "k") == "2"


def test_readonly_connection_sees_writes(tmp_path):
    path = str(tmp_path / "k.db")
    w = db.connect(path)
    db.init_schema(w)
    db.set_meta(w, "k", "v")
    w.commit()
    r = db.connect(path, readonly=True)
    assert db.get_meta(r, "k") == "v"
