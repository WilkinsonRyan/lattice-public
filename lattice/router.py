"""T0 — the only file loaded in every session.

Generated, never hand-edited, always disposable. Holds addresses, not content:
which projects exist, and which preference IDs a task needs. Everything else is
fetched by name.

Budget is a contract. If T0 exceeds MAX_TOKENS the build fails loudly rather than
quietly growing the tax on every future session.
"""

from __future__ import annotations

import re
from pathlib import Path

from .core import data_home, load_roots, open_db

MAX_TOKENS = 600


def roots() -> list[tuple[str, str]]:
    """(alias, absolute path) for every configured root, plus Lattice's own
    home under its fixed alias "L" — read once per call so a config change
    takes effect on the next `lattice router` without restarting anything."""
    out = [(r["alias"], str(Path(r["path"]).expanduser())) for r in load_roots()]
    out.append(("L", str(data_home().parent)))
    return out

SECTION = re.compile(
    r"^##\s+(?P<domain>\S+)\s+—\s+(?P<label>[^|]+?)\s*\|\s*when:\s*(?P<when>[^|]+?)"
    r"(?:\s*\|\s*project:\s*(?P<project>\S+))?\s*$"
)
BULLET = re.compile(r"^\[(?P<id>[a-z]+)-(?P<num>\d+)\]")


def prefs_path() -> Path:
    return Path(__file__).resolve().parent.parent / "prefs" / "preferences.txt"


def t0_path() -> Path:
    return data_home() / "T0.md"


def paths_path() -> Path:
    return data_home() / "paths.md"


# Measured against tiktoken o200k_base on real T0 output 2026-09-09: this file's
# mix of prose, paths and punctuation runs ~3.2 chars/token, not the ~4 that plain
# English does. Estimating high is the safe direction for a budget gate.
CHARS_PER_TOKEN = 3.2


def estimate_tokens(text: str) -> int:
    """Conservative estimate, no dependencies. `lattice router --exact` uses a
    real tokenizer when one is installed."""
    return int(len(text) / CHARS_PER_TOKEN)


def _alias(path: str) -> str:
    for name, root in roots():
        if path == root:
            return f"{name}/"
        if path.startswith(root + "/"):
            return f"{name}/{path[len(root) + 1:]}"
    return path


def read_prefs(path: Path | None = None) -> list[dict]:
    """Parse the preference corpus into one entry per domain: its trigger and the
    span of IDs it covers. Bullet text is never read into T0."""
    path = path or prefs_path()
    if not path.exists():
        return []

    domains: list[dict] = []
    current: dict | None = None
    for line in path.read_text().splitlines():
        m = SECTION.match(line)
        if m:
            current = {
                "domain": m.group("domain"),
                "label": m.group("label").strip(),
                "when": m.group("when").strip(),
                "project": (m.group("project") or "").strip(),
                "ids": [],
            }
            domains.append(current)
            continue
        b = BULLET.match(line)
        if b and current:
            current["ids"].append(f"{b.group('id')}-{b.group('num')}")
    return [d for d in domains if d["ids"]]


def _span(ids: list[str]) -> str:
    if len(ids) == 1:
        return ids[0]
    return f"{ids[0]}..{ids[-1].split('-')[1]}"


def always_text(path: Path | None = None) -> str:
    """Full bullet text of every unscoped 'when: always' domain.

    T0 only ever names these IDs (`Always: ask-01..14 ...`) — it never inlines
    them, so pointing at T0 alone does not guarantee they reach a session. This
    is what the SessionStart hook cats alongside T0 so the guarantee is real,
    the way MEMORY.md's auto-load used to be the only thing providing it.
    """
    path = path or prefs_path()
    if not path.exists():
        return ""
    out: list[str] = []
    in_always = False
    for line in path.read_text().splitlines():
        m = SECTION.match(line)
        if m:
            in_always = m.group("when").strip().lower() == "always" and not m.group("project")
            if in_always:
                out.append(f"## {m.group('domain')} — {m.group('label').strip()}")
            continue
        if in_always and line.strip():
            out.append(line)
    return "\n".join(out).strip() + ("\n" if out else "")


def build(check_only: bool = False) -> dict:
    conn = open_db()
    rows = conn.execute(
        """
        SELECT p.name, p.path, p.side,
               (SELECT COUNT(*) FROM tasks t
                 WHERE t.project_id = p.id AND t.status = 'open') AS open_tasks
          FROM projects p
         WHERE p.status = 'active'
         ORDER BY p.side, p.name
        """
    ).fetchall()

    prefs = read_prefs()

    out: list[str] = []
    out.append("# Lattice T0. Read this, then stop. Fetch the rest by name.")
    out.append("briefs+facts+path `lattice brief <name>` · prefs L/prefs/preferences.txt"
               " · project paths L/lattice_data/paths.md · other folders L/lattice_data/census.md")
    out.append("")
    # Always-on spans need no trigger; project-scoped spans ride with their
    # project instead of costing a line in the global table.
    always = [d for d in prefs if d["when"].lower() == "always" and not d["project"]]
    scoped: dict[str, list[str]] = {}
    for d in prefs:
        if d["project"]:
            scoped.setdefault(d["project"], []).append(_span(d["ids"]))
    conditional = [d for d in prefs if d["when"].lower() != "always" and not d["project"]]

    out.append("## Preferences — L/prefs/preferences.txt")
    if always:
        out.append("Always: " + " ".join(_span(d["ids"]) for d in always))
    out.append("Load a span below only if its trigger matches the task:")
    for d in conditional:
        out.append(f"{d['when']} → {_span(d['ids'])}")
    out.append("")

    by_side: dict[str, list] = {}
    for r in rows:
        by_side.setdefault(r["side"] or "unknown", []).append(r)

    for side in ("work", "personal", "unknown"):
        if side not in by_side:
            continue
        out.append(f"## Projects — {side}")
        names: list[str] = []
        for r in by_side[side]:
            line = r["name"]
            if r["open_tasks"]:
                line += f" ({r['open_tasks']} open)"
            if r["name"] in scoped:
                line += " [" + " ".join(scoped[r["name"]]) + "]"
            names.append(line)
        out.append(" · ".join(names))
        out.append("")

    text = "\n".join(out).rstrip() + "\n"
    tokens = estimate_tokens(text)

    result = {
        "tokens": tokens,
        "budget": MAX_TOKENS,
        "projects": len(rows),
        "pref_domains": len(prefs),
        "pref_bullets": sum(len(d["ids"]) for d in prefs),
        "over_budget": tokens > MAX_TOKENS,
        "path": str(t0_path()),
    }

    if not check_only and not result["over_budget"]:
        t0_path().write_text(text)
        home = str(Path.home())
        paths = ["# Project paths. Generated — regenerate with `lattice router`.",
                 "Roots " + " ".join(f"{n}={r.replace(home, '~')}" for n, r in roots()), ""]
        paths += [f"{r['name']}  {_alias(r['path'])}" for r in rows]
        paths_path().write_text("\n".join(paths) + "\n")

        from . import census
        census.build()

        result["written"] = True
    else:
        result["written"] = False

    result["text"] = text
    return result
