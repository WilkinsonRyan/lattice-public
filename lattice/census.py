"""Census — every folder Lattice knows about, one line each.

See docs/DEFINITION.md, "The census rule": registered projects get a brief;
everything else gets one line and a path, so "look at my tax info" resolves to
a location without anything earning a page it does not need.

Bounded walk: a folder that holds only subfolders is a category
(`Coded Programs`, `Arduino`) and gets walked one level deeper; a folder that
holds any file of its own — or has no subfolders left to descend into — is a
leaf and gets one line, not a walk of its insides. A folder matching a
registered project's path is skipped entirely; it already has a brief.
"""

from __future__ import annotations

from pathlib import Path

from . import execution, storage
from .core import data_home, open_db
from .router import roots, _alias

IGNORE = {".git", ".DS_Store", "__pycache__", "node_modules", ".venv", "venv",
          ".pytest_cache", "dist", "build"}

MAX_DEPTH = 4


def census_path() -> Path:
    return data_home() / "census.md"


def _is_noise(name: str) -> bool:
    return name in IGNORE or name.startswith(".") or name.endswith(".egg-info")


def _looks_like_a_project(path: Path) -> bool:
    """A folder that is a thing in itself, not just a bucket of other folders.

    A stray file sitting in a pure category folder (a leftover .zip, a
    download) must not collapse the whole category into one line and hide
    what is actually inside it — so this checks for real project markers,
    never bare file presence.
    """
    if (path / ".git").is_dir():
        return True
    if execution.detect_kind(path) is not None:
        return True
    return any((path / n).exists() for n in ("README.md", "README.txt", "README"))


def _emit(path: Path, registered: set[str], depth: int, out: list[str]) -> None:
    if str(path.resolve()) in registered:
        return  # has its own brief — see T0's project list

    try:
        dirs = sorted(c for c in path.iterdir() if c.is_dir() and not _is_noise(c.name))
    except OSError:
        return

    if not dirs or depth >= MAX_DEPTH or _looks_like_a_project(path):
        desc = execution.describe(path)
        line = f"- `{_alias(str(path.resolve()))}`"
        if desc:
            line += f" — {desc}"
        out.append(line)
        return

    for d in dirs:
        _emit(d, registered, depth + 1, out)


def build() -> dict:
    conn = open_db()
    registered = {str(Path(r["path"]).resolve()) for r in storage.list_projects(conn)}
    conn.close()

    lines = [
        "# Census. Every folder on the account that is not a registered project.",
        "Generated — regenerate with `lattice router`. One line, no brief: "
        "`lattice project add <path>` gives it one.",
        "",
    ]
    for alias, root in roots():
        rp = Path(root)
        if alias == "L" or not rp.is_dir():
            continue  # Lattice's own root is the tool, not census material
        section: list[str] = []
        for child in sorted(rp.iterdir()):
            if child.is_dir() and not _is_noise(child.name):
                _emit(child, registered, 1, section)
        if section:
            lines.append(f"## {alias} — {rp.name}")
            lines += section
            lines.append("")

    text = "\n".join(lines).rstrip() + "\n"
    census_path().write_text(text)
    return {"path": str(census_path()), "entries": sum(1 for ln in lines if ln.startswith("- "))}
