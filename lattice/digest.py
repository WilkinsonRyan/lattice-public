"""The morning digest — what needs the user today, in one page.

Answers two questions he actually asks: "what am I blocking?" and "what is
each live project waiting on?" Everything here is read from the database, so
the digest cannot drift from the task list the way a hand-kept note does.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from .core import data_home, open_db

# Tasks that open with one of these are a question for the user, not work I can do
# unattended: they need a measurement, a decision, a purchase, or his eyes on
# something physical.
NEEDS_YOU = re.compile(
    r"^\s*(confirm|decide|measure|order|check|answer|choose|verify|pick|test|"
    r"bench-test|record|solder|wire|attach|print|set |give |rotate|revoke)\b",
    re.I,
)
# Anything that is a live credential exposure outranks everything else.
# "password" alone is not a credential exposure — a storefront gate uses that word too.
SECURITY = re.compile(
    r"\b(api key|service account|credential|secret|token|rotate|revoke|leaked)\b", re.I)


def digest_dir() -> Path:
    return data_home() / "digest"


def _tasks(conn):
    """Open, not declined, and not snoozed by a Wait or Ignore press."""
    from .triage import visible_filter
    return conn.execute(
        f"""SELECT t.id, t.title, p.name AS project, p.status AS pstatus, p.side
             FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE {visible_filter()}
            ORDER BY p.name, t.id"""
    ).fetchall()


def classify() -> dict:
    """One classification, shared by the markdown and the interactive page.

    Two renderers drifting apart is how a digest starts lying, so the grouping
    lives here and nowhere else.
    """
    conn = open_db()
    tasks = _tasks(conn)
    projects = conn.execute(
        """SELECT name, side, status, description,
                  (SELECT COUNT(*) FROM tasks t
                    WHERE t.project_id = projects.id AND t.status='open') AS open
             FROM projects ORDER BY side, name"""
    ).fetchall()

    security = [t for t in tasks if SECURITY.search(t["title"])]
    sec_ids = {t["id"] for t in security}
    loose = [t for t in tasks if t["pstatus"] == "finished" and t["id"] not in sec_ids]
    live = [t for t in tasks if t["pstatus"] in ("active", "paused") and t["id"] not in sec_ids]
    waiting = [t for t in live if NEEDS_YOU.match(t["title"])]
    wait_ids = {t["id"] for t in waiting}
    return {"tasks": tasks, "projects": projects, "security": security,
            "loose": loose, "live": live, "waiting": waiting, "wait_ids": wait_ids}


def build() -> str:
    today = datetime.date.today()
    c = classify()
    tasks, projects = c["tasks"], c["projects"]
    security, loose, live = c["security"], c["loose"], c["live"]
    waiting, wait_ids = c["waiting"], c["wait_ids"]

    L: list[str] = []
    L.append(f"# Priorities — {today:%A %-d %B %Y}")
    L.append("")
    active = [p for p in projects if p["status"] == "active"]
    L.append(f"{len(tasks)} open · {len(waiting)} waiting on you · "
             f"{len(active)} active projects")
    L.append("")

    if security:
        L.append("## Security — do these first")
        for t in security:
            flag = "" if t["pstatus"] == "active" else f"  _({t['pstatus']} project)_"
            L.append(f"- **[{t['id']}] {t['project']}** — {t['title']}{flag}")
        L.append("")

    if waiting:
        L.append("## Waiting on you")
        L.append("_Nothing moves on these until you answer, measure or buy something._")
        L.append("")
        for proj in dict.fromkeys(t["project"] for t in waiting):
            L.append(f"**{proj}**")
            for t in (x for x in waiting if x["project"] == proj):
                L.append(f"- [{t['id']}] {t['title']}")
            L.append("")

    L.append("## Active projects")
    for p in active:
        mine = [t for t in live if t["project"] == p["name"] and t["id"] not in wait_ids]
        head = f"### {p['name']} · {p['side']}"
        L.append(head + (f" · {p['open']} open" if p["open"] else " · nothing open"))
        if p["description"]:
            L.append(f"_{p['description']}_")
        for t in mine:
            L.append(f"- [{t['id']}] {t['title']}")
        if p["open"] and not mine:
            L.append("- _(everything open is in Waiting on you, above)_")
        L.append("")

    paused = [p for p in projects if p["status"] == "paused"]
    if paused:
        L.append("## Paused")
        for p in paused:
            n = f" — {p['open']} open" if p["open"] else ""
            L.append(f"- **{p['name']}** ({p['side']}){n}")
        L.append("")

    if loose:
        L.append("## Loose ends on finished projects")
        for t in loose:
            L.append(f"- [{t['id']}] **{t['project']}** — {t['title']}")
        L.append("")

    fin = [p for p in projects if p["status"] == "finished"]
    L.append(f"_{len(fin)} finished projects not shown. `lattice project list --all` for everything._")
    return "\n".join(L) + "\n"


def to_html(md: str) -> str:
    """A readable page, because a raw .md file opens in whatever the OS guesses.

    Deliberately tiny: headings, list items, bold, and inline code. The digest is
    generated markdown with a known shape, not arbitrary input.
    """
    import html as _h

    body: list[str] = []
    in_list = False

    def inline(t: str) -> str:
        t = _h.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
        t = re.sub(r"_(.+?)_", r"<em>\1</em>", t)
        return t

    for line in md.splitlines():
        if line.startswith("- "):
            if not in_list:
                body.append("<ul>"); in_list = True
            body.append(f"<li>{inline(line[2:])}</li>")
            continue
        if in_list:
            body.append("</ul>"); in_list = False
        if line.startswith("### "):
            body.append(f"<h3>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            sec = line[3:]
            cls = " class='sec-alert'" if sec.lower().startswith(("security", "waiting")) else ""
            body.append(f"<h2{cls}>{inline(sec)}</h2>")
        elif line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    if in_list:
        body.append("</ul>")

    built = f"{datetime.date.today():%Y-%m-%d}"
    return f"""<!doctype html><meta charset="utf-8">
<title>Lattice priorities — {datetime.date.today():%-d %b %Y}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 :root{{--ink:#16191f;--soft:#5d6672;--bg:#f6f7f9;--card:#fff;--line:#e2e6ec;
        --alert:#b3372a;--accent:#2f6fad}}
 @media (prefers-color-scheme:dark){{:root{{--ink:#e6eaf0;--soft:#98a3b2;--bg:#0f1317;
        --card:#171c22;--line:#2a323c;--alert:#e8796a;--accent:#79aee0}}}}
 body{{background:var(--bg);color:var(--ink);margin:0;padding:38px 20px 64px;
   font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
 main{{max-width:760px;margin:0 auto;background:var(--card);border:1px solid var(--line);
   border-radius:10px;padding:32px 34px}}
 h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}}
 h1+p{{margin:0 0 26px;color:var(--soft);font-variant-numeric:tabular-nums}}
 h2{{font-size:17px;margin:32px 0 6px;padding-top:16px;border-top:1px solid var(--line)}}
 h2.sec-alert{{color:var(--alert)}}
 h3{{font-size:14px;margin:20px 0 4px;color:var(--accent)}}
 p{{margin:6px 0}} em{{color:var(--soft);font-style:normal}}
 ul{{margin:8px 0 14px;padding-left:22px}} li{{margin:5px 0}}
 code{{background:var(--bg);border:1px solid var(--line);border-radius:4px;
   padding:1px 5px;font:12.5px ui-monospace,Menlo,monospace}}
 @media (max-width:560px){{main{{padding:22px 18px}}body{{padding:18px 12px 40px}}}}
</style>
<main>{''.join(body)}</main>
<script>
/* latest.html is overwritten every morning, so a tab left open overnight keeps
   showing yesterday's list — `open` focuses that stale tab without reloading it.
   Reload when the tab is looked at again and the page in front of us was built
   on an earlier day. Guarded per build date so a digest that genuinely has not
   been rebuilt cannot loop. */
(function () {{
  var built = "{built}";
  function today() {{
    var d = new Date(), p = function (n) {{ return String(n).padStart(2, "0"); }};
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  }}
  function refreshIfStale() {{
    if (document.visibilityState !== "visible") return;
    if (built >= today()) return;
    try {{
      if (sessionStorage.getItem("lattice-digest-reloaded") === built) return;
      sessionStorage.setItem("lattice-digest-reloaded", built);
    }} catch (e) {{}}
    location.reload();
  }}
  document.addEventListener("visibilitychange", refreshIfStale);
  window.addEventListener("focus", refreshIfStale);
}})();
</script>
"""


def write(text: str | None = None) -> Path:
    text = text or build()
    d = digest_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.date.today():%Y-%m-%d}.md"
    path.write_text(text)
    (d / "latest.md").write_text(text)
    (d / "latest.html").write_text(to_html(text))
    (d / f"{datetime.date.today():%Y-%m-%d}.html").write_text(to_html(text))
    return path
