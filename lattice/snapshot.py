"""The resume snapshot — what a session needs to know after it compacts.

Lattice used to speak once, at SessionStart, and then go quiet. When a long
session compacts, the harness summarises the conversation away; whatever Lattice
had contributed is gone at exactly the moment context is tightest.

This is the idea worth taking from context-mode: build a small, prioritised
snapshot at PreCompact and re-inject it, so the session resumes knowing what it
was actually doing rather than what it was told an hour ago.

Prioritised by criticality, hard-capped, and cheap — it is a database read, no
model call. The cap matters more than completeness: a snapshot that blows the
budget is the problem it was meant to solve.
"""

from __future__ import annotations

import datetime
import subprocess
from pathlib import Path

from .core import open_db

BUDGET = 2000            # characters, ~500 tokens. Hard ceiling, not a target.


def _recent_projects(conn, hours: int = 6) -> list:
    """Projects this session actually touched — by file mtime, not by guesswork."""
    since = (datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp()
    out = []
    for p in conn.execute(
            "SELECT id, name, path FROM projects WHERE status IN ('active','paused')"):
        root = Path(p["path"])
        if not root.exists():
            continue
        newest = 0.0
        try:
            for f in root.rglob("*"):
                if f.is_file() and not any(
                        part in {".git", "node_modules", ".venv", "__pycache__"}
                        for part in f.parts):
                    m = f.stat().st_mtime
                    if m > newest:
                        newest = m
                        if newest >= since:
                            break
        except (OSError, PermissionError):
            continue
        if newest >= since:
            out.append({"id": p["id"], "name": p["name"], "path": p["path"],
                        "newest": newest})
    out.sort(key=lambda d: -d["newest"])
    return out[:3]


def _dirty(path: str) -> str:
    try:
        r = subprocess.run(["git", "-C", path, "status", "--porcelain"],
                           capture_output=True, text=True, timeout=5)
        n = len([l for l in r.stdout.splitlines() if l.strip()])
        return f"{n} uncommitted file(s)" if n else ""
    except Exception:
        return ""


def build(budget: int = BUDGET) -> str:
    """Highest-criticality first, so truncation drops the least important thing."""
    conn = open_db()
    today = datetime.date.today().isoformat()
    parts: list[str] = ["# Lattice resume snapshot",
                        "State at compaction. Re-read a memory or task if you need detail."]

    # 1. Security work outranks everything.
    # Reuse the digest's definition of a security task rather than inventing a
    # second one. A LIKE '%token%' here matched the word "tokens" in a memory-size
    # note and reported it as credential exposure.
    from .digest import SECURITY
    candidates = conn.execute(
        """SELECT t.id, p.name, t.title FROM tasks t JOIN projects p ON p.id=t.project_id
            WHERE t.status='open'
              AND (t.snoozed_until IS NULL OR t.snoozed_until <= date('now'))
            ORDER BY t.id""").fetchall()
    sec = [r for r in candidates if SECURITY.search(r["title"])][:3]
    if sec:
        parts.append("\n## Live credential exposure")
        for r in sec:
            parts.append(f"- [{r['id']}] {r['name']}: {r['title'][:90]}")

    # 2. What this session has been touching.
    touched = _recent_projects(conn)
    if touched:
        parts.append("\n## In play this session")
        for p in touched:
            dirty = _dirty(p["path"])
            tasks = conn.execute(
                "SELECT id, title FROM tasks WHERE project_id=? AND status='open' "
                "ORDER BY id LIMIT 3", (p["id"],)).fetchall()
            line = f"- **{p['name']}** — {p['path']}"
            if dirty:
                line += f" ({dirty})"
            parts.append(line)
            for t in tasks:
                parts.append(f"    [{t['id']}] {t['title'][:80]}")

    # 3. Decisions made today — the things it would be wrong to re-litigate.
    dec = conn.execute(
        "SELECT d.decision, p.name FROM decisions d LEFT JOIN projects p ON p.id=d.project_id "
        "WHERE date(d.created_at)=? ORDER BY d.id DESC LIMIT 3", (today,)).fetchall()
    if dec:
        parts.append("\n## Settled today — do not re-open")
        for d in dec:
            parts.append(f"- {d['name'] or 'general'}: {d['decision'][:100]}")

    # 4. Memories written today.
    mem = conn.execute(
        "SELECT content FROM memories WHERE date(created_at)=? ORDER BY id DESC LIMIT 2",
        (today,)).fetchall()
    if mem:
        parts.append("\n## Learned today")
        for m in mem:
            parts.append(f"- {m['content'][:110]}")

    parts.append("\n`lattice context <project>` for detail. `lattice memory search <term>`.")

    out = "\n".join(parts)
    if len(out) > budget:
        out = out[:budget].rsplit("\n", 1)[0] + "\n…truncated to stay inside budget."
    return out
