"""The Future tab's daily sweep of the wider world.

The hard part is not fetching, it is not drowning the user. A generic trending feed is
noise; what earns a place here is something that could change how Lattice or one
of his projects works. So every candidate is scored against what he actually has
registered and written down, and anything that scores nothing is dropped.

Sources need no credentials: GitHub's search API and Hacker News via Algolia.
Moltbook is deliberately absent — its API requires a registered, claimed agent key
and it currently hosts no posts.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.parse
import urllib.request

from .core import data_home, open_db

UA = {"User-Agent": "lattice-sweep/1.0", "Accept": "application/vnd.github+json"}

# What Lattice itself might absorb. Narrow on purpose.
QUERIES = [
    "agent memory llm",
    "llm long-term memory",
    "personal knowledge base local-first",
    "ai coding agent context",
    "sqlite knowledge graph notes",
]

SCHEMA = """CREATE TABLE IF NOT EXISTS sweep_items (
    id         TEXT PRIMARY KEY,
    source     TEXT NOT NULL,
    title      TEXT NOT NULL,
    url        TEXT NOT NULL,
    summary    TEXT,
    score      INTEGER NOT NULL DEFAULT 0,
    matched    TEXT,
    stars      INTEGER,
    license    TEXT,
    found_at   TEXT NOT NULL,
    dismissed  INTEGER NOT NULL DEFAULT 0
)"""


def migrate() -> None:
    conn = open_db()
    conn.execute(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(sweep_items)")}
    if "license" not in have:
        conn.execute("ALTER TABLE sweep_items ADD COLUMN license TEXT")
    conn.commit()


def _get(url: str, headers: dict | None = None) -> dict:
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())


# Terms that carry no signal. Mining project descriptions for any long word
# pulled in "across", "every", "design", "media" — which matched almost
# everything and turned the filter into a formality.
STOP = set("""about across after again against already always another anything
around because before being below better between both bring build built cannot
change check could design different during each either enough even every
everything first from full general getting given goes going great group hand have
having here high however inside instead into just keep known large last later
least less like little long look made make many math maybe means might more most
much must need never next nothing often once only other over own part people
place point possible provide question quite rather really right same second seem
series several should shows side simple since small some something sometimes
still such take than that their them then there these they thing think this
those three through time together tool tools toward under until upon used useful
using very want ways well were what when where which while will with within
without work working would your""".split())

# Terms that DO carry signal for what Lattice might absorb. Weighted above
# anything harvested automatically.
DOMAIN = {
    "memory": 3, "recall": 3, "retrieval": 3, "embedding": 3, "rag": 3,
    "knowledge": 2, "context": 3, "agent": 2, "agents": 2, "sqlite": 3,
    "local-first": 3, "digest": 2, "provenance": 3, "summariz": 2,
    "forgetting": 3, "consolidation": 3, "graph": 2, "notes": 2,
    "orchestrat": 2, "skill": 2, "mcp": 3,
}


def interests() -> dict[str, int]:
    """The user's own vocabulary, weighted. Project names score highest because a hit
    on one of those is unambiguous; generic words are excluded outright."""
    conn = open_db()
    words: dict[str, int] = dict(DOMAIN)
    for r in conn.execute("SELECT name, kind FROM projects"):
        for tok in re.findall(r"[a-z0-9]{5,}", (r["name"] or "").lower()):
            if tok not in STOP:
                words[tok] = 4
        if r["kind"]:
            words.setdefault(r["kind"].lower(), 2)
    return {w: v for w, v in words.items() if w not in STOP}


def _score(text: str, words: dict[str, int]) -> tuple[int, list[str]]:
    low = text.lower()
    hits = {w: v for w, v in words.items() if w in low}
    return sum(hits.values()), sorted(hits, key=lambda w: -hits[w])[:4]


def github(per_query: int = 6) -> list[dict]:
    out = []
    since = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    for q in QUERIES:
        url = ("https://api.github.com/search/repositories?q=" +
               urllib.parse.quote(f"{q} pushed:>{since}") +
               f"&sort=stars&order=desc&per_page={per_query}")
        try:
            data = _get(url)
        except Exception:
            continue
        for repo in data.get("items", []):
            out.append({
                "id": f"gh:{repo['id']}", "source": "GitHub",
                "title": repo["full_name"], "url": repo["html_url"],
                "summary": (repo.get("description") or "")[:300],
                "stars": repo.get("stargazers_count", 0),
            })
    return out


def hackernews(per_query: int = 6) -> list[dict]:
    out = []
    cutoff = int((datetime.datetime.now() - datetime.timedelta(days=30)).timestamp())
    for q in QUERIES:
        url = ("https://hn.algolia.com/api/v1/search?query=" + urllib.parse.quote(q) +
               f"&tags=story&numericFilters=created_at_i>{cutoff}&hitsPerPage={per_query}")
        try:
            data = _get(url)
        except Exception:
            continue
        for hit in data.get("hits", []):
            if not hit.get("title"):
                continue
            out.append({
                "id": f"hn:{hit['objectID']}", "source": "Hacker News",
                "title": hit["title"],
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
                "summary": f"{hit.get('points', 0)} points · {hit.get('num_comments', 0)} comments",
                "stars": hit.get("points", 0),
            })
    return out


def arxiv(per_query: int = 4) -> list[dict]:
    """Research, not products. Where "AI memory improving" ideas appear first."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for q in QUERIES[:3]:
        url = ("http://export.arxiv.org/api/query?search_query=" +
               urllib.parse.quote(f'all:"{q}"') +
               f"&sortBy=submittedDate&sortOrder=descending&max_results={per_query}")
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=25) as r:
                root = ET.fromstring(r.read())
        except Exception:
            continue
        for e in root.findall("a:entry", ns):
            title = (e.findtext("a:title", "", ns) or "").strip().replace("\n", " ")
            link = e.findtext("a:id", "", ns)
            summary = (e.findtext("a:summary", "", ns) or "").strip().replace("\n", " ")
            if title and link:
                out.append({"id": f"ax:{link.rsplit('/', 1)[-1]}", "source": "arXiv",
                            "title": title[:160], "url": link,
                            "summary": summary[:300], "stars": 0})
    return out


def huggingface(limit: int = 15) -> list[dict]:
    try:
        data = _get(f"https://huggingface.co/api/daily_papers?limit={limit}")
    except Exception:
        return []
    out = []
    for item in data:
        paper = item.get("paper", {})
        pid = paper.get("id") or item.get("id")
        if not pid:
            continue
        out.append({"id": f"hf:{pid}", "source": "HF papers",
                    "title": (paper.get("title") or "")[:160],
                    "url": f"https://huggingface.co/papers/{pid}",
                    "summary": (paper.get("summary") or "")[:300],
                    "stars": int(paper.get("upvotes") or 0)})
    return out


def lobsters() -> list[dict]:
    out = []
    # "programming" is general-interest noise; only the ai tag ever scores.
    for tag in ("ai",):
        try:
            data = _get(f"https://lobste.rs/t/{tag}.json")
        except Exception:
            continue
        for st in data[:15]:
            out.append({"id": f"lo:{st['short_id']}", "source": "Lobsters",
                        "title": st.get("title", "")[:160],
                        "url": st.get("url") or st.get("short_id_url", ""),
                        "summary": ", ".join(st.get("tags", []))[:300],
                        "stars": int(st.get("score") or 0)})
    return out


def run(min_score: int = 6) -> dict:
    """Fetch, score against the user's own work, keep only what clears the bar."""
    migrate()
    words = interests()
    raw = github() + hackernews() + arxiv() + huggingface() + lobsters()
    seen, kept = set(), []
    for item in raw:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        score, hits = _score(item["title"] + " " + item["summary"], words)
        if score < min_score:
            continue
        item["score"], item["matched"] = score, hits
        kept.append(item)
    kept.sort(key=lambda i: (-i["score"], -(i["stars"] or 0)))

    conn = open_db()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for i in kept:
        conn.execute(
            "INSERT OR IGNORE INTO sweep_items "
            "(id,source,title,url,summary,score,matched,stars,found_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (i["id"], i["source"], i["title"], i["url"], i["summary"], i["score"],
             json.dumps(i["matched"]), i["stars"], now))
    conn.commit()
    return {"seen": len(raw), "kept": len(kept), "items": kept[:20]}


def recent(limit: int = 15) -> list[dict]:
    migrate()
    rows = open_db().execute(
        "SELECT * FROM sweep_items WHERE dismissed=0 ORDER BY score DESC, stars DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["matched"] = json.loads(d["matched"] or "[]")
        out.append(d)
    return out


def run_if_stale(hours: int = 20) -> None:
    """Once a day is the point; a sweep every 15 minutes would be rude to both
    APIs and useless to the user."""
    migrate()
    row = open_db().execute("SELECT MAX(found_at) AS last FROM sweep_items").fetchone()
    if row and row["last"]:
        age = datetime.datetime.now() - datetime.datetime.fromisoformat(row["last"])
        if age.total_seconds() < hours * 3600:
            return
    run()


def adopt(item_id: str) -> str:
    """Turn a find into a real Lattice task, so the Future tab feeds the system
    rather than being a reading list you scroll past every morning."""
    migrate()
    conn = open_db()
    row = conn.execute("SELECT * FROM sweep_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("no such item")
    pid = conn.execute("SELECT id FROM projects WHERE name='lattice'").fetchone()
    if not pid:
        raise ValueError("the lattice project is not registered")
    now = datetime.datetime.now().isoformat(timespec="seconds")
    title = f"Evaluate {row['title']} for Lattice — {row['url']}"
    task = conn.execute(
        "INSERT INTO tasks (project_id,title,status,created_at) VALUES (?,?,'open',?)",
        (pid["id"], title, now)).lastrowid
    conn.execute("UPDATE sweep_items SET dismissed=1 WHERE id=?", (item_id,))
    conn.commit()

    from .triage import set_guidance
    set_guidance(
        task,
        explanation=(f"Found on {row['source']} by the daily sweep because it matches "
                     f"{', '.join(json.loads(row['matched'] or '[]')) or 'your work'}. "
                     f"{(row['summary'] or '')[:160]}"),
        fix="Queue it — reading the thing and deciding what, if anything, Lattice "
            "should take from it is a judgement call, not a scripted edit.",
        your_part=None, action_kind="queue")
    return f"added as task {task}"


def dismiss(item_id: str) -> str:
    migrate()
    conn = open_db()
    conn.execute("UPDATE sweep_items SET dismissed=1 WHERE id=?", (item_id,))
    conn.commit()
    return "hidden from the sweep"


# --- adopting for real -------------------------------------------------------
# "Adopt" used to mean "make a note to look at this later", which is what Queue
# means. Adopt now does everything that does not require judgement: it pulls the
# actual material down, establishes the licence, and hands a session a task with
# the reading already done. Writing the integration is judgement and stays with
# a session — a server cannot do it and should not pretend to.

ADOPTED = data_home() / "adopted"

# Licences that would infect a repository the user intends to publish.
VIRAL = {"GPL-3.0", "GPL-2.0", "AGPL-3.0", "LGPL-3.0", "LGPL-2.1", "SSPL-1.0"}
PERMISSIVE = {"MIT", "Apache-2.0", "BSD-3-Clause", "BSD-2-Clause", "ISC", "Unlicense",
              "MPL-2.0", "CC0-1.0"}

# Source-available licences GitHub cannot map to an SPDX id. They are real
# licences with real permissions — reporting them as "no licence" is wrong in the
# direction that matters, because it rejects code that is actually usable.
SOURCE_AVAILABLE = {
    "elastic license 2.0": (
        "Elastic License 2.0 — source-available. Use, fork, modify and distribute "
        "are all permitted. Two prohibitions: you may not offer it as a hosted or "
        "managed service, and you may not remove the licensing notices. Lattice is "
        "a local tool, so the hosted-service clause does not bite; keep the notices."),
    "business source license": (
        "Business Source License — source-available with a delayed open-source date. "
        "Usually permits everything except production/competing use until the change "
        "date. Read the Additional Use Grant before copying code."),
    "functional source license": (
        "Functional Source License — source-available; converts to Apache/MIT after "
        "two years. Non-compete restrictions until then."),
    "polyform": (
        "PolyForm licence — source-available with a use restriction. Read which one."),
}


def _repo_slug(url: str) -> str | None:
    m = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", url or "")
    return m.group(1).rstrip("/") if m else None


def _github_detail(slug: str) -> dict:
    """Never let a failed lookup masquerade as a fact.

    A 403 from the rate limiter used to be swallowed, leaving license=None, which
    reads as "no licence declared — all rights reserved". An MIT repo would be
    reported as unusable because GitHub throttled us. `error` distinguishes "we
    could not find out" from "there genuinely is none".
    """
    out = {"license": None, "license_text": "", "readme": "", "topics": [],
           "description": "", "error": None}
    try:
        repo = _get(f"https://api.github.com/repos/{slug}")
        lic = (repo.get("license") or {}).get("spdx_id")
        # NOASSERTION means "a licence exists but GitHub cannot identify it" —
        # not "there is no licence". Read the file rather than guessing.
        if lic == "NOASSERTION" or (repo.get("license") or {}).get("key") == "other":
            out["license"], out["license_text"] = _read_license(slug)
        else:
            out["license"] = None if lic is None else lic
        out["topics"] = repo.get("topics", []) or []
        out["description"] = repo.get("description") or ""
    except Exception as exc:
        code = getattr(exc, "code", None)
        out["error"] = ("GitHub rate limit reached — 60 requests an hour without a token"
                        if code == 403 else f"could not reach GitHub ({exc})")
    try:
        rd = _get(f"https://api.github.com/repos/{slug}/readme")
        import base64
        out["readme"] = base64.b64decode(rd.get("content", "")).decode("utf-8", "replace")
    except Exception:
        pass
    return out


def rate_limit() -> dict:
    """What GitHub will still let us do this hour."""
    try:
        d = _get("https://api.github.com/rate_limit")["resources"]["core"]
        return {"limit": d["limit"], "remaining": d["remaining"], "reset": d["reset"]}
    except Exception:
        return {"limit": None, "remaining": None, "reset": None}


def _read_license(slug: str) -> tuple[str | None, str]:
    """Pull the LICENSE file itself when GitHub cannot name the licence."""
    try:
        import base64
        lf = _get(f"https://api.github.com/repos/{slug}/license")
        body = base64.b64decode(lf.get("content", "")).decode("utf-8", "replace")
    except Exception:
        return None, ""
    head = " ".join(body.strip().splitlines()[:3]).lower()
    for needle in SOURCE_AVAILABLE:
        if needle in head:
            return needle, body[:400]
    first = body.strip().splitlines()[0][:80] if body.strip() else ""
    return (f"unrecognised: {first}" if first else None), body[:400]


def licence_verdict(spdx: str | None, error: str | None = None) -> tuple[str, str]:
    if error:
        return "unavailable", (f"could not be established — {error}. Nothing is claimed "
                               "about this licence either way; check it yourself before "
                               "copying any code.")
    if spdx in PERMISSIVE:
        return "ok", f"{spdx} — permissive, safe to borrow from with attribution"
    if spdx in SOURCE_AVAILABLE:
        return "source-available", SOURCE_AVAILABLE[spdx]
    if spdx and spdx.startswith("unrecognised: "):
        return "check", (f"the repo ships a licence GitHub cannot name — it begins "
                         f"\u201c{spdx[14:]}\u201d. Read it before copying code; it is "
                         "not absent, just unusual.")
    if spdx in VIRAL:
        return "blocked", (f"{spdx} — copyleft. Taking code under it would force the same "
                           "licence on Lattice, which you intend to publish. Read it for "
                           "ideas; do not copy code.")
    if not spdx:
        return "unknown", ("no licence declared — legally that means all rights reserved. "
                           "Ideas only; copying anything needs the author's permission.")
    return "check", f"{spdx} — read the terms before copying code"


def adopt(item_id: str) -> str:
    """Pull the material, settle the licence, and file a task ready to act on."""
    migrate()
    conn = open_db()
    row = conn.execute("SELECT * FROM sweep_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("no such item")
    pid = conn.execute("SELECT id FROM projects WHERE name='lattice'").fetchone()
    if not pid:
        raise ValueError("the lattice project is not registered")

    slug = _repo_slug(row["url"])
    detail = _github_detail(slug) if slug else {}
    spdx = detail.get("license")
    verdict, licence_note = licence_verdict(spdx, detail.get("error"))

    safe = re.sub(r"[^a-z0-9]+", "-", (slug or row["title"]).lower()).strip("-")[:60]
    folder = ADOPTED / safe
    folder.mkdir(parents=True, exist_ok=True)
    head = [f"# {row['title']}", "",
            f"- Source: {row['source']}", f"- URL: {row['url']}",
            f"- Licence: {licence_note}",
            f"- Matched: {', '.join(json.loads(row['matched'] or '[]')) or '—'}",
            f"- Pulled: {datetime.datetime.now().isoformat(timespec='seconds')}", ""]
    if detail.get("topics"):
        head.append(f"- Topics: {', '.join(detail['topics'])}\n")
    if detail.get("description"):
        head += ["## Description", "", detail["description"], ""]
    if detail.get("readme"):
        head += ["## README as published", "", detail["readme"]]
    elif row["summary"]:
        head += ["## Summary", "", row["summary"]]
    (folder / "SOURCE.md").write_text("\n".join(head))

    now = datetime.datetime.now().isoformat(timespec="seconds")
    title = f"Integrate {row['title']} into Lattice"
    task = conn.execute(
        "INSERT INTO tasks (project_id,title,status,created_at) VALUES (?,?,'open',?)",
        (pid["id"], title, now)).lastrowid
    conn.execute("UPDATE sweep_items SET dismissed=1 WHERE id=?", (item_id,))
    conn.commit()

    from .triage import set_guidance
    rel = folder.relative_to(data_home().parent)
    set_guidance(
        task,
        explanation=(f"Adopted from {row['source']}. The material is already pulled down to "
                     f"{rel}/SOURCE.md, so nothing needs fetching again. Licence: {licence_note}"),
        fix=("Queue it — the reading is done and the licence is settled; deciding what "
             "Lattice actually takes from it, and writing that, is a judgement call."),
        your_part=(None if verdict == "ok" else
                   f"Decide whether this is usable at all — {licence_note}"),
        action_kind="queue")
    return (f"pulled the source to {rel}/SOURCE.md, licence {spdx or 'none declared'} "
            f"({verdict}), filed as task {task}")


def queue_item(item_id: str) -> str:
    """The old, cheap action: make a note to look at this later. Fetches nothing."""
    migrate()
    conn = open_db()
    row = conn.execute("SELECT * FROM sweep_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("no such item")
    pid = conn.execute("SELECT id FROM projects WHERE name='lattice'").fetchone()
    now = datetime.datetime.now().isoformat(timespec="seconds")
    task = conn.execute(
        "INSERT INTO tasks (project_id,title,status,created_at) VALUES (?,?,'open',?)",
        (pid["id"], f"Look at {row['title']} for Lattice — {row['url']}", now)).lastrowid
    conn.execute("UPDATE sweep_items SET dismissed=1 WHERE id=?", (item_id,))
    conn.commit()
    from .triage import set_guidance
    set_guidance(task,
                 explanation=(f"Spotted on {row['source']} by the daily sweep. Nothing has "
                              f"been read yet. {(row['summary'] or '')[:150]}"),
                 fix="Queue it — someone still has to read the thing.",
                 action_kind="queue")
    return f"noted as task {task}"



def info(item_id: str) -> dict:
    """Everything known about a find, for the Info panel.

    The licence is fetched once and cached, because it is the fact that decides
    whether any of this is usable and it should not wait for Adopt.
    """
    migrate()
    conn = open_db()
    row = conn.execute("SELECT * FROM sweep_items WHERE id=?", (item_id,)).fetchone()
    if not row:
        raise ValueError("no such item")
    d = dict(row)
    d["matched"] = json.loads(d["matched"] or "[]")

    spdx = d.get("license")
    slug = _repo_slug(d["url"])
    lookup_error = None
    if spdx is None and slug:
        detail = _github_detail(slug)
        lookup_error = detail.get("error")
        spdx = detail.get("license") or ""
        # Only cache a licence we actually established. Caching "" after a failed
        # lookup would freeze the wrong answer in place for good.
        if not lookup_error:
            conn.execute("UPDATE sweep_items SET license=? WHERE id=?", (spdx, item_id))
            conn.commit()
        d["description"] = detail.get("description", "")
        d["topics"] = detail.get("topics", [])
    verdict, note = licence_verdict(spdx or None, lookup_error)

    # Why it was chosen, in the reader's own terms rather than as a score.
    owner = {}
    for r in conn.execute("SELECT name FROM projects"):
        for tok in re.findall(r"[a-z0-9]{5,}", r["name"].lower()):
            owner.setdefault(tok, r["name"])
    why = []
    for term in d["matched"]:
        if term in DOMAIN:
            why.append(f"**{term}** — part of how Lattice stores and recalls things")
        elif term in owner:
            why.append(f"**{term}** — from your project *{owner[term]}*")
        else:
            why.append(f"**{term}** — matches your work")
    return {
        "id": d["id"], "title": d["title"], "url": d["url"], "source": d["source"],
        "summary": d["summary"], "stars": d["stars"], "score": d["score"],
        "license": spdx or None, "verdict": verdict, "license_note": note,
        "why": why, "is_repo": bool(slug),
        "adopt_does": (
            "Pulls the README and description to lattice_data/adopted/, settles the "
            "licence, and files a task with the reading already done."
            if slug else
            "Files a task with what is known, and records where to read the rest. "
            "Only GitHub sources can have their material pulled automatically."),
    }
