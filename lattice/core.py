"""Core workflows.

This is the layer that matters architecturally. The CLI parses arguments and
calls into here; a future API server, phone client or Claude adapter will call
exactly these functions. No workflow logic belongs in a command handler.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

from . import execution, secrets, storage

DEFAULT_HOME = Path.home() / "Lattice" / "lattice_data"

# The top-level split a fresh install starts with — most people separate two
# areas of life onto two folders. Real deployments rename these and add more;
# `guess_side`, the router and `changes.recent` all read this list rather than
# a hardcoded folder name, so a rename in config.json is the whole migration.
DEFAULT_ROOTS = [
    {"alias": "W", "name": "Work", "path": "~/Desktop/Work", "side": "work"},
    {"alias": "P", "name": "Personal", "path": "~/Desktop/Personal", "side": "personal"},
]

AGENT_ROLES: dict[str, str] = {
    "orchestrator": "Interprets intent, chooses the workflow, reads context, updates memory, returns concise next steps.",
    "architect": "Designs structure, evaluates tradeoffs, defines boundaries, avoids unnecessary complexity.",
    "planner": "Converts goals into sequenced tasks, keeps work scoped, names blockers.",
    "builder": "Implements changes, follows project conventions, avoids unrelated edits.",
    "reviewer": "Hunts bugs, regressions, missing tests and risk. Reports findings before summaries.",
    "documenter": "Captures final state, decisions and usage instructions. Keeps docs current.",
    "skill_librarian": "Watches for repeated workflows and promotes a pattern to a skill only once it has proved useful more than once.",
}


def data_home() -> Path:
    return Path(os.environ.get("LATTICE_HOME", DEFAULT_HOME))


def db_path() -> Path:
    return data_home() / "lattice.sqlite"


def load_config() -> dict:
    cfg = data_home() / "config.json"
    if not cfg.exists():
        return {}
    try:
        return json.loads(cfg.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def load_roots() -> list[dict]:
    """The top-level folders Lattice knows about, each an alias/name/path/side.

    Reads `config.json`'s `roots` key; falls back to `DEFAULT_ROOTS` for an
    install that has not customised it. Paths are stored `~`-relative and
    expanded here, so the config file itself never carries a machine-specific
    absolute path.
    """
    return load_config().get("roots", DEFAULT_ROOTS)


def load_name_tokens() -> list[str]:
    """Words that mean "this is addressed to the user specifically" in mail
    triage — their own name, surname, however they'd expect to see it in a
    subject line. Empty until config.json sets `name_tokens`."""
    return [t.lower() for t in load_config().get("name_tokens", [])]


# One connection per thread per database, reused.
#
# open_db() used to hand back a brand-new connection on every call and nobody
# closed them. A single page render calls it once per task, so the long-running
# server leaked ~50 file descriptors per page load and eventually died with
# "Too many open files" — still holding its listening socket, so launchd went on
# reporting it healthy while it refused every request.
#
# Keyed by path so a test pointing at a temporary database still gets its own.
_conns = threading.local()


def open_db():
    path = db_path()
    cache = getattr(_conns, "by_path", None)
    if cache is None:
        cache = _conns.by_path = {}
    key = str(path)
    conn = cache.get(key)
    if conn is not None:
        try:
            conn.execute("SELECT 1")          # a caller or a test may have closed it
        except sqlite3.ProgrammingError:
            cache.pop(key, None)
            conn = None
    if conn is None:
        conn = storage.connect(path)
        storage.init_schema(conn)
        cache[key] = conn
    return conn


def close_db() -> None:
    """Release this thread's connections.

    The server calls this at the end of every request. Without it a threaded
    server accumulates one connection per request thread, which is the leak that
    took the page down with "Too many open files" while the listening socket
    stayed open and launchd went on reporting the job healthy.
    """
    cache = getattr(_conns, "by_path", None)
    if not cache:
        return
    for conn in cache.values():
        try:
            conn.close()
        except Exception:
            pass
    cache.clear()


# --- init -------------------------------------------------------------------

def init() -> dict:
    """Create the data store. Safe to run repeatedly."""
    home = data_home()
    created = []
    for sub in ("", "agents", "skills", "logs"):
        d = home / sub if sub else home
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created.append(str(d))

    conn = open_db()

    cfg = home / "config.json"
    if not cfg.exists():
        cfg.write_text(json.dumps({
            "version": 1,
            "data_home": str(home),
            "scan_for_secrets_on_register": True,
            "offer_git_init_on_register": True,
            "roots": DEFAULT_ROOTS,
            "name_tokens": [],
        }, indent=2) + "\n")
        created.append(str(cfg))

    for role, purpose in AGENT_ROLES.items():
        f = home / "agents" / f"{role}.md"
        if not f.exists():
            f.write_text(
                f"# {role.replace('_', ' ').title()}\n\n"
                f"**Purpose.** {purpose}\n\n"
                "## Inputs\nProject context, recent memory, open tasks, git state.\n\n"
                "## Outputs\nConcise findings or changes, plus anything worth writing to memory.\n\n"
                "## Boundaries\nStay inside this role. Hand off rather than doing another role's job badly.\n"
            )
            created.append(str(f))

    tmpl = home / "skills" / "skill_template.md"
    if not tmpl.exists():
        tmpl.write_text(
            "# <skill name>\n\n"
            "**Purpose.** What this does in one sentence.\n\n"
            "**When to use it.** The trigger. Be specific enough that it is obvious.\n\n"
            "## Inputs\n\n## Steps\n1.\n2.\n\n## Outputs\n\n"
            "## Safety notes\nAnything destructive, irreversible, or that touches credentials.\n"
        )
        created.append(str(tmpl))

    readme = home / "skills" / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Skills\n\nReusable workflows. A pattern earns a place here once it has "
            "proved useful **more than once** — see `lattice pattern list`.\n\n"
            "Start from `skill_template.md`.\n"
        )
        created.append(str(readme))

    write_project_index(conn)
    conn.close()
    return {"home": str(home), "created": created}


# --- projects ---------------------------------------------------------------

def register_project(path, name=None, side=None, scan=True, git_init=False) -> dict:
    """Register a project directory. Returns a report dict.

    Registration also scans for exposed credentials and can start git tracking,
    because both problems are cheapest to fix the moment a project is known.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_dir():
        return {"error": f"not a directory: {p}"}

    conn = open_db()
    existing = storage.get_project(conn, str(p))
    if existing:
        conn.close()
        return {"error": f"already registered as '{existing['name']}' (id {existing['id']})"}

    name = name or p.name
    if storage.get_project(conn, name):
        name = f"{name}-{p.parent.name}"

    report = {
        "name": name,
        "path": str(p),
        "side": side or execution.guess_side(p),
        "kind": execution.detect_kind(p),
        "description": execution.describe(p),
        "git": execution.git_info(p),
        "findings": [],
        "git_init": None,
    }

    if scan:
        report["findings"] = secrets.scan(p)

    if git_init and not report["git"].get("is_repo"):
        ok, msg = execution.init_git_repo(p)
        report["git_init"] = msg
        if ok:
            report["git"] = execution.git_info(p)

    pid = storage.add_project(
        conn, name, p, side=report["side"],
        description=report["description"], kind=report["kind"],
    )
    report["id"] = pid
    storage.log_execution(conn, pid, f"register {p}", f"kind={report['kind']}")
    write_project_index(conn)
    conn.close()
    return report


def inspect_project(ident, scan=False) -> dict:
    conn = open_db()
    row = storage.get_project(conn, ident)
    if not row:
        conn.close()
        return {"error": f"no project matching '{ident}'"}

    p = Path(row["path"])
    out = {
        "project": dict(row),
        "exists": p.is_dir(),
        "git": execution.git_info(p) if p.is_dir() else {"is_repo": False},
        "memories": [dict(m) for m in storage.recent_memories(conn, row["id"], 5)],
        "tasks": [dict(t) for t in storage.list_tasks(conn, row["id"], "open")],
        "findings": secrets.scan(p) if (scan and p.is_dir()) else None,
    }
    conn.close()
    return out


# --- index ------------------------------------------------------------------

def write_project_index(conn) -> Path:
    """Regenerate the human-readable index.

    This exists so the knowledge layer is legible without querying SQLite —
    on a phone, in a browser, or by the user in a text editor.
    """
    home = data_home()
    home.mkdir(parents=True, exist_ok=True)
    out = home / "project_index.md"

    lines = [
        "# Project Index",
        "",
        "_Generated by Lattice. Do not edit by hand — run any `lattice` command to refresh._",
        "",
    ]
    for side_key, heading in (("work", "Work"), ("personal", "Personal"), ("unknown", "Unfiled")):
        rows = storage.list_projects(conn, side_key)
        if not rows:
            continue
        lines += [f"## {heading}", ""]
        for r in rows:
            open_tasks = len(storage.list_tasks(conn, r["id"], "open"))
            bits = [f"**{r['name']}** (id {r['id']})"]
            if r["kind"]:
                bits.append(f"`{r['kind']}`")
            if open_tasks:
                bits.append(f"{open_tasks} open task{'s' if open_tasks != 1 else ''}")
            lines.append("- " + " · ".join(bits))
            lines.append(f"  - `{r['path']}`")
            if r["description"]:
                lines.append(f"  - {r['description']}")
        lines.append("")

    if len(lines) <= 4:
        lines.append("_No projects registered yet. Try `lattice project add <path>`._")

    out.write_text("\n".join(lines) + "\n")
    return out


# --- status & review --------------------------------------------------------

def status() -> dict:
    conn = open_db()
    projects = storage.list_projects(conn)
    out = {
        "home": str(data_home()),
        "projects": len(projects),
        "by_side": {
            s: len([p for p in projects if p["side"] == s])
            for s in ("work", "personal", "unknown")
        },
        "open_tasks": len(storage.list_tasks(conn, None, "open")),
        "memories": len([f for f in MEMORY_DIR.glob("*.md")
                          if f.name != "MEMORY.md"]) if MEMORY_DIR.is_dir() else 0,
        "patterns": [dict(p) for p in storage.list_patterns(conn)],
        "skills": [dict(s) for s in storage.list_skills(conn)],
        "roles": sorted(AGENT_ROLES),
        "recent_memories": [],   # Claude Code surfaces these itself
        "untracked_projects": [],
    }
    for p in projects:
        pp = Path(p["path"])
        if pp.is_dir() and not execution.git_info(pp).get("is_repo"):
            out["untracked_projects"].append(p["name"])
    conn.close()
    return out


def review(ident) -> str:
    """Build a review brief — context assembled for a human or an AI to act on.

    Deliberately does not attempt the review itself. Its job is to make sure
    whoever does it starts with everything that matters.
    """
    data = inspect_project(ident, scan=True)
    if "error" in data:
        return data["error"]

    pr, git = data["project"], data["git"]
    L = [
        f"# Review brief — {pr['name']}",
        "",
        f"- **Path**  `{pr['path']}`",
        f"- **Side**  {pr['side']}",
        f"- **Kind**  {pr['kind'] or 'unknown'}",
    ]
    if pr["description"]:
        L.append(f"- **About**  {pr['description']}")

    L += ["", "## Git"]
    if git.get("is_repo"):
        L += [
            f"- Branch **{git['branch']}**, {git['commit_count']} commits",
            f"- Working tree: {'DIRTY — ' + str(git['dirty_count']) + ' file(s) uncommitted' if git['dirty'] else 'clean'}",
            f"- Last commit: `{git['last_commit']}`",
        ]
    else:
        L.append("- **Not a git repository.** No history to fall back on. "
                 "Run `lattice project track " + pr["name"] + "` to start tracking.")

    L += ["", "## Open tasks"]
    L += [f"- [{t['id']}] {t['title']}" for t in data["tasks"]] or ["- none"]

    L += ["", "## Recent memory"]
    L += [f"- {m['created_at'][:10]} — {m['content']}" for m in data["memories"]] or ["- none recorded"]

    L += ["", "## Credential scan"]
    if data["findings"]:
        L.append(f"- **{len(data['findings'])} finding(s) — resolve before anything else:**")
        L.append("```")
        L.append(secrets.format_findings(data["findings"], Path(pr["path"])))
        L.append("```")
    else:
        L.append("- clean")

    L += [
        "", "## Checklist",
        "- [ ] Does the README explain how to run it from nothing?",
        "- [ ] Is there a test that would fail if the main path broke?",
        "- [ ] Any credentials, tokens or customer data in the tree?",
        "- [ ] Anything here worth recording as a memory or a reusable pattern?",
        "- [ ] Is the work committed?",
        "",
        "## Suggested next commands",
        f"- `lattice memory add {pr['name']} \"<what you learned>\"`",
        f"- `lattice task add {pr['name']} \"<next action>\"`",
        f"- `lattice pattern add {pr['name']} \"<workflow worth reusing>\"`",
    ]
    return "\n".join(L)


# --- context packs ----------------------------------------------------------

def context_pack(ident=None, budget="short") -> str:
    """Everything an AI should know before starting work, as compact markdown.

    This is the *read* half of automatic memory. A session that begins with
    this does not have to be told what exists. Layered on purpose: the index is
    small, and detail is fetched per project only when one is named.
    """
    conn = open_db()
    L: list[str] = []

    if ident:
        row = storage.get_project(conn, ident)
        if not row:
            conn.close()
            return f"No project matching '{ident}'."
        p = Path(row["path"])
        g = execution.git_info(p)

        # A brief, when one exists, IS the answer. Raw git state and a memory
        # dump were what a session had to read before briefs existed; serving
        # both now would spend the tokens the brief was written to save.
        from . import briefs
        brief = briefs.hand_written(row)

        L += [f"# Lattice context — {row['name']}", ""]
        if brief:
            # Drop the brief's own H1 so the page has one title, not two.
            body = brief.split("\n", 1)[1].lstrip() if brief.startswith("# ") else brief
            L += [body, ""]
        else:
            L.append(f"- Path: `{row['path']}` ({row['side']}, {row['kind'] or 'unknown'})")
            if row["description"]:
                L.append(f"- About: {row['description']}")
            L += ["", f"> **No brief.** This project has no `CONTEXT.md`, so what follows is "
                      f"raw state, not an explanation. Write one with "
                      f"`lattice brief write {row['name']}`.", ""]

        L.append("## Live state")
        L.append(f"- Path: `{row['path']}` ({row['side']}, {row['kind'] or 'unknown'})")
        if g.get("is_repo"):
            L.append(f"- Git: {g['branch']}, {g['commit_count']} commits, "
                     + ("dirty" if g["dirty"] else "clean"))
        else:
            L.append("- Git: **not tracked**")

        tasks = storage.list_tasks(conn, row["id"], "open")
        if tasks:
            L.append(f"- Open tasks ({len(tasks)}):")
            L += [f"  - [{t['id']}] {t['title']}" for t in tasks]
        else:
            L.append("- Open tasks: none")

        idx, n = briefs.memory_index(row["name"])
        if idx:
            L.append(f"- Further facts: {n} indexed at "
                     f"`memory/_index/{row['name']}.md` — read one when needed")

        if not brief:
            decs = storage.list_decisions(conn, row["id"], 10)
            if decs:
                L += ["", "## Decisions"]
                for d in decs:
                    L.append(f"- **{d['decision']}**"
                             + (f" — {d['rationale']}" if d["rationale"] else ""))
        conn.close()
        return "\n".join(L)

    # No project named: the small index. Keep this cheap — it loads every session.
    projects = storage.list_projects(conn)
    L += ["# Lattice context", ""]
    if not projects:
        L.append("No projects registered yet.")
    else:
        L.append(f"{len(projects)} registered project(s). "
                 "Run `lattice context <name>` for detail on any one.")
        L.append("")
        side = None
        for r in projects:
            if r["side"] != side:
                side = r["side"]
                L.append(f"**{side}**")
            n = len(storage.list_tasks(conn, r["id"], "open"))
            L.append(f"- `{r['name']}` — {r['kind'] or 'unknown'}"
                     + (f", {n} open task(s)" if n else "") + f" — `{r['path']}`")

    tasks = storage.list_tasks(conn, None, "open")
    if tasks:
        L += ["", f"## Open tasks ({len(tasks)})"]
        L += [f"- [{t['id']}] {t['project_name']}: {t['title']}" for t in tasks[:15]]

    ready = [p for p in storage.list_patterns(conn)
             if p["seen_count"] >= 2 and not p["promoted_to_skill"]]
    if ready:
        L += ["", "## Patterns ready to become skills"]
        L += [f"- {p['seen_count']}× {p['title']}" for p in ready]

    # Memory is NOT listed here. It lives in ~/Lattice/memory/, which Claude Code
    # loads into context on its own — repeating it would spend the same tokens twice.

    conn.close()
    return "\n".join(L)


# --- sessions ---------------------------------------------------------------

def session_start(interface="claude-code") -> dict:
    conn = open_db()
    sid = storage.start_session(conn, interface=interface)
    conn.close()
    return {"session_id": sid}


def session_end(summary=None, interface="claude-code") -> dict:
    conn = open_db()
    row = storage.open_session(conn, interface)
    if not row:
        conn.close()
        return {"session_id": None}
    storage.end_session(conn, row["id"], summary)
    conn.close()
    return {"session_id": row["id"]}


# --- pattern -> skill -------------------------------------------------------
#
# Skills live in ~/.claude/skills/, where Claude Code actually loads them from.
# Lattice deliberately does NOT keep a parallel set: two skill folders means
# nobody knows which one is real. Lattice tracks and promotes; Claude Code runs.

SKILLS_DIR = Path.home() / ".claude" / "skills"

def promote_pattern(ident, skill_name=None) -> dict:
    """Turn a proven pattern into a skill file.

    Refuses a pattern seen only once. That refusal is the whole discipline —
    without it the skills folder fills with things that happened one time.
    """
    conn = open_db()
    pat = storage.get_pattern(conn, ident)
    if not pat:
        conn.close()
        return {"error": f"no pattern matching '{ident}'"}
    if pat["seen_count"] < 2:
        conn.close()
        return {"error": f"'{pat['title']}' has only been seen once. "
                         "A pattern earns promotion by recurring — record it again "
                         "next time it comes up, or pass --force if you are certain."}

    name = skill_name or pat["title"].lower().replace(" ", "-")[:60]
    name = "".join(c for c in name if c.isalnum() or c in "-_")
    path = SKILLS_DIR / name / "SKILL.md"
    if path.exists():
        conn.close()
        return {"error": f"skill already exists: {path}"}

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\n"
        f"description: {pat['description'] or pat['title']}. "
        f"Promoted from a pattern seen {pat['seen_count']} times.\n---\n\n"
        f"# {pat['title']}\n\n"
        f"**Purpose.** {pat['description'] or pat['title']}\n\n"
        f"**When to use it.** _Fill this in — be specific enough that the trigger "
        f"is obvious. A skill nobody can trigger is a skill nobody uses._\n\n"
        f"## Steps\n1.\n2.\n\n## Safety notes\n\n"
        f"---\n_Promoted from a pattern seen {pat['seen_count']}x "
        f"(first recorded {pat['created_at'][:10]})._\n"
    )
    storage.add_skill(conn, name, pat["description"] or pat["title"], path)
    storage.mark_pattern_promoted(conn, pat["id"])
    conn.close()
    return {"name": name, "path": str(path), "seen": pat["seen_count"]}


def force_promote(ident, skill_name=None) -> dict:
    conn = open_db()
    pat = storage.get_pattern(conn, ident)
    if pat and pat["seen_count"] < 2:
        conn.execute("UPDATE patterns SET seen_count=2 WHERE id=?", (pat["id"],))
        conn.commit()
    conn.close()
    return promote_pattern(ident, skill_name)


# --- decisions --------------------------------------------------------------

def add_decision(ident, decision, rationale=None) -> dict:
    conn = open_db()
    row = storage.get_project(conn, ident)
    if not row:
        conn.close()
        return {"error": f"no project matching '{ident}'"}
    did = storage.add_decision(conn, row["id"], decision, rationale)
    storage.touch_project(conn, row["id"])
    conn.close()
    return {"id": did, "project": row["name"]}


# --- ideas ------------------------------------------------------------------

IDEAS_DIR = Path.home() / "Lattice" / "context" / "future-ideas"


def add_idea(title, trigger=None, side="unknown", body=None) -> dict:
    """Capture something the user said they might build one day, before it evaporates."""
    IDEAS_DIR.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title.lower())
    slug = "-".join(filter(None, slug.split("-")))[:60]
    path = IDEAS_DIR / f"{slug}.md"
    if path.exists():
        return {"error": f"idea already captured: {path}"}
    path.write_text(
        f"---\nidea: {title}\nside: {side}\ncaptured: {storage.now()[:10]}\n"
        f"trigger: \"{trigger or ''}\"\nstatus: idea\n---\n\n"
        f"## The idea in one sentence\n{body or title}\n\n"
        f"## Why the user wants it\n\n## What already exists on their machine\n\n"
        f"## What already exists in the world\n\n## The hard part\n\n"
        f"## A first small version\n"
    )
    return {"path": str(path), "title": title}


def list_ideas() -> list[dict]:
    if not IDEAS_DIR.is_dir():
        return []
    out = []
    for f in sorted(IDEAS_DIR.glob("*.md")):
        if f.name.startswith("_") or f.name == "README.md":
            continue
        head = f.read_text(errors="ignore")[:600]
        title = f.stem
        side = "unknown"
        for line in head.splitlines():
            if line.startswith("idea:"):
                title = line.split(":", 1)[1].strip()
            elif line.startswith("side:"):
                # strip trailing "# comment" — otherwise it lands in the value
                side = line.split(":", 1)[1].split("#", 1)[0].strip()
        out.append({"title": title, "side": side, "path": str(f)})
    return out


# --- sync between machines --------------------------------------------------
#
# The SQLite file is deliberately NOT committed. A binary that changes on every
# command produces constant merge conflicts and an unreadable history. Instead
# each table is exported to sorted JSONL — text, so git merges it, humans read
# it, and a phone can open it.
#
# Merging is additive: rows are identified by their content, never by autoincrement
# id (ids collide across machines). Nothing is ever deleted by a sync, because a
# delete travelling between machines is how knowledge silently disappears.

# Skills are deliberately NOT synced. They are files installed per machine in
# ~/.claude/skills, and their stored paths are machine-specific — different
# accounts even on machines owned by the same person often resolve to different
# home directories. Each machine discovers its own with `lattice skill scan`.
# Syncing them propagates dead paths.
# Memories are NOT here. They live as markdown in ~/Lattice/memory/, which is
# Claude Code's auto-memory directory — loaded into context automatically every
# session and already inside this repo, so it syncs for free. Keeping a second
# copy in SQLite is the duplication this design exists to avoid.
EXPORT_TABLES = ("projects", "tasks", "decisions", "patterns")

# Tables that carry a project_id. `projects` is the parent and `skills` are
# global — neither has the column, and assuming otherwise breaks import on a
# machine that has not registered its skills yet.
PROJECT_SCOPED = ("tasks", "decisions", "patterns")


def export_dir() -> Path:
    return data_home() / "export"


def _project_path_by_id(conn) -> dict:
    return {r["id"]: r["path"] for r in conn.execute("SELECT id, path FROM projects")}


def _row_identity(table: str, row: dict) -> tuple:
    """Content-based identity, stable across machines."""
    if table == "projects":
        return (row["path"],)
    if table == "skills":
        return (row["name"],)
    if table == "patterns":
        return (row["title"],)
    key = {"memories": "content", "tasks": "title", "decisions": "decision"}[table]
    return (row.get("_project_path"), row[key], row["created_at"])


def export_all() -> dict:
    """Write every table to sorted JSONL. Deterministic, so identical state
    produces an identical file and git shows no diff."""
    conn = open_db()
    paths = _project_path_by_id(conn)
    out = export_dir()
    out.mkdir(parents=True, exist_ok=True)
    counts = {}

    for table in EXPORT_TABLES:
        rows = []
        for r in conn.execute(f"SELECT * FROM {table}"):
            d = dict(r)
            d.pop("id", None)                       # ids are machine-local
            pid = d.pop("project_id", None)
            if table not in ("projects", "skills"):
                d["_project_path"] = paths.get(pid)
            rows.append(d)
        rows.sort(key=lambda d: json.dumps(d, sort_keys=True))
        (out / f"{table}.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        )
        counts[table] = len(rows)

    conn.close()
    return {"dir": str(out), "counts": counts}


def import_all() -> dict:
    """Merge exported JSONL back in. Additive only — existing rows are left
    alone and nothing is deleted."""
    conn = open_db()
    d = export_dir()
    added = {t: 0 for t in EXPORT_TABLES}
    completed: dict = {}
    if not d.is_dir():
        conn.close()
        return {"error": f"nothing to import: {d} does not exist"}

    # projects first — everything else references them by path
    for table in EXPORT_TABLES:
        f = d / f"{table}.jsonl"
        if not f.exists():
            continue
        existing = set()
        paths = _project_path_by_id(conn)
        for r in conn.execute(f"SELECT * FROM {table}"):
            row = dict(r)
            pid = row.get("project_id")
            row["_project_path"] = paths.get(pid)
            existing.add(_row_identity(table, row))

        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if _row_identity(table, row) in existing:
                # A row already here is not re-inserted — but a task COMPLETED on
                # the other machine must still land, or a finished task stays open
                # here forever and the task list lies. Safe because completion is
                # monotonic: nothing ever un-completes, so this cannot conflict.
                if table == "tasks" and row.get("status") == "done":
                    conn.execute(
                        "UPDATE tasks SET status='done', completed_at=?"
                        " WHERE title=? AND status='open'",
                        (row.get("completed_at") or storage.now(), row["title"]))
                    if conn.total_changes:
                        completed[table] = completed.get(table, 0) + 1
                continue

            ppath = row.pop("_project_path", None)
            if table not in PROJECT_SCOPED:
                if table == "projects":
                    # a name clash across machines must not break the insert
                    if conn.execute("SELECT 1 FROM projects WHERE name=?",
                                    (row["name"],)).fetchone():
                        row["name"] = f"{row['name']}-{Path(row['path']).parent.name}"
                cols = list(row.keys())
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) "
                    f"VALUES ({','.join('?' * len(cols))})",
                    [row[c] for c in cols])
            else:
                pid = None
                if ppath:
                    got = conn.execute("SELECT id FROM projects WHERE path=?", (ppath,)).fetchone()
                    pid = got["id"] if got else None
                cols = list(row.keys())
                conn.execute(
                    f"INSERT INTO {table} (project_id,{','.join(cols)}) "
                    f"VALUES (?,{','.join('?' * len(cols))})",
                    [pid] + [row[c] for c in cols])
            added[table] += 1
        conn.commit()

    write_project_index(conn)
    conn.close()
    return {"added": added, "completed": completed}


def scan_skills() -> dict:
    """Register every skill already in ~/.claude/skills/ so `lattice skill list`
    reflects reality rather than a parallel universe."""
    conn = open_db()
    found = []
    if SKILLS_DIR.is_dir():
        for d in sorted(SKILLS_DIR.iterdir()):
            f = d / "SKILL.md"
            if not f.is_file():
                continue
            desc = None
            for line in f.read_text(errors="ignore").splitlines()[:20]:
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
            storage.add_skill(conn, d.name, (desc or "")[:300], f, status="active")
            found.append(d.name)

    # Reconcile: a registered skill whose file is gone is not a skill. Without
    # this, `lattice status` reports a count that does not match reality.
    removed = []
    for row in storage.list_skills(conn):
        if row["path"] and not Path(row["path"]).is_file():
            conn.execute("DELETE FROM skills WHERE id=?", (row["id"],))
            removed.append(row["name"])
    conn.commit()
    conn.close()
    return {"found": found, "removed": removed}


# --- memory: markdown, single store ----------------------------------------

MEMORY_DIR = Path.home() / "Lattice" / "memory"


def _slug(text: str, limit: int = 60) -> str:
    s = "".join(c if c.isalnum() or c in " -_" else " " for c in text.lower())
    return "-".join(s.split())[:limit].strip("-") or "note"


def memory_add(project, content, mtype="project") -> dict:
    """Write one fact as a markdown file in the single memory store.

    Deliberately not SQLite: this directory is Claude Code's auto-memory
    directory, so anything here loads into context every session without a hook
    or a query, and it is inside the git repo so it reaches the other machine.
    """
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    name = _slug(content[:60])
    path = MEMORY_DIR / f"{name}.md"
    i = 2
    while path.exists():
        path = MEMORY_DIR / f"{name}-{i}.md"
        i += 1

    desc = content.split(".")[0][:150].strip()
    path.write_text(
        f"---\nname: {path.stem}\ndescription: {desc}\nmetadata:\n"
        f"  node_type: memory\n  type: {mtype}\n"
        f"  modified: {storage.now()}\n---\n\n"
        + (f"**Project:** {project}\n\n" if project else "")
        + content + "\n"
    )

    idx = MEMORY_DIR / "MEMORY.md"
    title = desc[:70] or path.stem
    line = f"- [{title}]({path.name}) — {content[:110]}"
    with idx.open("a") as f:
        f.write(line + "\n")
    return {"path": str(path)}


def memory_search(query, limit=20) -> list[dict]:
    """Plain substring search over the markdown store. At one person's scale this
    finds what FTS did, over files a human can also just open."""
    out = []
    q = query.lower()
    if not MEMORY_DIR.is_dir():
        return out
    for f in sorted(MEMORY_DIR.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        body = f.read_text(errors="ignore")
        if q in body.lower():
            hit = next((ln.strip() for ln in body.splitlines()
                        if q in ln.lower() and not ln.startswith(("---", "name:", "description:"))),
                       "")
            out.append({"file": f.name, "line": hit[:200]})
        if len(out) >= limit:
            break
    return out
