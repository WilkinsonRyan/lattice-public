"""Segmenting the memory corpus without moving a single file.

`MEMORY.md` is loaded into every session, so its size is a tax on every message
sent. On one real corpus it grew to a 135-line catalogue costing ~7,200
tokens a session — the index was bigger than most of the things it indexed.

The fix is layering, not deletion. `MEMORY.md` becomes a router: areas, counts,
and the handful of facts that genuinely apply everywhere. Per-area indexes live
in `memory/_index/` and are read only when that area is in play.

The files themselves stay flat in `memory/`. The harness owns that directory via
`autoMemoryDirectory` and its recall may glob it flat, so moving 134 files into
subfolders risks silently breaking recall to win tidiness. Segmentation is
therefore logical, not physical.
"""

from __future__ import annotations

import collections
import pathlib
import re

from .core import data_home, open_db

MEM = pathlib.Path.home() / "Lattice" / "memory"
INDEX = MEM / "_index"

# Terms that identify an area when a file carries no **Project:** line.
# Tags that were written by hand and are not registered projects.
# Maps a **Project:** tag spelling to the registered project it really means.
# Populate this as you notice a project referred to by more than one name.
FOLD: dict[str, str] = {}

# One entry per project: terms that identify it in an untagged file's prose.
# Empty by default — a fresh install has no vocabulary to guess from yet, so
# every untagged fact starts out global until you teach it a project's terms.
ALIAS: dict[str, list[str]] = {}
# Areas that are real bodies of work but not registered projects.
EXTRA_AREAS: dict[str, list[str]] = {}
# A fact about the user, their machine, or how to work with them belongs to no project.
GLOBAL_HINTS = ["preference", "feedback", "tone"]

AREA_TITLES = {"_global": "Everywhere — the user, the machine, how to work with them"}


_WORD_CACHE: dict[str, re.Pattern] = {}


def _count(text: str, term: str) -> int:
    """Whole-word counting. Substring counting scored "esc" inside "describe",
    "cast" inside "broadcast" and "prop" inside "properties", which quietly filed
    unrelated facts into the drone project."""
    pat = _WORD_CACHE.get(term)
    if pat is None:
        pat = _WORD_CACHE[term] = re.compile(r"(?<![a-z0-9])" + re.escape(term) +
                                             r"(?![a-z0-9])")
    return len(pat.findall(text))


def _frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).splitlines():
        # Keys are nested under `metadata:` and therefore indented. Skipping
        # indented lines made every file read as the default type, which is how
        # a normalise pass came to overwrite 96 deliberately-set types.
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip()
    return fm, text[m.end():]


def classify() -> list[dict]:
    projects = [r["name"] for r in open_db().execute("SELECT name FROM projects")]
    out = []
    for f in sorted(MEM.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        text = f.read_text()
        fm, body = _frontmatter(text)
        low = text.lower()
        tagged = re.search(r"^\*\*Project:\*\*\s*(.+)$", body, re.M)
        ftype = fm.get("type", "note")

        # How to work with the user, and who they are, belong to no project — even
        # when the example that taught it happened to be about one.
        if ftype in ("feedback", "user"):
            out.append({
                "path": f, "name": f.stem, "area": "_global", "how": "by type",
                "type": ftype, "description": fm.get("description", "").strip().strip('"'),
                "superseded_by": fm.get("superseded_by", "").strip(), "size": len(text)})
            continue

        if tagged:
            raw = tagged.group(1).strip()
            area = raw.split("/")[0].strip()
            area = FOLD.get(area, area)
            how = "tagged"
        else:
            scores = {}
            for name, terms in list(ALIAS.items()) + list(EXTRA_AREAS.items()):
                scores[name] = sum(_count(low, t) for t in terms)
            for p in projects:
                scores.setdefault(p, _count(low, p.lower()))
            best = max(scores, key=scores.get)
            if scores[best] >= 2:
                area, how = best, "inferred"
            else:
                area, how = "_global", "global"
        if area not in projects and area not in EXTRA_AREAS:
            area = "_global" if any(h in low for h in GLOBAL_HINTS) else area
        out.append({
            "path": f, "name": f.stem, "area": area, "how": how,
            "type": fm.get("type") or "note",
            "description": fm.get("description", "").strip().strip('"'),
            "superseded_by": fm.get("superseded_by", "").strip(),
            "size": len(text),
        })
    return out


def _retype(rec: dict) -> str:
    """`note` is the default the CLI writes, so it means "nobody chose". Choose."""
    if rec["type"] != "note":
        return rec["type"]
    low = rec["path"].read_text().lower()
    if any(h in low for h in ("do not ", "never ", "how to apply", "don't ")):
        return "feedback" if rec["area"] == "_global" else "project"
    if rec["area"] == "_global":
        return "reference"
    return "project"


def normalize() -> dict:
    """Write the area and a real type into each file's frontmatter. Idempotent."""
    changed = collections.Counter()
    for rec in classify():
        text = rec["path"].read_text()
        fm, body = _frontmatter(text)
        want_type = _retype(rec)
        if fm.get("area") == rec["area"] and fm.get("type") == want_type:
            continue
        head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        if not head:
            continue
        block = head.group(1)
        block = re.sub(r"^\s*type:.*$", f"  type: {want_type}", block, flags=re.M)
        block = re.sub(r"^\s*area:.*$\n?", "", block, flags=re.M)
        block = block.rstrip() + f"\n  area: {rec['area']}"
        rec["path"].write_text(f"---\n{block}\n---\n" + text[head.end():])
        changed[rec["area"]] += 1
    return dict(changed)


def conflicts(ratio: float = 3.0, floor: int = 3) -> list[dict]:
    """Files whose hand-written **Project:** tag is contradicted by their content.

    A tag is normally authoritative. But a batch of facts filed during one drifted
    session can all carry the same wrong tag, and then the index confidently files
    them in the wrong place forever. Content only overrides a tag when it wins by a
    wide margin, so a passing mention never moves a file.
    """
    out = []
    for r in classify():
        if r["how"] != "tagged":
            continue
        low = r["path"].read_text().lower()
        scores = {}
        for name, terms in list(ALIAS.items()) + list(EXTRA_AREAS.items()):
            if name == r["area"]:
                continue
            scores[name] = sum(_count(low, t) for t in terms)
        if not scores:
            continue
        best = max(scores, key=scores.get)
        own = sum(_count(low, t) for t in ALIAS.get(r["area"], [r["area"].lower()]))
        if scores[best] >= floor and scores[best] >= max(own, 1) * ratio:
            out.append({"name": r["name"], "path": r["path"], "from": r["area"],
                        "to": best, "score": scores[best], "own": own})
    return out


def retag() -> list[dict]:
    """Rewrite the **Project:** line where content overwhelmingly disagrees."""
    moved = []
    for c in conflicts():
        text = c["path"].read_text()
        new = re.sub(r"^\*\*Project:\*\*\s*.+$", f"**Project:** {c['to']}",
                     text, count=1, flags=re.M)
        if new != text:
            c["path"].write_text(new)
            moved.append(c)
    return moved


def supersede(old: str, new: str) -> str:
    """Make superseding first-class instead of leaving a corrected fact standing
    beside its correction with nothing to say which won."""
    o, n = MEM / f"{old}.md", MEM / f"{new}.md"
    for f in (o, n):
        if not f.exists():
            raise ValueError(f"no memory named {f.stem}")
    text = o.read_text()
    head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not head:
        raise ValueError(f"{old} has no frontmatter")
    block = re.sub(r"^\s*superseded_by:.*$\n?", "", head.group(1), flags=re.M)
    block = block.rstrip() + f"\n  superseded_by: {new}"
    body = text[head.end():]
    banner = (f"> **Superseded by [[{new}]].** Kept for provenance — what it says here "
              f"was true when written and is not what Lattice believes now.\n\n")
    if not body.lstrip().startswith("> **Superseded"):
        body = banner + body.lstrip("\n")
    o.write_text(f"---\n{block}\n---\n\n" + body)
    return f"{old} marked superseded by {new}"


def build() -> dict:
    """MEMORY.md becomes a router; the catalogue moves into per-area indexes."""
    INDEX.mkdir(exist_ok=True)
    recs = classify()
    by_area: dict[str, list] = collections.defaultdict(list)
    for r in recs:
        by_area[r["area"]].append(r)

    for old in INDEX.glob("*.md"):
        old.unlink()

    for area, items in sorted(by_area.items()):
        items.sort(key=lambda r: (bool(r["superseded_by"]), r["name"]))
        title = AREA_TITLES.get(area, area)
        live = sum(1 for r in items if not r["superseded_by"])
        dead = len(items) - live
        lines = [f"# {title}", "",
                 f"{live} fact(s)" + (f", {dead} superseded" if dead else "") +
                 ". Read a file when you need it; this index is",
                 "not loaded unless this area is in play.", ""]
        for r in items:
            desc = r["description"] or "(no description)"
            mark = " **[superseded]**" if r["superseded_by"] else ""
            lines.append(f"- [{r['name']}](../{r['name']}.md) — {desc}{mark}")
        (INDEX / f"{area}.md").write_text("\n".join(lines) + "\n")

    glob_items = sorted(by_area.get("_global", []), key=lambda r: r["name"])
    rows = []
    for area, items in sorted(by_area.items(), key=lambda kv: -len(kv[1])):
        if area == "_global":
            continue
        live = sum(1 for r in items if not r["superseded_by"])
        rows.append(f"| [{area}](_index/{area}.md) | {live} |")

    out = ["# Memory — router", "",
           f"{len(recs)} facts across {len(by_area)} areas. **This file is loaded into every",
           "session, so it stays a router and never becomes a catalogue.** Open the index",
           "for the area you are working in; open a fact only when you need it.", "",
           "| Area | Facts |", "|:--|--:|"] + rows + ["",
           "## Applies everywhere", "",
           "These are about the user, their machine, or how to work with them — not",
           "one project, so they are listed here in full.", ""]
    for r in glob_items:
        d = r["description"] or "(no description)"
        if len(d) > 96:
            d = d[:93].rstrip(" ,;—-") + "…"
        out.append(f"- [{r['name']}]({r['name']}.md) — {d}")
    out += ["", "---", "",
            "*Regenerate with `lattice memory reindex` after adding facts. "
            "Hand edits to this file are overwritten.*"]
    MEMORY = MEM / "MEMORY.md"
    before = MEMORY.stat().st_size if MEMORY.exists() else 0
    MEMORY.write_text("\n".join(out) + "\n")
    return {"areas": len(by_area), "facts": len(recs),
            "before_bytes": before, "after_bytes": MEMORY.stat().st_size}
