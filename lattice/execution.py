"""Execution layer — reads the world, changes as little as possible.

Everything here is read-only except `init_git_repo`, which is the one place
Lattice writes to a project, and only when explicitly asked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Marker file -> project kind. First match wins, so order matters.
KIND_MARKERS: list[tuple[str, str]] = [
    ("pyproject.toml", "python"),
    ("requirements.txt", "python"),
    ("setup.py", "python"),
    ("package.json", "node"),
    ("Cargo.toml", "rust"),
    ("go.mod", "go"),
    ("platformio.ini", "platformio"),
    ("manifest.json", "chrome-extension"),
    ("CMakeLists.txt", "cmake"),
    ("Makefile", "make"),
    ("index.html", "web"),
]


def _git(path: Path, *args: str) -> str | None:
    """Run a git command, returning stripped stdout or None on any failure."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_info(path: Path) -> dict:
    """Branch, cleanliness and last commit for a path, or is_repo=False."""
    path = Path(path)
    inside = _git(path, "rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return {"is_repo": False}

    status = _git(path, "status", "--porcelain") or ""
    dirty_files = [ln for ln in status.splitlines() if ln.strip()]
    return {
        "is_repo": True,
        "branch": _git(path, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(dirty_files),
        "dirty_count": len(dirty_files),
        "last_commit": _git(path, "log", "-1", "--format=%h %s") or "(no commits yet)",
        "remote": _git(path, "remote", "get-url", "origin"),
        "commit_count": _git(path, "rev-list", "--count", "HEAD") or "0",
    }


def init_git_repo(path: Path) -> tuple[bool, str]:
    """Start tracking a project. Writes a .gitignore only if none exists.

    Returns (ok, message). Never touches a directory that is already a repo.
    """
    path = Path(path)
    if not path.is_dir():
        return False, f"not a directory: {path}"
    if git_info(path).get("is_repo"):
        return False, "already a git repository"

    gitignore = path / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(
            ".DS_Store\n__pycache__/\n*.py[cod]\n.venv/\nvenv/\nnode_modules/\n"
            "dist/\nbuild/\n\n# never commit secrets\n.env\n*.pem\n*.key\n"
            "credentials*\nclient_secret*\n"
        )

    for args in (
        ["init", "-q", "-b", "main"],
        ["add", "-A"],
        ["commit", "-q", "-m", "Initial commit: existing project state"],
    ):
        if _git(path, *args) is None and args[0] != "commit":
            return False, f"git {args[0]} failed"

    info = git_info(path)
    if not info.get("is_repo"):
        return False, "git init did not take"
    return True, f"tracking started on {info['branch']} ({info['commit_count']} commit)"


def detect_kind(path: Path) -> str | None:
    path = Path(path)
    for marker, kind in KIND_MARKERS:
        if (path / marker).exists():
            return kind
    for sub in ("src", "app"):
        if (path / sub).is_dir():
            return "unknown-src"
    return None


def guess_side(path: Path) -> str:
    """Which configured root a path falls under — 'work', 'personal', or
    whatever else the roots in config.json declare. See `core.load_roots`."""
    from .core import load_roots  # local import: core imports this module
    s = str(Path(path).resolve())
    for r in load_roots():
        root = str(Path(r["path"]).expanduser())
        if s == root or s.startswith(root + "/"):
            return r.get("side", "unknown")
    return "unknown"


def describe(path: Path) -> str | None:
    """First meaningful line of a README, if there is one."""
    path = Path(path)
    for name in ("README.md", "README.txt", "README", "readme.md"):
        f = path / name
        if not f.exists():
            continue
        try:
            for line in f.read_text(errors="ignore").splitlines():
                line = line.strip().lstrip("#").strip()
                if line and not line.startswith(("---", "===", "![", "[!", "<", "|")):
                    return line[:200]
        except OSError:
            pass
    return None
