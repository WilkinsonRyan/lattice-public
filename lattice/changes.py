"""What changed recently, across everything Lattice knows about.

Answers "what did I touch last night?" without needing every project to be a git
repo — plain mtimes, because half of a real project folder tree is not repos and
the ones that are do not commit on every edit.
"""

from __future__ import annotations

import datetime
import os
from pathlib import Path

from .core import load_roots, open_db

# Directories that are never interesting and are expensive to walk.
SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__", ".pytest_cache",
        ".DS_Store", "build", "dist", ".next", ".cache", "Pods", ".idea",
        "site-packages", ".mypy_cache", "image-cache", "shell-snapshots"}
SKIP_SUFFIX = {".pyc", ".pyo", ".log", ".lock", ".tmp", ".swp"}
MAX_DEPTH = 4


def _walk(root: Path, since: float, depth: int = 0):
    if depth > MAX_DEPTH:
        return
    try:
        entries = list(os.scandir(root))
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        return
    for e in entries:
        name = e.name
        if name in SKIP or name.startswith("."):
            continue
        try:
            if e.is_dir(follow_symlinks=False):
                yield from _walk(Path(e.path), since, depth + 1)
            elif e.is_file(follow_symlinks=False):
                if Path(name).suffix in SKIP_SUFFIX:
                    continue
                st = e.stat()
                if st.st_mtime >= since:
                    yield Path(e.path), st.st_mtime
        except (PermissionError, FileNotFoundError, OSError):
            continue


def recent(hours: int = 24, limit_per_group: int = 12) -> list[dict]:
    """Grouped by what the thing IS, not by folder — projects, memory, context."""
    since = (datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp()
    home = Path.home()
    groups: list[dict] = []

    conn = open_db()
    projects = conn.execute(
        "SELECT name, path, side FROM projects WHERE status IN ('active','paused') ORDER BY name"
    ).fetchall()

    proj_items = []
    for p in projects:
        root = Path(p["path"])
        if not root.exists():
            continue
        hits = sorted(_walk(root, since), key=lambda x: -x[1])
        if hits:
            proj_items.append({
                "label": p["name"], "side": p["side"], "count": len(hits),
                "newest": max(h[1] for h in hits),
                "files": [str(f.relative_to(root)) for f, _ in hits[:6]],
            })
    proj_items.sort(key=lambda d: -d["newest"])
    if proj_items:
        groups.append({"title": "Project folders", "items": proj_items[:limit_per_group]})

    work_root = next((r["path"] for r in load_roots() if r.get("side") == "work"), None)
    context_docs = Path(work_root).expanduser() / "Context Documents" if work_root else None

    for title, root in (("Memory", home / "Lattice/memory"),
                        ("Context documents", context_docs),
                        ("Lattice itself", home / "Lattice/lattice")):
        if root is None or not root.exists():
            continue
        hits = sorted(_walk(root, since), key=lambda x: -x[1])
        if hits:
            groups.append({"title": title, "items": [{
                "label": str(f.relative_to(root)),
                "side": "", "count": 1, "newest": m, "files": [],
            } for f, m in hits[:limit_per_group]]})
    return groups


def stale_projects(days: int = 120) -> list[dict]:
    """Candidates for deletion or archiving: nothing touched in a long time."""
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).timestamp()
    conn = open_db()
    out = []
    for p in conn.execute(
            """SELECT name, path, side, status, description,
                      (SELECT COUNT(*) FROM tasks t WHERE t.project_id=projects.id
                        AND t.status='open') AS open
                 FROM projects ORDER BY name"""):
        root = Path(p["path"])
        if not root.exists():
            out.append({"name": p["name"], "side": p["side"], "status": p["status"],
                        "open": p["open"], "days": None, "why": "folder no longer exists",
                        "path": p["path"]})
            continue
        newest = 0.0
        for _, m in _walk(root, 0):
            if m > newest:
                newest = m
        if newest and newest < cutoff:
            age = int((datetime.datetime.now().timestamp() - newest) / 86400)
            out.append({"name": p["name"], "side": p["side"], "status": p["status"],
                        "open": p["open"], "days": age,
                        "why": f"nothing changed in {age} days", "path": p["path"]})
    out.sort(key=lambda d: -(d["days"] or 9999))
    return out
