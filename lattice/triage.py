"""Triage: the Approve / Deny / Wait / Ignore / Info layer behind the digest.

The digest used to be a read-only page. This module gives every task a plain
sentence explaining it, a fix stated strictly as what Lattice can actually do to
close it, and a recorded action that Approve executes.

Design rule: the fix is not advice. It is the thing Approve runs.
Anything only he can do (rotate a key, order parts, solder a header) is named
separately as his part and never dressed up as a fix.

Secrets never enter this database. An action that removes a line holding a
credential matches on line number plus a non-secret prefix and verifies that
prefix before touching the file; the value is never stored, rendered or logged.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
from pathlib import Path

from .core import data_home, open_db

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS task_guidance (
        task_id     INTEGER PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        explanation TEXT NOT NULL,          -- one plain sentence: what this is
        fix         TEXT,                   -- what Approve does; NULL = nothing Lattice can do
        your_part   TEXT,                   -- what only the user can do
        action_kind TEXT NOT NULL DEFAULT 'none',
        action_spec TEXT,                   -- JSON, interpreted per action_kind
        updated_at  TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS task_events (
        id         INTEGER PRIMARY KEY,
        task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        event      TEXT NOT NULL,           -- approve | deny | wait | ignore | undo
        detail     TEXT,
        backup     TEXT,                    -- where the pre-change copy lives
        created_at TEXT NOT NULL
    )""",
]

# Columns added to task_events.
EVENT_COLUMNS = {"cost_usd": "REAL"}

# Columns added to task_guidance.
GUIDANCE_COLUMNS = {
    # A decision task has no single "approve" — it has one approve per branch.
    # JSON list of {key,label,fix,action_kind,action_spec}.
    "choices": "TEXT",
}

# Columns added to the existing tasks table.
COLUMNS = {
    "snoozed_until": "TEXT",     # hidden from the digest until this date
    "resolution": "TEXT",        # approved | denied | queued
    "resolved_at": "TEXT",
}


def migrate() -> list[str]:
    """Idempotent. Returns what it changed, so a caller can report honestly."""
    conn = open_db()
    done: list[str] = []
    for stmt in SCHEMA:
        name = stmt.split("EXISTS ")[1].split(" ")[0]
        before = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name=?", (name,)).fetchone()[0]
        conn.execute(stmt)
        if not before:
            done.append(f"created table {name}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    for col, decl in EVENT_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE task_events ADD COLUMN {col} {decl}")
            done.append(f"added task_events.{col}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(task_guidance)")}
    for col, decl in GUIDANCE_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE task_guidance ADD COLUMN {col} {decl}")
            done.append(f"added task_guidance.{col}")
    have = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
    for col, decl in COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {decl}")
            done.append(f"added tasks.{col}")
    conn.commit()
    return done


# ---------------------------------------------------------------- guidance

def set_guidance(task_id: int, explanation: str, fix: str | None = None,
                 your_part: str | None = None, action_kind: str = "none",
                 action_spec: dict | None = None, choices: list | None = None) -> None:
    if action_kind not in ACTIONS:
        raise ValueError(f"unknown action kind: {action_kind}")
    for ch in choices or []:
        if ch.get("action_kind", "none") not in ACTIONS:
            raise ValueError(f"unknown action kind in choice {ch.get('key')!r}")
        if not ch.get("key") or not ch.get("label"):
            raise ValueError("every choice needs a key and a label")
    conn = open_db()
    conn.execute(
        """INSERT INTO task_guidance
             (task_id, explanation, fix, your_part, action_kind, action_spec, choices, updated_at)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(task_id) DO UPDATE SET
             explanation=excluded.explanation, fix=excluded.fix,
             your_part=excluded.your_part, action_kind=excluded.action_kind,
             action_spec=excluded.action_spec, choices=excluded.choices,
             updated_at=excluded.updated_at""",
        (task_id, explanation, fix, your_part, action_kind,
         json.dumps(action_spec) if action_spec else None,
         json.dumps(choices) if choices else None,
         datetime.datetime.now().isoformat(timespec="seconds")))
    conn.commit()


def guidance(task_id: int) -> dict | None:
    conn = open_db()
    r = conn.execute("SELECT * FROM task_guidance WHERE task_id=?", (task_id,)).fetchone()
    if not r:
        return None
    g = dict(r)
    g["action_spec"] = json.loads(g["action_spec"]) if g["action_spec"] else {}
    g["choices"] = json.loads(g["choices"]) if g.get("choices") else []
    return g


# ---------------------------------------------------------------- actions

HOME = Path.home()
# Never let a recorded action touch these, however it was authored.
FORBIDDEN = [HOME / "Library", Path("/System"), Path("/usr"), Path("/bin"),
             Path("/etc"), Path("/var"), Path("/Applications")]


def _safe(path_str: str) -> Path:
    """Resolve a recorded path and refuse anything outside the user's own files."""
    p = Path(os.path.expanduser(path_str)).resolve()
    if not str(p).startswith(str(HOME)):
        raise ValueError(f"refusing to touch a path outside home: {p}")
    for bad in FORBIDDEN:
        if p == bad or bad in p.parents:
            raise ValueError(f"refusing to touch a protected path: {p}")
    return p


def _backup(p: Path) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = data_home() / "backups" / "actions" / f"{stamp}-{p.name}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if p.is_dir():
        shutil.copytree(p, dest)
        dest.chmod(0o700)
    else:
        shutil.copy2(p, dest)
        # copy2 preserves the source mode. A world-readable file whose whole
        # reason for being backed up is that it held a credential must not stay
        # world-readable here — that moves the exposure rather than ending it.
        dest.chmod(0o600)
    return str(dest)


def _delete_line(spec: dict) -> tuple[str, str]:
    """Remove one line, identified by number AND a non-secret prefix.

    The prefix is verified before anything is written, so a file that has shifted
    since the action was recorded fails loudly instead of deleting the wrong line.
    The line's content is never returned or logged — it holds the credential.
    """
    p = _safe(spec["path"])
    lines = p.read_text().splitlines(keepends=True)
    i = spec["line"] - 1
    if not 0 <= i < len(lines):
        raise ValueError(f"{p} has {len(lines)} lines; line {spec['line']} does not exist")
    if not lines[i].lstrip().startswith(spec["startswith"]):
        raise ValueError(
            f"line {spec['line']} of {p} no longer starts with the expected text — "
            "the file changed since this was recorded, so nothing was touched")
    backup = _backup(p)
    del lines[i]
    p.write_text("".join(lines))
    return f"removed line {spec['line']} from {p}", backup


def _delete_path(spec: dict) -> tuple[str, str]:
    """Move to the backup area rather than deleting, so Approve stays reversible."""
    p = _safe(spec["path"])
    if not p.exists():
        raise ValueError(f"nothing at {p} — already gone?")
    backup = _backup(p)
    if p.is_dir():
        shutil.rmtree(p)
    else:
        p.unlink()
    return f"removed {p}", backup


def _replace_in_file(spec: dict) -> tuple[str, str]:
    p = _safe(spec["path"])
    text = p.read_text()
    n = text.count(spec["old"])
    want = spec.get("count", 1)
    if n != want:
        raise ValueError(
            f"expected {want} occurrence(s) of the target text in {p}, found {n} — "
            "nothing was touched")
    backup = _backup(p)
    p.write_text(text.replace(spec["old"], spec["new"]))
    return f"replaced {n} occurrence(s) in {p}", backup


def _symlink(spec: dict) -> tuple[str, str]:
    src, dest = _safe(spec["src"]), _safe(spec["dest"])
    if not src.exists():
        raise ValueError(f"source does not exist: {src}")
    if dest.exists() or dest.is_symlink():
        raise ValueError(f"already exists: {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.symlink_to(src)
    return f"linked {dest} -> {src}", ""


def _none(spec: dict) -> tuple[str, str]:
    return "nothing for Lattice to do — closed on your word", ""


def _queue(spec: dict) -> tuple[str, str]:
    """No deterministic edit exists; the next Claude session picks it up."""
    return "queued for the next Claude Code session", ""


ACTIONS = {
    "none": _none,
    "queue": _queue,
    "delete_line": _delete_line,
    "delete_path": _delete_path,
    "replace_in_file": _replace_in_file,
    "symlink": _symlink,
}


# ---------------------------------------------------------------- resolving

def _event(conn, task_id: int, event: str, detail: str, backup: str = "") -> None:
    conn.execute(
        "INSERT INTO task_events (task_id,event,detail,backup,created_at) VALUES (?,?,?,?,?)",
        (task_id, event, detail, backup or None,
         datetime.datetime.now().isoformat(timespec="seconds")))


def resolve(task_id: int, decision: str, choice: str | None = None) -> dict:
    """Apply one button press. Returns what happened, for the page to show.

    approve — run the recorded action, then close the task
    deny    — close it as declined; the action never runs
    never   — archive it; it never appears again, and nothing is changed
    wait    — hide it until tomorrow
    ignore  — hide it for seven days
    """
    conn = open_db()
    now = datetime.datetime.now()
    task = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    if not task:
        raise ValueError(f"no task {task_id}")

    if decision == "wait":
        until = (now.date() + datetime.timedelta(days=1)).isoformat()
        conn.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (until, task_id))
        _event(conn, task_id, "wait", f"hidden until {until}")
        conn.commit()
        return {"ok": True, "state": "waiting", "detail": f"back tomorrow ({until})"}

    if decision == "ignore":
        until = (now.date() + datetime.timedelta(days=7)).isoformat()
        conn.execute("UPDATE tasks SET snoozed_until=? WHERE id=?", (until, task_id))
        _event(conn, task_id, "ignore", f"hidden until {until}")
        conn.commit()
        return {"ok": True, "state": "ignored", "detail": f"back on {until}"}

    if decision == "never":
        # Distinct from Deny. Deny is a decision about the work; this is a
        # decision about the LIST — stop showing it, change nothing.
        conn.execute(
            "UPDATE tasks SET status='archived', resolution='ignored', resolved_at=? "
            "WHERE id=?", (now.isoformat(timespec="seconds"), task_id))
        _event(conn, task_id, "never", "archived — will not appear again")
        conn.commit()
        return {"ok": True, "state": "archived",
                "detail": "gone for good. Nothing was changed; it just stops appearing"}

    if decision == "deny":
        conn.execute(
            "UPDATE tasks SET status='declined', resolution='denied', resolved_at=? WHERE id=?",
            (now.isoformat(timespec="seconds"), task_id))
        _event(conn, task_id, "deny", "declined — action not run")
        conn.commit()
        return {"ok": True, "state": "denied", "detail": "declined, nothing was changed"}

    if decision != "approve":
        raise ValueError(f"unknown decision: {decision}")

    g = guidance(task_id) or {}
    picked = None
    if g.get("choices"):
        if not choice:
            return {"ok": False, "state": "failed",
                    "detail": "this task is a decision — pick one of the options"}
        picked = next((ch for ch in g["choices"] if ch["key"] == choice), None)
        if not picked:
            return {"ok": False, "state": "failed", "detail": f"no such option: {choice}"}
        kind, spec = picked.get("action_kind", "none"), picked.get("action_spec") or {}
    else:
        kind, spec = g.get("action_kind", "none"), g.get("action_spec") or {}
    try:
        detail, backup = ACTIONS[kind](spec)
        if picked:
            detail = f"chose {picked['label']} — {detail}"
    except Exception as exc:
        # A failed action must not close the task or pretend it worked.
        _event(conn, task_id, "approve", f"FAILED: {exc}")
        conn.commit()
        return {"ok": False, "state": "failed", "detail": str(exc)}

    if kind == "queue":
        conn.execute(
            "UPDATE tasks SET resolution='queued', resolved_at=? WHERE id=?",
            (now.isoformat(timespec="seconds"), task_id))
    else:
        conn.execute(
            """UPDATE tasks SET status='done', resolution='approved',
                                resolved_at=?, completed_at=? WHERE id=?""",
            (now.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), task_id))
    _event(conn, task_id, "approve", detail, backup)
    conn.commit()
    return {"ok": True, "state": "queued" if kind == "queue" else "approved",
            "detail": detail, "backup": backup}


def visible_filter() -> str:
    """SQL fragment: open, not declined, not snoozed. Used by the digest."""
    return ("t.status='open' AND "
            "(t.snoozed_until IS NULL OR t.snoozed_until <= date('now','localtime'))")
