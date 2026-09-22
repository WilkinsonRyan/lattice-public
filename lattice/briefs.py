"""Per-project briefs — the ~800-token page a cold agent reads instead of a repo.

Gate 2 of DONE.md: every project has a brief, no brief exceeds ~800 tokens, and
recall works cold. A brief is `CONTEXT.md` in the project folder, split in two:

    everything above the marker   hand-written, never touched by Lattice
    <!-- lattice:generated -->    the boundary
    everything below              rewritten by `lattice brief write <name>`

The split exists because the two halves rot at different speeds. What a project
IS changes rarely and only a human knows it; which tasks are open changes daily
and only the database knows it. Regenerating the second half must never cost the
first.
"""

from pathlib import Path

from . import storage
from .core import open_db

MARKER = "<!-- lattice:generated -->"
BUDGET = 800          # tokens; DONE.md gate 2
MEM_INDEX = Path.home() / "Lattice" / "memory" / "_index"

# Files written before the marker convention existed had no single boundary —
# whichever of these headings came first began the generated material. Used to
# find the split once, on a file's first regeneration.
LEGACY_BOUNDARIES = (
    "## Open work",
    "## Open tasks",
    "## Decisions on record",
    "## Decisions",
    "## Related memory",
    "## Further facts",
)


def _legacy_split(text: str) -> tuple[str, str]:
    cuts = [text.index(h) for h in LEGACY_BOUNDARIES if h in text]
    if not cuts:
        return text.rstrip(), ""
    i = min(cuts)
    return text[:i].rstrip(), text[i:].strip()


def est_tokens(text: str) -> int:
    """Words x 4/3. Close enough to police a budget, and it never needs a model."""
    return int(len(text.split()) * 4 / 3)


def brief_path(row) -> Path:
    return Path(row["path"]) / "CONTEXT.md"


def split_brief(text: str, remigrate: bool = False) -> tuple[str, str]:
    """Return (hand-written, generated). Either half may be empty.

    `remigrate` ignores the marker and re-splits at the earliest legacy
    heading. It exists because the first migration cut some files in the wrong
    place, leaving a generated section stranded above the marker; it is a
    repair, not part of normal operation.
    """
    if MARKER in text and not remigrate:
        head, _, tail = text.partition(MARKER)
        return head.rstrip(), tail.strip()
    if MARKER in text:
        text = text.partition(MARKER)[0]
    return _legacy_split(text)


def read_brief(row) -> str | None:
    p = brief_path(row)
    if not p.is_file():
        return None
    return p.read_text(errors="ignore")


def hand_written(row) -> str | None:
    """The durable half — what `lattice context <name>` actually serves."""
    text = read_brief(row)
    if text is None:
        return None
    head, _ = split_brief(text)
    return head or None


def memory_index(name: str) -> tuple[Path | None, int]:
    """Path to this project's memory index and how many facts it lists.

    The brief names the index rather than copying it. Gate 2 asks a brief to say
    which further facts exist WITHOUT loading them; a pointer does that for one
    line, a copied list costs a few hundred tokens and goes stale.
    """
    p = MEM_INDEX / f"{name}.md"
    if not p.is_file():
        return None, 0
    n = sum(1 for ln in p.read_text(errors="ignore").splitlines()
            if ln.startswith("- ["))
    return p, n


SKELETON = """# {name} — project context

**{side} · {kind}** · brief written {date}

> Written to be read cold. If you have no history with this project, everything
> you need to avoid repeating a mistake is on this page or linked from it.

## What this is
TODO — one or two sentences. What it does and who it is for, not how it is built.

## Where it stands
TODO — built / half-built / stalled, and the single next thing that matters.

Folder: `{path}`

## Traps and settled facts
_These cost real time to learn. Do not re-derive them._

- TODO — decisions already made, numbers already settled, bugs already paid for.
"""


def generated_tail(conn, row) -> str:
    """The half Lattice owns: what is open, what was decided, where facts live."""
    L = [MARKER, ""]

    tasks = storage.list_tasks(conn, row["id"], "open")
    L.append("## Open work")
    if tasks:
        L += [f"- `[{t['id']}]` {t['title']}" for t in tasks]
    else:
        L.append("_None open._")

    decs = storage.list_decisions(conn, row["id"], 10)
    if decs:
        L += ["", "## Decisions"]
        for d in decs:
            L.append(f"- **{d['decision']}**"
                     + (f" — {d['rationale']}" if d["rationale"] else ""))

    idx, n = memory_index(row["name"])
    L += ["", "## Further facts"]
    if idx:
        L.append(f"{n} fact(s) indexed at `memory/_index/{row['name']}.md` — "
                 "read one only when you need it.")
    else:
        L.append("No memory index yet. `lattice memory add "
                 f"{row['name']} \"<fact>\"` starts one.")
    L.append(f"`lattice memory search <term>` searches all of `~/Lattice/memory/`.")

    L += ["", "---",
          f"_Everything below `{MARKER}` is regenerated by "
          f"`lattice brief write {row['name']}`. Everything above it is "
          "hand-written and is never overwritten._"]
    return "\n".join(L)


def write(ident, date=None, remigrate=False) -> dict:
    """Create or refresh one project's brief. Returns what happened."""
    from datetime import date as _date
    conn = open_db()
    row = storage.get_project(conn, ident)
    if not row:
        conn.close()
        return {"ok": False, "error": f"No project matching '{ident}'."}

    p = brief_path(row)
    if not Path(row["path"]).is_dir():
        conn.close()
        return {"ok": False, "error": f"Project folder is missing: {row['path']}"}

    created = not p.is_file()
    if created:
        head = SKELETON.format(
            name=row["name"],
            side=row["side"],
            kind=row["kind"] or "unknown kind",
            date=(date or _date.today().isoformat()),
            path=row["path"],
        ).rstrip()
    else:
        head, _ = split_brief(p.read_text(errors="ignore"), remigrate)

    text = head + "\n\n" + generated_tail(conn, row) + "\n"
    p.write_text(text)
    conn.close()

    todos = sum(1 for ln in head.splitlines() if "TODO" in ln)
    return {"ok": True, "created": created, "path": str(p),
            "tokens": est_tokens(text), "hand_tokens": est_tokens(head),
            "todos": todos}


def survey() -> list[dict]:
    """Every project's brief status. This is the gate-2 measurement."""
    conn = open_db()
    out = []
    for row in storage.list_projects(conn):
        text = read_brief(row)
        head, _ = split_brief(text) if text else ("", "")
        out.append({
            "name": row["name"],
            "side": row["side"],
            "exists": text is not None,
            "folder_missing": not Path(row["path"]).is_dir(),
            "tokens": est_tokens(text) if text else 0,
            "hand_tokens": est_tokens(head) if text else 0,
            "marked": bool(text and MARKER in text),
            "todos": sum(1 for ln in head.splitlines() if "TODO" in ln),
        })
    conn.close()
    return out
