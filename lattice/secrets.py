"""Secret scanner.

Runs whenever a project is registered or inspected. It reports *where* a
credential is and *what kind* it is — it never prints, stores, or logs the
value itself. A scanner that echoes secrets into a database has made the
problem worse.

This is a tripwire, not a guarantee. It catches the common shapes; it will
miss a credential in an unusual format.
"""

from __future__ import annotations

import re
from pathlib import Path

# (label, compiled pattern). Patterns match the *shape* of a credential.
PATTERNS: list[tuple[str, re.Pattern]] = [
    ("AWS access key id",      re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret access key",  re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{30,}")),
    ("GitHub token",           re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("Slack token",            re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("OpenAI/Anthropic key",   re.compile(r"\b(sk-ant-|sk-)[A-Za-z0-9_\-]{20,}\b")),
    ("Google API key",         re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Google OAuth client id", re.compile(r"\b\d{10,}-[a-z0-9]{20,}\.apps\.googleusercontent\.com\b")),
    ("private key block",      re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("generic assigned secret",
     re.compile(r"(?i)\b(api[_-]?key|secret|passwd|password|token)\b\s*[=:]\s*['\"][^'\"]{12,}['\"]")),
    ("database URL with password",
     re.compile(r"(?i)\b(postgres|postgresql|mysql|mongodb)(\+\w+)?://[^\s:]+:[^\s@]+@")),
]

# Filenames that are a problem by their very existence.
SUSPICIOUS_NAMES = [
    re.compile(r"(?i)^client_secret.*\.json$"),
    re.compile(r"(?i)^service[_-]?account.*\.json$"),
    re.compile(r"(?i)^\.env(\..+)?$"),
    re.compile(r"(?i)^.*\.(pem|p12|pfx|keystore)$"),
    re.compile(r"(?i)^id_(rsa|dsa|ecdsa|ed25519)$"),
    re.compile(r"(?i)^credentials(\.json|\.yml|\.yaml)?$"),
]

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", "site-packages", ".Trash",
}
TEXT_SUFFIXES = {
    "", ".txt", ".md", ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yml",
    ".yaml", ".toml", ".ini", ".cfg", ".env", ".sh", ".bash", ".zsh", ".rb",
    ".go", ".java", ".php", ".sql", ".html", ".xml", ".properties", ".conf",
    ".command", ".ino",
}
MAX_BYTES = 2_000_000  # skip anything larger; secrets don't live in big binaries

# Values that look like credentials but are published documentation examples.
# Without this, a project's own tests and docs trip the scanner forever, and a
# scanner that always cries wolf gets ignored — which is the real failure.
KNOWN_FAKE = (
    "AKIAIOSFODNN7EXAMPLE",                       # AWS docs example key id
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",   # AWS docs example secret
    "AKIAI44QH8DHBEXAMPLE",                       # AWS docs, second example
)

# An inline marker to silence one line deliberately, e.g. a fixture or a doc
# sample the allowlist does not cover.
ALLOW_MARKER = "lattice:allow"


def scan(root: Path, max_files: int = 4000) -> list[dict]:
    """Walk `root` and return findings.

    Each finding is {path, line, kind}. There is deliberately no `value` key.
    """
    findings: list[dict] = []
    seen = 0
    root = Path(root)

    for path in root.rglob("*"):
        if seen >= max_files:
            break
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        seen += 1

        for name_re in SUSPICIOUS_NAMES:
            if name_re.match(path.name):
                findings.append({
                    "path": str(path), "line": 0,
                    "kind": "credential file by name",
                })
                break

        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            text = path.read_text(errors="ignore")
        except (OSError, ValueError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            if len(line) > 4000 or ALLOW_MARKER in line:
                continue
            if any(fake in line for fake in KNOWN_FAKE):
                continue
            for label, pat in PATTERNS:
                if pat.search(line):
                    findings.append({"path": str(path), "line": lineno, "kind": label})
                    break

    return findings


def format_findings(findings: list[dict], root: Path | None = None) -> str:
    """Human-readable report. Never includes the secret itself."""
    if not findings:
        return "  No credential patterns found."
    out = []
    for f in findings:
        p = f["path"]
        if root:
            try:
                p = str(Path(p).relative_to(root))
            except ValueError:
                pass
        where = f"{p}:{f['line']}" if f["line"] else p
        out.append(f"  [{f['kind']}]  {where}")
    return "\n".join(out)
