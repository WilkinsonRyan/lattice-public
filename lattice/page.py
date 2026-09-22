"""Lattice Today — the tabbed daily page served from 127.0.0.1.

One tab per source, so Monday, Calendar, Zoom and Slack drop in later without a
rewrite. Everything is rendered server-side and tabs switch with no network call,
because a daily page that spinners between tabs is worse than a long list.

The rule the interface is built around: you should know what a click will do
before you make it. Every control carries a preview that appears on hover and on
keyboard focus, in plain language, above the buttons.
"""

from __future__ import annotations

import datetime
import html as _h
import json

from . import changes, digest
from .triage import guidance

ACTS = [
    ("deny",   "Deny",   "Closes it. Nothing on your machine changes."),
    ("wait",   "Wait",   "Hides it until tomorrow morning."),
    ("ignore", "Ignore", "Hides it for seven days."),
    ("never",  "Never",  "Hides it for good. Nothing is changed — it just stops appearing."),
    ("ask",    "Question", "Ask Claude about this one task. Costs a few cents per question."),
    ("info",   "Info",   "Explains what this is and what Approve would do."),
]

# Sources that exist but have nothing behind them yet. Named honestly rather
# than shown as empty tabs that look broken.
PENDING = [("monday", "Monday"), ("calendar", "Calendar"), ("zoom", "Zoom"),
           ("slack", "Slack"), ("make", "Make")]


def info_for(task_id: int) -> dict:
    g = guidance(task_id) or {}
    spec = g.get("action_spec") or {}
    return {
        "task": task_id,
        "explanation": g.get("explanation") or "",
        "fix": g.get("fix"),
        "your_part": g.get("your_part"),
        "action_kind": g.get("action_kind", "none"),
        "choices": [{"label": c["label"], "fix": c.get("fix")} for c in (g.get("choices") or [])],
        "file": spec.get("path") or spec.get("dest") or "",
        "line": spec.get("line"),
    }


def _task(t, g: dict) -> str:
    tid = t["id"]
    g = g or {}
    project = t["project"] if "project" in t.keys() else ""
    choices = g.get("choices") or []
    if choices:
        approve = "".join(
            f'<button class="act act-approve" data-do="approve" data-choice="{_h.escape(c["key"])}"'
            f' data-preview="{_h.escape(c.get("fix") or "Records this decision and closes the task.")}">'
            f'Approve {_h.escape(c["label"])}</button>' for c in choices)
    else:
        prev = g.get("fix") or "Closes this task. There is no part of it Lattice can carry out."
        approve = (f'<button class="act act-approve" data-do="approve" '
                   f'data-preview="{_h.escape(prev)}">Approve</button>')
    acts = approve + "".join(
        f'<button class="act" data-do="{k}" data-preview="{_h.escape(p)}"'
        + (f' aria-expanded="false" aria-controls="info-{tid}"' if k == "info" else "")
        + f'>{lbl}</button>'
        for k, lbl, p in ACTS)
    # The project name leads, so a task you do not recognise still tells you what
    # it belongs to before you have read a word of it.
    owner = f'<span class="owner">{_h.escape(project)}</span>' if project else ""
    expl = g.get("explanation")
    if expl:
        line = f'<p class="explain">{owner}{_h.escape(expl)}</p>'
    else:
        line = (f'<p class="explain none">{owner}No write-up yet — nobody has recorded '
                f'what this is or what would close it.</p>')
    return f"""<li class="task" data-task="{tid}" data-state="open">
  <div class="task-body">
    <p class="title"><span class="tid">{tid}</span>{_h.escape(t["title"])}</p>
    {line}
    <p class="preview" aria-live="polite"></p>
    <div class="acts">{acts}</div>
    <div class="info" id="info-{tid}" hidden></div>
  </div>
  <p class="result" role="status"></p>
</li>"""


def _group(title: str, tasks, notes, *, alert=False, note="") -> str:
    if not tasks:
        return ""
    cls = " class='alert'" if alert else ""
    sub = f'<p class="note">{_h.escape(note)}</p>' if note else ""
    items = "".join(_task(t, notes.get(t["id"]) or {}) for t in tasks)
    return f'<h2{cls}>{_h.escape(title)}</h2>{sub}<ul>{items}</ul>'


def _ago(ts: float) -> str:
    mins = int((datetime.datetime.now().timestamp() - ts) / 60)
    if mins < 60:
        return f"{mins} min ago"
    if mins < 60 * 36:
        return f"{mins // 60} h ago"
    return f"{mins // 1440} d ago"


def _updated_panel() -> str:
    groups = changes.recent(24)
    if not groups:
        return '<p class="empty">Nothing changed in the last 24 hours.</p>'
    out = ['<h2>Updated in the last 24 hours</h2>',
           '<p class="note">Read from file timestamps, so it catches edits you never committed.</p>']
    for g in groups:
        out.append(f'<h3>{_h.escape(g["title"])}</h3><ul class="changes">')
        for it in g["items"]:
            files = ""
            if it["files"]:
                shown = ", ".join(_h.escape(f) for f in it["files"][:4])
                more = f' +{it["count"] - len(it["files"][:4])} more' if it["count"] > 4 else ""
                files = f'<span class="files">{shown}{more}</span>'
            side = f'<span class="chip">{_h.escape(it["side"])}</span>' if it["side"] else ""
            out.append(f'<li><span class="when">{_ago(it["newest"])}</span>'
                       f'<span class="what">{_h.escape(it["label"])}{side}</span>{files}</li>')
        out.append("</ul>")
    return "".join(out)


def _future_panel() -> str:
    stale = changes.stale_projects(120)
    out = ['<h2>Projects you could retire</h2>',
           '<p class="note">Registered projects with no file touched in 120 days.</p>']
    if not stale:
        out.append('<p class="empty">Nothing has gone stale.</p>')
    else:
        out.append('<ul class="changes">')
        for s in stale:
            warn = (f'<span class="chip warn">{s["open"]} open task(s)</span>'
                    if s["open"] else "")
            out.append(f'<li><span class="when">{_h.escape(s["why"])}</span>'
                       f'<span class="what">{_h.escape(s["name"])}'
                       f'<span class="chip">{_h.escape(s["side"])}</span>{warn}</span>'
                       f'<span class="files">{_h.escape(s["path"])}</span></li>')
        out.append("</ul>")
    from . import sweep
    found = sweep.recent(15)
    out.append('<h2>Ideas from the wider world</h2>')
    if not found:
        out.append('<p class="empty">The first sweep has not run yet.</p>')
    else:
        out.append('<p class="note">A daily sweep of GitHub and Hacker News, scored '
                   'against your own projects and the way Lattice stores things. '
                   'Ranked by how closely it matches, not by how popular it is.</p>')
        out.append('<ul class="changes">')
        for f in found:
            stars = (f'<span class="chip">{f["stars"]:,}</span>'
                     if f["source"] == "GitHub" and f["stars"] else
                     (f'<span class="chip">{f["stars"]} pts</span>' if f["stars"] else ""))
            why = ", ".join(f["matched"])
            out.append(
                f'<li class="find" data-item="{_h.escape(f["id"])}">'
                f'<span class="when">{_h.escape(f["source"])}</span>'
                f'<span class="what"><a href="{_h.escape(f["url"])}" target="_blank" '
                f'rel="noreferrer">{_h.escape(f["title"])}</a>{stars}</span>'
                f'<span class="files">{_h.escape(f["summary"][:160])}'
                + (f'<br>matches: {_h.escape(why)}' if why else "")
                + '<span class="preview" aria-live="polite"></span>'
                  '<span class="acts">'
                  '<button class="act act-approve" data-sweep="adopt"'
                  ' data-preview="Pulls the source down, settles the licence, and files it '
                  'to work on now.">Adopt</button>'
                  '<button class="act" data-sweep="queue"'
                  ' data-preview="Adds it to the queue to deal with later. Fetches nothing '
                  'now.">Add to queue</button>'
                  '<button class="act" data-sweep="dismiss"'
                  ' data-preview="Hides it for good.">Dismiss</button>'
                  '<button class="act" data-sweep="info" aria-expanded="false"'
                  ' data-preview="What this is, why it was chosen, and its licence.">Info'
                  '</button></span>'
                  '<div class="info" hidden></div>'
                  '<span class="result" role="status"></span></span></li>')
        out.append("</ul>")
    return "".join(out)


MAIL_ACTS = [
    ("reply",  "Reply",     "Opens a box here; sending replies in the real thread as you."),
    ("star",   "Star",      "Stars it in your Gmail inbox, right now."),
    ("label",  "Add Label", "Choose one of your labels, or make a new one."),
    ("spam",   "Spam",      "Moves it to Gmail's spam folder. You can undo it right here."),
    ("ignore", "Ignore",    "Hides it from this list until tomorrow. Gmail is untouched."),
    ("info",   "Info",      "Who sent it, when, and why it survived the filter."),
]


def _sender(raw: str) -> tuple[str, str]:
    """Display name and address, split so the name can lead."""
    import re
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', raw or "")
    if m:
        return m.group(1).strip() or m.group(2), m.group(2)
    return (raw or "").strip(), (raw or "").strip()


def _mail(item: dict) -> str:
    name, addr = _sender(item["from"])
    chips = ""
    if item["unread"]:
        chips += '<span class="chip">unread</span>'
    if item["starred"]:
        chips += '<span class="chip">starred</span>'
    for l in item["labels"][:3]:
        chips += f'<span class="chip">{_h.escape(l.replace("Label_", "")[:24])}</span>'
    if item["kept_because"]:
        chips += f'<span class="chip warn">{_h.escape(item["kept_because"])}</span>'
    acts = "".join(
        f'<button class="act{" act-approve" if k == "reply" else ""}" data-mail="{k}"'
        f' data-preview="{_h.escape(p)}">{lbl}</button>' for k, lbl, p in MAIL_ACTS)
    return f"""<li class="task mail" data-id="{_h.escape(item["id"])}"
      data-thread="{_h.escape(item["thread"])}" data-from="{_h.escape(addr)}"
      data-date="{_h.escape(item["date"])}" data-why="{_h.escape(item["kept_because"])}"
      data-state="open">
  <div class="task-body">
    <p class="title"><span class="who">{_h.escape(name)}</span>{_h.escape(item["subject"])}</p>
    <p class="explain">{_h.escape(item["snippet"][:180])}</p>
    <p class="chips">{chips}</p>
    <p class="preview" aria-live="polite"></p>
    <div class="acts">{acts}</div>
    <div class="info" hidden></div>
  </div>
  <p class="result" role="status"></p>
</li>"""


def _gmail_panel() -> str:
    from . import gmail_auth, gmail_source
    if not gmail_auth.have_credentials():
        return ('<h2>Gmail</h2><p class="empty">Not connected yet.</p>')
    snap = gmail_source.cached()
    items = snap["items"]
    if snap["error"] and not items:
        return (f'<h2>Gmail</h2><p class="empty">Could not reach Gmail — '
                f'{_h.escape(snap["error"][:160])}</p>')
    if not snap["fetched_at"]:
        return ('<h2>Gmail</h2><p class="empty">First sweep of your inbox is running. '
                'It takes about half a minute; reload shortly.</p>')
    seen = items[0].get("seen") if items else 0
    dropped = items[0].get("dropped") if items else 0
    when = _ago(datetime.datetime.fromisoformat(snap["fetched_at"]).timestamp())
    head = (f'<h2>Needs you — last 24 hours</h2>'
            f'<p class="note">{seen} messages arrived · {dropped} were bulk, no-reply or '
            f'platform noise · {len(items)} left. Read {when}.'
            + (' <strong>Showing the last good sweep.</strong>' if snap["stale"] else '')
            + '</p>')
    if not items:
        return head + '<p class="empty">Nothing in the last 24 hours wants you.</p>'
    return head + '<ul>' + "".join(_mail(i) for i in items) + '</ul>'


def _pending_panel(label: str) -> str:
    return (f'<h2>{_h.escape(label)}</h2>'
            f'<p class="empty">Not connected. {_h.escape(label)} is installed as an '
            'unauthenticated stub, so nothing can be read from it yet.</p>')


def render(token: str) -> str:
    c = digest.classify()
    today = datetime.date.today()
    security, loose, live = c["security"], c["loose"], c["live"]
    waiting, wait_ids, projects = c["waiting"], c["wait_ids"], c["projects"]
    active = [p for p in projects if p["status"] == "active"]
    notes = {t["id"]: guidance(t["id"]) for t in c["tasks"]}
    unwritten = [t for t in c["tasks"] if not notes.get(t["id"])]

    urgent = _group("Security — do these first", security, notes, alert=True)
    urgent += _group("Waiting on you", waiting, notes, alert=True,
                     note="Nothing moves on these until you answer, measure or buy something.")
    if not urgent:
        urgent = '<p class="empty">Nothing urgent. Everything open is ordinary work.</p>'

    lat = ""
    for p in active:
        mine = [t for t in live if t["project"] == p["name"] and t["id"] not in wait_ids]
        if mine:
            lat += _group(f"{p['name']} · {p['side']}", mine, notes)
    lat += _group("Loose ends on finished projects", loose, notes)
    if not lat:
        lat = '<p class="empty">No ordinary work open — check the Urgent tab.</p>'
    lat += _updated_panel()

    gap = ""
    if unwritten:
        which = ", ".join(str(t["id"]) for t in unwritten[:6])
        gap = (f'<p class="gap">{len(unwritten)} task(s) have no write-up yet '
               f'({which}) — added since the last session. Ask Claude to write them '
               f'up and they will explain themselves here.</p>')

    tabs = [("urgent", "Urgent", len(security) + len(waiting), urgent),
            ("lattice", "Lattice", len(c["tasks"]), lat),
            ("gmail", "Gmail", 0, _gmail_panel()),
            ("future", "Future", len(changes.stale_projects(120)), _future_panel())]
    tabs += [(k, lbl, 0, _pending_panel(lbl)) for k, lbl in PENDING]

    nav = "".join(
        f'<button role="tab" class="tab" data-tab="{k}" id="tab-{k}"'
        f' aria-controls="panel-{k}" aria-selected="{"true" if i == 0 else "false"}"'
        f' tabindex="{0 if i == 0 else -1}"'
        f'{" data-pending=\"1\"" if k in dict(PENDING) else ""}>{_h.escape(lbl)}'
        + (f'<span class="badge">{n}</span>' if n else "") + '</button>'
        for i, (k, lbl, n, _) in enumerate(tabs))
    panels = "".join(
        f'<section role="tabpanel" id="panel-{k}" aria-labelledby="tab-{k}"'
        f'{"" if i == 0 else " hidden"}>{body}</section>'
        for i, (k, lbl, n, body) in enumerate(tabs))

    return f"""<!doctype html><meta charset="utf-8">
<title>Lattice Today — {today:%-d %b %Y}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
 /* Light is the default; the others are opt-in. A theme the viewer chose wins
    over the system setting in both directions, so every palette is declared as a
    complete set rather than as a patch over another one. */
 :root,:root[data-theme="light"]{{--ink:#16191f;--soft:#5d6672;--bg:#f6f7f9;--card:#fff;
   --line:#e2e6ec;--alert:#b3372a;--accent:#2f6fad;--ok:#2d6a4a;--wash:#eef1f5;
   --ring:#2f6fad;--warn:#8a5a12}}
 @media (prefers-color-scheme:dark){{:root:not([data-theme]){{--ink:#e6eaf0;--soft:#9aa5b4;
   --bg:#0f1317;--card:#171c22;--line:#2a323c;--alert:#e8796a;--accent:#79aee0;
   --ok:#63b98c;--wash:#1e242c;--ring:#79aee0;--warn:#d8a54a}}}}
 :root[data-theme="dark"]{{--ink:#e6eaf0;--soft:#9aa5b4;--bg:#0f1317;--card:#171c22;
   --line:#2a323c;--alert:#e8796a;--accent:#79aee0;--ok:#63b98c;--wash:#1e242c;
   --ring:#79aee0;--warn:#d8a54a}}
 :root[data-theme="dracula"]{{--ink:#f8f8f2;--soft:#b6bad0;--bg:#21222c;--card:#282a36;
   --line:#44475a;--alert:#ff8888;--accent:#bd93f9;--ok:#69ff94;--wash:#343746;
   --ring:#bd93f9;--warn:#ffb86c}}
 *{{box-sizing:border-box}}
 body{{background:var(--bg);color:var(--ink);margin:0;padding:34px 20px 72px;
   font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   -webkit-font-smoothing:antialiased}}
 ::selection{{background:color-mix(in srgb,var(--accent) 22%,transparent)}}
 main{{max-width:860px;margin:0 auto;background:var(--card);border:1px solid var(--line);
   border-radius:14px;padding:30px 34px 36px}}
 h1{{font-size:25px;margin:0 0 3px;letter-spacing:-.022em}}
 .counts{{margin:0;color:var(--soft);font-variant-numeric:tabular-nums}}
 .gap{{margin:8px 0 0;font-size:13px;color:var(--warn);max-width:68ch;
   overflow-wrap:anywhere}}
 .top{{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;
   flex-wrap:wrap;margin-bottom:20px}}
 .themes{{display:flex;gap:3px;padding:3px;border:1px solid var(--line);border-radius:9px;
   background:var(--wash)}}
 .themes button{{font:inherit;font-size:12px;padding:5px 10px;border:0;border-radius:6px;
   background:transparent;color:var(--soft);cursor:pointer;
   transition:background .16s ease,color .16s ease}}
 .themes button:hover{{color:var(--ink)}}
 .themes button[aria-pressed="true"]{{background:var(--card);color:var(--ink);
   box-shadow:0 1px 2px rgba(0,0,0,.14)}}
 .themes button:focus-visible{{outline:2px solid var(--ring);outline-offset:1px}}
 /* tabs */
 .tabs{{display:flex;flex-wrap:wrap;gap:4px;border-bottom:1px solid var(--line);
   margin:0 -6px 6px;padding:0 6px}}
 .tab{{font:inherit;font-size:13.5px;font-weight:500;padding:9px 13px 11px;border:0;
   background:transparent;color:var(--soft);cursor:pointer;border-radius:8px 8px 0 0;
   position:relative;display:flex;align-items:center;gap:7px;
   transition:color .18s ease,background .18s ease}}
 .tab:hover{{color:var(--ink);background:var(--wash)}}
 .tab[aria-selected="true"]{{color:var(--ink)}}
 .tab[aria-selected="true"]::after{{content:"";position:absolute;left:11px;right:11px;
   bottom:-1px;height:2px;background:var(--accent);border-radius:2px}}
 .tab[data-pending]{{opacity:.5}}
 .badge{{font-size:11px;font-variant-numeric:tabular-nums;padding:1px 6px;border-radius:9px;
   background:var(--wash);border:1px solid var(--line);color:var(--soft)}}
 .tab[aria-selected="true"] .badge{{border-color:color-mix(in srgb,var(--accent) 40%,var(--line))}}
 .tab:focus-visible{{outline:2px solid var(--ring);outline-offset:-2px}}
 @media (prefers-reduced-motion:no-preference){{
   [role=tabpanel]{{animation:rise .34s cubic-bezier(.16,1,.3,1)}}
   @keyframes rise{{from{{opacity:0;transform:translateY(4px)}}to{{opacity:1;transform:none}}}}
 }}
 /* content */
 h2{{font-size:17px;margin:30px 0 6px;padding-top:15px;border-top:1px solid var(--line);
   letter-spacing:-.01em}}
 section>h2:first-child{{margin-top:18px;border-top:0;padding-top:0}}
 h2.alert{{color:var(--alert)}}
 h3{{font-size:13px;margin:22px 0 6px;color:var(--soft);font-weight:600;
   letter-spacing:.03em;text-transform:uppercase}}
 .note{{margin:0 0 10px;color:var(--soft);font-size:13.5px;max-width:68ch;
   overflow-wrap:anywhere}}
 ul{{list-style:none;margin:0;padding:0}}
 .task{{border-bottom:1px solid var(--line);padding:14px 0}}
 .task:last-child{{border-bottom:0}}
 .title{{margin:0;max-width:68ch;display:flex;gap:9px;align-items:baseline;
   overflow-wrap:anywhere}}
 .tid{{flex:none;min-width:2.2em;padding:1px 6px;border:1px solid var(--line);
   border-radius:5px;background:var(--wash);color:var(--soft);font-size:12px;
   font-variant-numeric:tabular-nums;text-align:center}}
 .explain{{margin:5px 0 0;color:var(--soft);max-width:68ch;font-size:14px;
   overflow-wrap:anywhere}}
 .explain.none{{font-style:italic;opacity:.75}}
 .owner{{font-weight:600;font-style:normal;color:var(--ink);margin-right:.4em}}
 .explain.none .owner{{color:var(--soft)}}
 .preview{{margin:9px 0 0;min-height:1.25em;font-size:12.5px;color:var(--accent);
   max-width:68ch;overflow-wrap:anywhere;opacity:0;transition:opacity .18s ease}}
 .preview.on{{opacity:1}}
 .acts{{display:flex;flex-wrap:wrap;gap:6px;margin-top:5px}}
 .act{{font:inherit;font-size:12.5px;line-height:1.2;padding:6px 11px;border-radius:7px;
   border:1px solid var(--line);background:transparent;color:var(--soft);cursor:pointer;
   transition:background .16s ease,color .16s ease,border-color .16s ease}}
 .act:hover{{background:var(--wash);color:var(--ink);border-color:var(--soft)}}
 .act-approve{{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 45%,var(--line))}}
 .act-approve:hover{{background:color-mix(in srgb,var(--accent) 12%,transparent);
   color:var(--accent);border-color:var(--accent)}}
 .act:focus-visible{{outline:2px solid var(--ring);outline-offset:2px}}
 .task[aria-busy=true] .act{{opacity:.45;pointer-events:none}}
 .info{{overflow-wrap:anywhere;margin-top:12px;padding:14px 16px;background:var(--wash);
   border:1px solid var(--line);border-radius:9px;font-size:14px;max-width:68ch}}
 .info h3{{margin:0 0 3px;font-size:12px;text-transform:uppercase;color:var(--soft)}}
 .info p{{margin:0 0 12px}} .info p:last-child{{margin-bottom:0}}
 .info .where{{font-variant-numeric:tabular-nums;color:var(--soft);font-size:13px}}
 .info .where.bad{{color:var(--alert)}}
 .result{{margin:0;font-size:13.5px;color:var(--ok);display:flex;flex-wrap:wrap;
   gap:8px;align-items:center}}
 .result .act{{color:var(--soft)}}
 .result.bad{{color:var(--alert)}}
 .task .result{{display:none}}
 .task[data-state=done] .task-body{{display:none}}
 .task[data-state=done] .result{{display:block}}
 .task[data-state=done]{{opacity:.7}}
 .task.queued{{opacity:.8}}
 .task.queued .acts{{display:none}}
 .task.queued .result{{display:flex;color:var(--accent)}}
 .changes{{margin:0 0 6px}}
 .changes li{{display:grid;grid-template-columns:8.5em 1fr;gap:4px 14px;
   padding:7px 0;border-bottom:1px solid var(--line);align-items:baseline}}
 .changes li:last-child{{border-bottom:0}}
 .find .acts{{margin-top:8px}}
 .find .preview{{display:block;margin:6px 0 0;min-height:1.1em;font-size:12.5px;
   color:var(--accent);opacity:0;transition:opacity .18s ease}}
 .find .preview.on{{opacity:1}}
 .find .info{{margin-top:10px;max-width:68ch}}
 .find.resolved{{opacity:.72}}
 .act:disabled{{opacity:.45;cursor:progress}}
 .why{{margin:0 0 12px;padding-left:20px}}
 .why li{{margin:3px 0}}
 .lic{{padding:9px 12px;border-radius:8px;border:1px solid var(--line)}}
 .lic-ok{{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}}
 .lic-blocked{{color:var(--alert);border-color:color-mix(in srgb,var(--alert) 45%,var(--line))}}
 .lic-unknown,.lic-check{{color:var(--warn);
   border-color:color-mix(in srgb,var(--warn) 40%,var(--line))}}
 .find .result{{display:block;margin-top:6px;font-size:12.5px;color:var(--ok)}}
 .find .result.bad{{color:var(--alert)}}
 .when{{color:var(--soft);font-size:12.5px;font-variant-numeric:tabular-nums}}
 .what{{font-size:14px;overflow-wrap:anywhere}}
 .files{{grid-column:2;color:var(--soft);font-size:12.5px;overflow-wrap:anywhere}}
 .chip{{margin-left:7px;padding:1px 7px;border-radius:9px;background:var(--wash);
   border:1px solid var(--line);color:var(--soft);font-size:11.5px}}
 .chip.warn{{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line))}}
 .empty{{color:var(--soft);margin:18px 0;max-width:68ch;overflow-wrap:anywhere}}
 a{{color:var(--accent);text-decoration-color:color-mix(in srgb,var(--accent) 40%,transparent);
   text-underline-offset:2px}}
 a:hover{{text-decoration-color:var(--accent)}}
 a:focus-visible{{outline:2px solid var(--ring);outline-offset:2px;border-radius:3px}}
 .who{{flex:none;font-weight:600;max-width:15em;overflow:hidden;text-overflow:ellipsis;
   white-space:nowrap}}
 .mail .title{{gap:11px}}
 .chips{{margin:7px 0 0;display:flex;flex-wrap:wrap;gap:5px}}
 .chips .chip{{margin:0}}
 .compose{{margin-top:12px;max-width:68ch}}
 .compose textarea{{width:100%;min-height:104px;padding:11px 13px;border-radius:9px;
   border:1px solid var(--line);background:var(--bg);color:var(--ink);font:inherit;
   font-size:14px;resize:vertical;caret-color:var(--accent)}}
 .compose textarea:focus-visible{{outline:2px solid var(--ring);outline-offset:1px;
   border-color:var(--accent)}}
 .compose .to{{margin:0 0 6px;color:var(--soft);font-size:12.5px;overflow-wrap:anywhere}}
 .picker{{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px;max-width:68ch}}
 .picker input{{font:inherit;font-size:12.5px;padding:6px 10px;border-radius:7px;
   border:1px solid var(--line);background:var(--bg);color:var(--ink);
   caret-color:var(--accent);min-width:11em}}
 .picker input:focus-visible{{outline:2px solid var(--ring);outline-offset:1px}}
 @media (max-width:560px){{main{{padding:20px 17px 28px}}body{{padding:16px 11px 44px}}
   .changes li{{grid-template-columns:1fr}} .files{{grid-column:1}}}}
</style>
<main>
 <div class="top">
   <div>
     <h1>Lattice Today</h1>
     <p class="counts">{today:%A %-d %B %Y} · {len(c['tasks'])} open · {len(waiting)} waiting on you</p>
     {gap}
   </div>
   <div class="themes" role="group" aria-label="Theme">
     <button data-theme-set="system" aria-pressed="false">System</button>
     <button data-theme-set="light" aria-pressed="false">Light</button>
     <button data-theme-set="dark" aria-pressed="false">Dark</button>
     <button data-theme-set="dracula" aria-pressed="false">Dracula</button>
   </div>
 </div>
 <div class="tabs" role="tablist" aria-label="Sources">{nav}</div>
 {panels}
</main>
<script>
const TOKEN = {json.dumps(token)};

/* ---- theme: remembered per browser, applied before anything else ---- */
(function () {{
  let saved = null;
  try {{ saved = localStorage.getItem("lattice-theme"); }} catch (e) {{}}
  applyTheme(saved || "system");
}})();
function applyTheme(name) {{
  if (name === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", name);
  try {{ localStorage.setItem("lattice-theme", name); }} catch (e) {{}}
  document.querySelectorAll("[data-theme-set]").forEach(b =>
    b.setAttribute("aria-pressed", b.dataset.themeSet === name ? "true" : "false"));
}}

/* ---- tabs: no network, keyboard-navigable ---- */
const tabs = [...document.querySelectorAll('[role=tab]')];
function select(tab) {{
  tabs.forEach(t => {{
    const on = t === tab;
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
    document.getElementById("panel-" + t.dataset.tab).hidden = !on;
  }});
  tab.focus({{preventScroll: true}});
}}
document.querySelector(".tabs").addEventListener("click", e => {{
  const t = e.target.closest("[role=tab]"); if (t) select(t);
}});
document.querySelector(".tabs").addEventListener("keydown", e => {{
  const i = tabs.indexOf(document.activeElement);
  if (i < 0) return;
  const next = {{ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1}}[e.key];
  if (next === undefined) return;
  e.preventDefault();
  select(tabs[(next + tabs.length) % tabs.length]);
}});

/* ---- say what a click will do, before it is made ---- */
document.addEventListener("pointerover", e => {{
  const b = e.target.closest(".act"); if (!b) return;
  const p = b.closest(".task").querySelector(".preview");
  p.textContent = b.dataset.preview || ""; p.classList.add("on");
}});
document.addEventListener("pointerout", e => {{
  const b = e.target.closest(".act"); if (!b) return;
  b.closest(".task").querySelector(".preview").classList.remove("on");
}});
document.addEventListener("focusin", e => {{
  const b = e.target.closest(".act"); if (!b) return;
  const p = b.closest(".task").querySelector(".preview");
  p.textContent = b.dataset.preview || ""; p.classList.add("on");
}});
document.addEventListener("focusout", e => {{
  const b = e.target.closest(".act"); if (!b) return;
  b.closest(".task").querySelector(".preview").classList.remove("on");
}});

/* ---- acting ---- */
async function send(row, decision, choice) {{
  row.setAttribute("aria-busy", "true");
  const out = row.querySelector(".result");
  try {{
    const res = await fetch("/api/resolve", {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{task: +row.dataset.task, decision, choice, token: TOKEN}})
    }});
    const d = await res.json();
    out.classList.toggle("bad", !d.ok);
    out.textContent = d.ok ? label(decision, d) : "Failed — " + d.detail;
    // A queued task is NOT finished — a session still has to do it. Collapsing
    // it like a completed row would contradict its reappearance tomorrow.
    if (d.ok && d.state !== "queued") row.dataset.state = "done";
    if (d.ok && d.state === "queued") row.classList.add("queued");
  }} catch (err) {{
    out.classList.add("bad");
    out.textContent = "Could not reach Lattice — is the digest server running?";
  }} finally {{ row.removeAttribute("aria-busy"); }}
}}
function label(decision, d) {{
  if (decision === "approve") {{
    return (d.state === "queued" ? "Queued — " : "Approved — ") + d.detail;
  }}
  if (decision === "deny")    return "Denied — nothing was changed";
  if (decision === "never")   return "Ignored for good — " + d.detail;
  if (decision === "wait")    return "Waiting — " + d.detail;
  return "Ignored — " + d.detail;
}}
function asker(row) {{
  const panel = row.querySelector(".info");
  panel.hidden = false;
  panel.innerHTML =
    "<div class='compose'><p class='to'>Ask about this task. Claude reads the project "
    + "to answer and can only read \u2014 it cannot change anything. Each question is a "
    + "real model call and the cost is shown with the answer.</p>"
    + "<textarea placeholder='e.g. is this asking me to check a specific theme?'></textarea>"
    + "<div class='acts'><button class='act act-approve' data-askgo='1'>Ask</button>"
    + "<button class='act' data-cancel='1'>Cancel</button></div></div>";
  panel.querySelector("textarea").focus();
}}

async function askSend(row) {{
  const panel = row.querySelector(".info");
  const box = panel.querySelector("textarea");
  const q = box.value.trim();
  if (!q) {{ box.focus(); return; }}
  panel.innerHTML = "<p class='where'>Asking Claude \u2014 it is reading the project. "
                  + "This usually takes twenty to forty seconds.</p>";
  try {{
    const d = await (await fetch("/api/ask", {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{task: +row.dataset.task, question: q, token: TOKEN}})
    }})).json();
    if (!d.ok) {{
      panel.innerHTML = "<p class='where bad'>" + esc(d.detail) + "</p>"; return;
    }}
    const cost = d.cost ? " \u00b7 cost $" + Number(d.cost).toFixed(3)
                        : (d.queued ? "" : " \u00b7 cost not reported");
    panel.innerHTML = "<h3>You asked</h3><p>" + esc(q) + "</p><h3>Answer</h3><p>"
      + esc(d.answer).replace(/\\n\\n/g, "</p><p>").replace(/\\n/g, "<br>")
      + "</p><p class='where'>Answered by Claude" + cost + "</p>"
      + "<div class='acts'><button class='act' data-askmore='1'>Ask another</button></div>";
  }} catch (err) {{
    panel.innerHTML = "<p class='where bad'>Could not reach Lattice.</p>";
  }}
}}

async function showInfo(row, btn) {{
  const panel = row.querySelector(".info");
  if (btn.getAttribute("aria-expanded") === "true") {{
    panel.hidden = true; btn.setAttribute("aria-expanded", "false"); return;
  }}
  panel.innerHTML = "<p class='where'>Loading…</p>";
  panel.hidden = false; btn.setAttribute("aria-expanded", "true");
  try {{
    const d = await (await fetch("/api/info?task=" + row.dataset.task +
                                 "&t=" + encodeURIComponent(TOKEN))).json();
    let h = "<h3>What this is</h3><p>" +
            (d.explanation ? esc(d.explanation) : "Not written up yet.") + "</p>";
    if (d.choices && d.choices.length) {{
      h += "<h3>Your options</h3>";
      d.choices.forEach(c => {{ h += "<p><strong>" + esc(c.label) + "</strong> — " +
        esc(c.fix || "closes the task with this decision recorded") + "</p>"; }});
    }} else if (d.fix) {{
      h += "<h3>What Approve will do</h3><p>" + esc(d.fix) + "</p>";
    }} else {{
      h += "<h3>What Approve will do</h3><p>Nothing — there is no part of this " +
           "Lattice can carry out. Approve just closes it on your word.</p>";
    }}
    if (d.your_part) h += "<h3>Your part</h3><p>" + esc(d.your_part) + "</p>";
    if (d.file) h += "<p class='where'>" + esc(d.file) +
                     (d.line ? ", line " + d.line : "") + "</p>";
    panel.innerHTML = h;
  }} catch (err) {{
    panel.innerHTML = "<p class='where'>Could not load this — is the server running?</p>";
  }}
}}
const esc = s => String(s).replace(/[&<>"]/g, ch =>
  ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}})[ch]);

async function mail(row, action, extra) {{
  row.setAttribute("aria-busy", "true");
  const out = row.querySelector(".result");
  try {{
    const res = await fetch("/api/mail", {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(Object.assign({{id: row.dataset.id, action, token: TOKEN}}, extra || {{}}))
    }});
    const d = await res.json();
    out.classList.toggle("bad", !d.ok);
    out.textContent = d.ok ? "Done — " + d.detail : "Failed — " + d.detail;
    if (d.ok && action === "spam") {{
      row.dataset.state = "done";
      out.innerHTML = "Marked as spam — " + esc(d.detail) +
        " <button class='act' data-undo='1'>Undo</button>" +
        "<button class='act' data-block='1'>Never show this sender again</button>";
      return;
    }}
    if (d.ok && action === "unspam") {{
      row.dataset.state = "open";
      out.textContent = "Put back — " + d.detail;
      return;
    }}
    if (d.ok && action === "block") {{
      out.textContent = "Filter updated — " + d.detail;
      return;
    }}
    if (d.ok && (action === "ignore" || action === "reply")) row.dataset.state = "done";
    if (d.ok && action !== "ignore" && action !== "reply") {{
      row.querySelector(".info").hidden = true;
      setTimeout(() => {{ out.textContent = ""; }}, 4000);
    }}
  }} catch (err) {{
    out.classList.add("bad");
    out.textContent = "Could not reach Lattice — is the digest server running?";
  }} finally {{ row.removeAttribute("aria-busy"); }}
}}

function composer(row) {{
  const panel = row.querySelector(".info");
  panel.hidden = false;
  panel.innerHTML =
    "<div class='compose'><p class='to'>Replying to " + esc(row.dataset.from) +
    " — this sends from your account, in the real thread.</p>" +
    "<textarea placeholder='Type your reply…'></textarea>" +
    "<div class='acts'><button class='act act-approve' data-send='1'>Send reply</button>" +
    "<button class='act' data-cancel='1'>Cancel</button></div></div>";
  panel.querySelector("textarea").focus();
}}

async function picker(row) {{
  const panel = row.querySelector(".info");
  panel.hidden = false;
  panel.innerHTML = "<p class='where'>Loading your labels…</p>";
  const d = await (await fetch("/api/labels?t=" + encodeURIComponent(TOKEN))).json();
  let h = "<h3>Add a label</h3><div class='picker'>";
  (d.labels || []).forEach(l =>
    h += "<button class='act' data-label='" + esc(l.id) + "'>" + esc(l.name) + "</button>");
  h += "</div><div class='picker'><input placeholder='or a new label name'>" +
       "<button class='act act-approve' data-newlabel='1'>Create and apply</button>" +
       "<button class='act' data-cancel='1'>Cancel</button></div>";
  panel.innerHTML = h;
}}

function mailInfo(row) {{
  const panel = row.querySelector(".info");
  if (!panel.hidden) {{ panel.hidden = true; return; }}
  panel.hidden = false;
  panel.innerHTML =
    "<h3>From</h3><p>" + esc(row.dataset.from) + "</p>" +
    "<h3>Received</h3><p>" + esc(row.dataset.date) + "</p>" +
    "<h3>Why it is here</h3><p>" + (row.dataset.why ? esc(row.dataset.why) :
      "It carried no unsubscribe link, no bulk marker, and did not come from a " +
      "no-reply address or a platform — so it reads as correspondence.") + "</p>" +
    "<p class='where'><a href='https://mail.google.com/mail/u/0/#all/" +
    esc(row.dataset.thread) + "' target='_blank' rel='noreferrer'>Open in Gmail</a></p>";
}}

/* ---- every control answers the instant it is pressed ---- */
function busy(scope, msg) {{
  scope.setAttribute("aria-busy", "true");
  scope.querySelectorAll(".act").forEach(b => b.disabled = true);
  const out = scope.querySelector(".result");
  if (out) {{ out.classList.remove("bad"); out.textContent = msg; }}
}}
function done(scope) {{
  scope.removeAttribute("aria-busy");
  scope.querySelectorAll(".act").forEach(b => b.disabled = false);
}}

/* ---- findings from the sweep ---- */
const ADOPT_STEPS = "Adopting — reading the source, checking the licence, filing the task…";

async function sweepInfo(find, btn) {{
  const panel = find.querySelector(".info");
  if (btn.getAttribute("aria-expanded") === "true") {{
    panel.hidden = true; btn.setAttribute("aria-expanded", "false"); return;
  }}
  panel.hidden = false;
  btn.setAttribute("aria-expanded", "true");
  panel.innerHTML = "<p class='where'>Reading…</p>";
  try {{
    const d = await (await fetch("/api/sweep_info?item=" +
      encodeURIComponent(find.dataset.item) + "&t=" + encodeURIComponent(TOKEN))).json();
    if (d.error) {{ panel.innerHTML = "<p class='where'>" + esc(d.error) + "</p>"; return; }}
    const bold = t => esc(t).replace(/[*][*](.+?)[*][*]/g, "<strong>$1</strong>");
    let h = "<h3>What it is</h3><p>" + (esc(d.summary) || "No description published.") + "</p>";
    h += "<h3>Why it was chosen</h3><ul class='why'>" +
         d.why.map(w => "<li>" + bold(w) + "</li>").join("") + "</ul>";
    h += "<h3>Licence</h3><p class='lic lic-" + esc(d.verdict) + "'>" +
         esc(d.license_note) + "</p>";
    h += "<h3>What Adopt will do</h3><p>" + esc(d.adopt_does) + "</p>";
    h += "<p class='where'><a href='" + esc(d.url) + "' target='_blank' rel='noreferrer'>" +
         esc(d.url) + "</a></p>";
    panel.innerHTML = h;
  }} catch (err) {{
    panel.innerHTML = "<p class='where'>Could not reach Lattice.</p>";
  }}
}}

async function sweepAct(find, btn, action) {{
  busy(find, action === "adopt" ? ADOPT_STEPS
      : action === "queue" ? "Adding to the queue…" : "Dismissing…");
  const out = find.querySelector(".result");
  try {{
    const d = await (await fetch("/api/sweep", {{
      method: "POST", headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify({{id: find.dataset.item, action, token: TOKEN}})
    }})).json();
    out.classList.toggle("bad", !d.ok);
    if (!d.ok) {{ out.textContent = "Failed — " + d.detail; done(find); return; }}
    out.textContent = action === "adopt" ? "Adopted — " + d.detail
                    : action === "queue" ? "Queued — " + d.detail
                    : "Dismissed — it won't be shown again";
    find.classList.add("resolved");
    find.querySelector(".acts").remove();
    const panel = find.querySelector(".info");
    if (panel) panel.hidden = true;
  }} catch (err) {{
    out.classList.add("bad");
    out.textContent = "Could not reach Lattice — is the digest server running?";
    done(find);
  }}
}}

document.addEventListener("click", e => {{
  const btn = e.target.closest("button");
  if (!btn || btn.disabled) return;

  if (btn.dataset.themeSet) {{ applyTheme(btn.dataset.themeSet); return; }}

  // Sweep findings are <li class="find">, not .task — checking for .task first is
  // what previously swallowed every Adopt click without a trace.
  const find = btn.closest(".find");
  if (find) {{
    if (btn.dataset.sweep === "info") sweepInfo(find, btn);
    else if (btn.dataset.sweep) sweepAct(find, btn, btn.dataset.sweep);
    return;
  }}

  const row = btn.closest(".task"); if (!row) return;
  if (btn.closest("[data-undo]"))  {{ mail(row, "unspam"); return; }}
  if (btn.closest("[data-block]")) {{ mail(row, "block", {{sender: row.dataset.from}}); return; }}
  if (btn.closest("[data-cancel]")) {{ row.querySelector(".info").hidden = true; return; }}
  if (btn.closest("[data-askgo]"))  {{ askSend(row); return; }}
  if (btn.closest("[data-askmore]")) {{ asker(row); return; }}
  if (btn.closest("[data-send]")) {{
    const box = row.querySelector(".compose textarea");
    if (!box.value.trim()) {{ box.focus(); return; }}
    mail(row, "reply", {{text: box.value}}); return;
  }}
  const lab = btn.closest("[data-label]");
  if (lab) {{ mail(row, "label", {{label_id: lab.dataset.label}}); return; }}
  if (btn.closest("[data-newlabel]")) {{
    const inp = row.querySelector(".picker input");
    if (!inp.value.trim()) {{ inp.focus(); return; }}
    mail(row, "label", {{new_name: inp.value.trim()}}); return;
  }}
  if (!btn.classList.contains("act")) return;
  if (btn.dataset.mail) {{
    const a = btn.dataset.mail;
    if (a === "reply") composer(row);
    else if (a === "label") picker(row);
    else if (a === "info") mailInfo(row);
    else mail(row, a);
    return;
  }}
  if (btn.dataset.do === "ask") asker(row);
  else if (btn.dataset.do === "info") showInfo(row, btn);
  else if (btn.dataset.do) send(row, btn.dataset.do, btn.dataset.choice || null);
}});
</script>
"""
