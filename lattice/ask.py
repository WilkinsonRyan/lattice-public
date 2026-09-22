"""Asking Claude about one specific task, from the page.

The server has no model of its own, so a question shells out to the Claude Code
CLI in headless mode. Two things are deliberate:

* **Read-only.** The run is given Read, Grep and Glob and nothing else. A question
  must never be able to edit a file, run a command, or reach the network. Asking
  "what does this mean" should be incapable of changing anything.
* **One at a time, and it reports what it cost.** Every question is a real model
  call. The run returns its own usage, so the page shows the actual cost rather
  than leaving the user to guess how much a button press spends.

Set `ask_mode` to "queue" in lattice_data/config.json to stop spending and record
questions for the next session instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading

from .core import data_home, open_db
from .triage import guidance

READ_ONLY = ["Read", "Grep", "Glob"]
BLOCKED = ["Bash", "Write", "Edit", "NotebookEdit", "WebFetch", "WebSearch", "Task"]
DEFAULT_MODEL = "claude-haiku-4-5-20251001"     # cheapest capable; override in config
TIMEOUT = 180

_busy = threading.Lock()


def claude_binary() -> str | None:
    """launchd hands a service a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin), so
    a CLI installed under the home directory is invisible to shutil.which there."""
    import os
    from pathlib import Path as _P
    found = shutil.which("claude")
    if found:
        return found
    for cand in (_P.home() / ".local/bin/claude",
                 _P.home() / ".claude/local/claude",
                 _P("/opt/homebrew/bin/claude"),
                 _P("/usr/local/bin/claude")):
        if cand.exists() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def config() -> dict:
    p = data_home() / "config.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def mode() -> str:
    return config().get("ask_mode", "headless")


def _context(task_id: int) -> tuple[str, str]:
    conn = open_db()
    row = conn.execute(
        """SELECT t.id, t.title, p.name AS project, p.path, p.description
             FROM tasks t JOIN projects p ON p.id = t.project_id WHERE t.id = ?""",
        (task_id,)).fetchone()
    if not row:
        raise ValueError(f"no task {task_id}")
    g = guidance(task_id) or {}
    lines = [f"Task {row['id']} in the project '{row['project']}'.",
             f"Project folder: {row['path']}",
             f"Task as written: {row['title']}"]
    if g.get("explanation"):
        lines.append(f"Existing write-up: {g['explanation']}")
    if g.get("your_part"):
        lines.append(f"What the user has to do himself: {g['your_part']}")
    return "\n".join(lines), row["path"]


def ask(task_id: int, question: str) -> dict:
    question = (question or "").strip()
    if not question:
        raise ValueError("no question was asked")
    if len(question) > 2000:
        raise ValueError("question is too long")

    if mode() == "queue":
        conn = open_db()
        conn.execute(
            "INSERT INTO task_events (task_id,event,detail,created_at) "
            "VALUES (?,?,?,datetime('now','localtime'))",
            (task_id, "question", question))
        conn.commit()
        return {"ok": True, "queued": True,
                "answer": "Saved for the next Claude session — nothing was spent."}

    binary = claude_binary()
    if not binary:
        raise RuntimeError("could not find the claude CLI")

    if not _busy.acquire(blocking=False):
        raise RuntimeError("a question is already running — wait for it to finish")
    try:
        context, cwd = _context(task_id)
        prompt = (
            "You are answering one question about a single task on someone's to-do list.\n\n"
            f"{context}\n\n"
            f"Their question: {question}\n\n"
            "Answer it directly and plainly, for someone who does not already know this "
            "task's background. Read files in the project folder if that is what it takes "
            "to be accurate. Be specific — name the file, the setting, the exact place to "
            "click. Under 200 words. Do not restate the task back at them. If the answer "
            "genuinely is not determinable from what is here, say so and say what would "
            "settle it.")
        proc = subprocess.run(
            [binary, "-p", prompt, "--output-format", "json",
             "--model", config().get("ask_model", DEFAULT_MODEL),
             "--allowedTools", *READ_ONLY, "--disallowedTools", *BLOCKED],
            cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "the run failed").strip()[:300])
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"ok": True, "answer": proc.stdout.strip(), "cost": None}
        answer = payload.get("result") or payload.get("text") or ""
        cost = payload.get("total_cost_usd")
        conn = open_db()
        # Record the cost with the question. Without it the only way to answer
        # "what did that cost me" later is to reconstruct it from Claude Code's
        # own transcript, which is not a thing to rely on.
        conn.execute(
            "INSERT INTO task_events (task_id,event,detail,cost_usd,created_at) "
            "VALUES (?,?,?,?,datetime('now','localtime'))",
            (task_id, "question", f"Q: {question}\nA: {answer[:800]}", cost))
        conn.commit()
        return {"ok": True, "answer": answer.strip(), "cost": cost,
                "model": payload.get("modelUsage") and list(payload["modelUsage"])[:1] or None}
    finally:
        _busy.release()


def spend(days: int = 30) -> dict:
    """What questions have cost. Cheap to call; the page shows it in the footer."""
    conn = open_db()
    row = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS total
             FROM task_events
            WHERE event = 'question' AND created_at >= date('now', ?)""",
        (f"-{days} days",)).fetchone()
    today = conn.execute(
        """SELECT COUNT(*) AS n, COALESCE(SUM(cost_usd), 0) AS total
             FROM task_events
            WHERE event = 'question' AND date(created_at) = date('now','localtime')"""
    ).fetchone()
    return {"days": days, "count": row["n"], "total": row["total"],
            "today_count": today["n"], "today_total": today["total"]}
