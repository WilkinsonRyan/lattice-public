"""SQLite storage for Lattice.

Every table lives here. Nothing above this layer writes SQL — the core layer
calls these functions, and future interfaces (an API, a phone client) call the
same core. That boundary is the point of the whole design.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    path        TEXT NOT NULL UNIQUE,
    side        TEXT NOT NULL DEFAULT 'unknown',   -- work | personal | unknown
    description TEXT,
    kind        TEXT,                              -- python, node, arduino, ...
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY,
    project_id  INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'note',
    content     TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'cli',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id           INTEGER PRIMARY KEY,
    project_id   INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'open',     -- open | done
    created_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id         INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    decision   TEXT NOT NULL,
    rationale  TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS patterns (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    title             TEXT NOT NULL,
    description       TEXT,
    seen_count        INTEGER NOT NULL DEFAULT 1,
    promoted_to_skill INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    path        TEXT,
    status      TEXT NOT NULL DEFAULT 'draft',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id                INTEGER PRIMARY KEY,
    project_id        INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    command_or_action TEXT NOT NULL,
    result_summary    TEXT,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id         INTEGER PRIMARY KEY,
    project_id INTEGER REFERENCES projects(id) ON DELETE SET NULL,
    interface  TEXT NOT NULL DEFAULT 'claude-code',
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    summary    TEXT
);

-- Full-text search over memories. FTS5 ships with Python's sqlite3 on macOS,
-- and at this scale it finds things as well as a vector database would, for
-- none of the operational cost.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
    USING fts5(content, content='memories', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content)
        VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def now() -> str:
    """UTC timestamp, second resolution, sorts lexically."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# --- projects ---------------------------------------------------------------

def add_project(conn, name, path, side="unknown", description=None, kind=None):
    ts = now()
    cur = conn.execute(
        "INSERT INTO projects (name, path, side, description, kind, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (name, str(path), side, description, kind, ts, ts),
    )
    conn.commit()
    return cur.lastrowid


def get_project(conn, ident):
    """Look a project up by id, exact name, or path."""
    row = None
    if str(ident).isdigit():
        row = conn.execute("SELECT * FROM projects WHERE id=?", (int(ident),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM projects WHERE name=?", (str(ident),)).fetchone()
    if row is None:
        row = conn.execute("SELECT * FROM projects WHERE path=?", (str(ident),)).fetchone()
    return row


def list_projects(conn, side=None):
    if side:
        return conn.execute(
            "SELECT * FROM projects WHERE side=? ORDER BY side, name", (side,)
        ).fetchall()
    return conn.execute("SELECT * FROM projects ORDER BY side, name").fetchall()


def touch_project(conn, project_id):
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), project_id))
    conn.commit()


# --- memories ---------------------------------------------------------------

def add_memory(conn, project_id, content, mtype="note", source="cli"):
    cur = conn.execute(
        "INSERT INTO memories (project_id, type, content, source, created_at)"
        " VALUES (?,?,?,?,?)",
        (project_id, mtype, content, source, now()),
    )
    conn.commit()
    return cur.lastrowid


def recent_memories(conn, project_id=None, limit=10):
    if project_id:
        return conn.execute(
            "SELECT * FROM memories WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM memories ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()


def search_memories(conn, query, limit=20):
    """Full-text search. Returns memories joined to their project name."""
    return conn.execute(
        """SELECT m.*, p.name AS project_name
             FROM memories_fts f
             JOIN memories m ON m.id = f.rowid
        LEFT JOIN projects p ON p.id = m.project_id
            WHERE memories_fts MATCH ?
         ORDER BY rank LIMIT ?""",
        (query, limit),
    ).fetchall()


# --- tasks ------------------------------------------------------------------

def add_task(conn, project_id, title):
    cur = conn.execute(
        "INSERT INTO tasks (project_id, title, created_at) VALUES (?,?,?)",
        (project_id, title, now()),
    )
    conn.commit()
    return cur.lastrowid


def list_tasks(conn, project_id=None, status="open"):
    sql = "SELECT t.*, p.name AS project_name FROM tasks t LEFT JOIN projects p ON p.id=t.project_id"
    where, args = [], []
    if project_id:
        where.append("t.project_id=?")
        args.append(project_id)
    if status:
        where.append("t.status=?")
        args.append(status)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY t.id"
    return conn.execute(sql, args).fetchall()


def complete_task(conn, task_id):
    cur = conn.execute(
        "UPDATE tasks SET status='done', completed_at=? WHERE id=? AND status='open'",
        (now(), task_id),
    )
    conn.commit()
    return cur.rowcount


# --- patterns / decisions / skills / logs -----------------------------------

def add_pattern(conn, project_id, title, description=None):
    """Record a pattern. Seeing the same title again bumps its count rather than
    duplicating it — that count is what later justifies promotion to a skill."""
    row = conn.execute("SELECT id, seen_count FROM patterns WHERE title=?", (title,)).fetchone()
    if row:
        conn.execute("UPDATE patterns SET seen_count=seen_count+1 WHERE id=?", (row["id"],))
        conn.commit()
        return row["id"]
    cur = conn.execute(
        "INSERT INTO patterns (project_id, title, description, created_at) VALUES (?,?,?,?)",
        (project_id, title, description, now()),
    )
    conn.commit()
    return cur.lastrowid


def list_patterns(conn):
    return conn.execute(
        "SELECT pa.*, p.name AS project_name FROM patterns pa"
        " LEFT JOIN projects p ON p.id=pa.project_id ORDER BY pa.seen_count DESC, pa.id"
    ).fetchall()


def add_decision(conn, project_id, decision, rationale=None):
    cur = conn.execute(
        "INSERT INTO decisions (project_id, decision, rationale, created_at) VALUES (?,?,?,?)",
        (project_id, decision, rationale, now()),
    )
    conn.commit()
    return cur.lastrowid


def list_skills(conn):
    return conn.execute("SELECT * FROM skills ORDER BY name").fetchall()


def log_execution(conn, project_id, action, summary=None):
    conn.execute(
        "INSERT INTO execution_logs (project_id, command_or_action, result_summary, created_at)"
        " VALUES (?,?,?,?)",
        (project_id, action, summary, now()),
    )
    conn.commit()


# --- sessions ---------------------------------------------------------------

def start_session(conn, project_id=None, interface="claude-code"):
    cur = conn.execute(
        "INSERT INTO sessions (project_id, interface, started_at) VALUES (?,?,?)",
        (project_id, interface, now()),
    )
    conn.commit()
    return cur.lastrowid


def end_session(conn, session_id, summary=None):
    conn.execute(
        "UPDATE sessions SET ended_at=?, summary=? WHERE id=?",
        (now(), summary, session_id),
    )
    conn.commit()


def open_session(conn, interface="claude-code"):
    """Most recent session that never got closed, if any."""
    return conn.execute(
        "SELECT * FROM sessions WHERE ended_at IS NULL AND interface=?"
        " ORDER BY id DESC LIMIT 1", (interface,),
    ).fetchone()


# --- skills -----------------------------------------------------------------

def add_skill(conn, name, description=None, path=None, status="draft"):
    cur = conn.execute(
        "INSERT OR IGNORE INTO skills (name, description, path, status, created_at)"
        " VALUES (?,?,?,?,?)",
        (name, description, str(path) if path else None, status, now()),
    )
    conn.commit()
    return cur.lastrowid


def get_pattern(conn, ident):
    if str(ident).isdigit():
        row = conn.execute("SELECT * FROM patterns WHERE id=?", (int(ident),)).fetchone()
        if row:
            return row
    return conn.execute("SELECT * FROM patterns WHERE title=?", (str(ident),)).fetchone()


def mark_pattern_promoted(conn, pattern_id):
    conn.execute("UPDATE patterns SET promoted_to_skill=1 WHERE id=?", (pattern_id,))
    conn.commit()


def list_decisions(conn, project_id=None, limit=20):
    if project_id:
        return conn.execute(
            "SELECT * FROM decisions WHERE project_id=? ORDER BY id DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
    return conn.execute(
        "SELECT d.*, p.name AS project_name FROM decisions d"
        " LEFT JOIN projects p ON p.id=d.project_id ORDER BY d.id DESC LIMIT ?", (limit,)
    ).fetchall()
