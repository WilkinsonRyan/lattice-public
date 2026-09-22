import sqlite3
from lattice import storage


def test_schema_creates_every_table(tmp_path):
    conn = storage.connect(tmp_path / "t.sqlite")
    storage.init_schema(conn)
    names = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("projects", "memories", "tasks", "decisions",
              "patterns", "skills", "execution_logs"):
        assert t in names


def test_init_schema_is_idempotent(tmp_path):
    conn = storage.connect(tmp_path / "t.sqlite")
    storage.init_schema(conn)
    storage.init_schema(conn)          # must not raise


def test_full_text_search_finds_a_memory(tmp_path):
    conn = storage.connect(tmp_path / "t.sqlite")
    storage.init_schema(conn)
    pid = storage.add_project(conn, "demo", tmp_path)
    storage.add_memory(conn, pid, "the CuraEngine binary lives inside Cura.app")
    storage.add_memory(conn, pid, "something entirely unrelated")
    hits = storage.search_memories(conn, "CuraEngine")
    assert len(hits) == 1
    assert "CuraEngine" in hits[0]["content"]


def test_search_reflects_deletes(tmp_path):
    conn = storage.connect(tmp_path / "t.sqlite")
    storage.init_schema(conn)
    pid = storage.add_project(conn, "demo", tmp_path)
    mid = storage.add_memory(conn, pid, "ephemeral fact about widgets")
    assert storage.search_memories(conn, "widgets")
    conn.execute("DELETE FROM memories WHERE id=?", (mid,)); conn.commit()
    assert not storage.search_memories(conn, "widgets")


def test_duplicate_project_path_is_rejected(tmp_path):
    conn = storage.connect(tmp_path / "t.sqlite")
    storage.init_schema(conn)
    storage.add_project(conn, "a", tmp_path)
    try:
        storage.add_project(conn, "b", tmp_path)
        assert False, "duplicate path should not be allowed"
    except sqlite3.IntegrityError:
        pass
