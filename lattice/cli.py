"""Command-line front door.

This module only parses arguments and prints. Every decision lives in core.
That separation is what lets a phone client or an API call the same code later
without a rewrite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import core, router, secrets, storage

BOLD, DIM, RED, YEL, GRN, OFF = "\033[1m", "\033[2m", "\033[31m", "\033[33m", "\033[32m", "\033[0m"


def _c(text, colour):
    return f"{colour}{text}{OFF}" if sys.stdout.isatty() else str(text)


def _resolve(conn, ident):
    row = storage.get_project(conn, ident)
    if not row:
        print(_c(f"No project matching '{ident}'.", RED))
        print(f"{DIM}Try `lattice project list`.{OFF}")
        raise SystemExit(1)
    return row


# --- commands ---------------------------------------------------------------

def cmd_init(a):
    r = core.init()
    print(_c("Lattice initialised.", GRN))
    print(f"  Data home: {r['home']}")
    print(f"  Created {len(r['created'])} new file(s)/folder(s)." if r["created"]
          else "  Everything was already in place.")
    print(f"\n{DIM}Next: lattice project add <path>{OFF}")


def cmd_project_add(a):
    r = core.register_project(a.path, name=a.name, side=a.side,
                              scan=not a.no_scan, git_init=a.track)
    if "error" in r:
        print(_c(r["error"], RED)); raise SystemExit(1)

    print(_c(f"Registered '{r['name']}' (id {r['id']})", GRN))
    print(f"  Path  {r['path']}")
    print(f"  Side  {r['side']}   Kind  {r['kind'] or 'unknown'}")
    if r["description"]:
        print(f"  About {r['description']}")

    g = r["git"]
    if g.get("is_repo"):
        state = _c("dirty", YEL) if g["dirty"] else _c("clean", GRN)
        print(f"  Git   {g['branch']} · {g['commit_count']} commits · {state}")
    else:
        print(f"  Git   {_c('not tracked', YEL)}"
              + ("" if r["git_init"] else f"  {DIM}(add --track to start){OFF}"))
    if r["git_init"]:
        print(f"        {r['git_init']}")

    if r["findings"]:
        print()
        print(_c(f"  ⚠  {len(r['findings'])} possible credential(s) found:", RED))
        print(secrets.format_findings(r["findings"], Path(r["path"])))
        print(f"{DIM}  Values are never stored or printed. Rotate anything real, "
              f"then move it to 1Password.{OFF}")


def cmd_project_list(a):
    conn = core.open_db()
    rows = storage.list_projects(conn, a.side)
    if not rows:
        print("No projects registered yet.  Try: lattice project add <path>")
        conn.close(); return
    side = None
    for r in rows:
        if r["side"] != side:
            side = r["side"]
            print(f"\n{BOLD}{side.upper()}{OFF}")
        n = len(storage.list_tasks(conn, r["id"], "open"))
        tag = f"  {YEL}{n} open{OFF}" if n else ""
        print(f"  {r['id']:>3}  {r['name']:<28} {DIM}{r['kind'] or '':<16}{OFF}{tag}")
        print(f"       {DIM}{r['path']}{OFF}")
    conn.close()


def cmd_project_inspect(a):
    d = core.inspect_project(a.project, scan=a.scan)
    if "error" in d:
        print(_c(d["error"], RED)); raise SystemExit(1)
    p, g = d["project"], d["git"]
    print(f"{BOLD}{p['name']}{OFF}  (id {p['id']}, {p['side']})")
    print(f"  {p['path']}" + ("" if d["exists"] else _c("   ← MISSING ON DISK", RED)))
    if p["description"]:
        print(f"  {p['description']}")
    print(f"\n  {BOLD}Git{OFF}")
    if g.get("is_repo"):
        print(f"    branch {g['branch']} · {g['commit_count']} commits · "
              + (_c(f"{g['dirty_count']} uncommitted", YEL) if g["dirty"] else _c("clean", GRN)))
        print(f"    last: {g['last_commit']}")
        if g.get("remote"):
            print(f"    remote: {g['remote']}")
    else:
        print(f"    {_c('not a git repository', YEL)}")

    print(f"\n  {BOLD}Open tasks{OFF}")
    for t in d["tasks"]:
        print(f"    [{t['id']}] {t['title']}")
    if not d["tasks"]:
        print(f"    {DIM}none{OFF}")

    print(f"\n  {BOLD}Recent memory{OFF}")
    for m in d["memories"]:
        print(f"    {DIM}{m['created_at'][:10]}{OFF}  {m['content']}")
    if not d["memories"]:
        print(f"    {DIM}none{OFF}")

    if d["findings"] is not None:
        print(f"\n  {BOLD}Credential scan{OFF}")
        print(secrets.format_findings(d["findings"], Path(p["path"])))


def cmd_project_track(a):
    from . import execution
    conn = core.open_db(); row = _resolve(conn, a.project); conn.close()
    ok, msg = execution.init_git_repo(Path(row["path"]))
    print(_c(msg, GRN) if ok else _c(msg, YEL))


def cmd_memory_add(a):
    name = None
    if a.project:
        conn = core.open_db(); name = _resolve(conn, a.project)["name"]; conn.close()
    r = core.memory_add(name, a.note, mtype=a.type)
    print(_c(f"Remembered: {Path(r['path']).name}", GRN))
    print(f"{DIM}  {core.MEMORY_DIR} — loads into every session automatically{OFF}")


def cmd_memory_search(a):
    hits = core.memory_search(a.query)
    if not hits:
        print(f"Nothing matches '{a.query}'.")
    for h in hits:
        print(f"  {BOLD}{h['file']}{OFF}")
        if h["line"]:
            print(f"    {DIM}{h['line']}{OFF}")


def cmd_task_add(a):
    conn = core.open_db(); row = _resolve(conn, a.project)
    tid = storage.add_task(conn, row["id"], a.title); conn.close()
    print(_c(f"Task {tid} added to {row['name']}.", GRN))


def cmd_task_list(a):
    conn = core.open_db()
    pid = _resolve(conn, a.project)["id"] if a.project else None
    rows = storage.list_tasks(conn, pid, None if a.all else "open")
    if not rows:
        print("No tasks." if a.all else "No open tasks.")
    for t in rows:
        mark = _c("✓", GRN) if t["status"] == "done" else " "
        print(f"  {mark} [{t['id']:>3}] {BOLD}{t['project_name'] or '—'}{OFF}  {t['title']}")
    conn.close()


def cmd_task_done(a):
    conn = core.open_db()
    n = storage.complete_task(conn, a.task_id); conn.close()
    print(_c(f"Task {a.task_id} completed.", GRN) if n
          else _c(f"Task {a.task_id} not found, or already done.", YEL))


def cmd_pattern_add(a):
    conn = core.open_db(); row = _resolve(conn, a.project)
    pid = storage.add_pattern(conn, row["id"], a.pattern)
    seen = conn.execute("SELECT seen_count FROM patterns WHERE id=?", (pid,)).fetchone()["seen_count"]
    conn.close()
    print(_c(f"Pattern recorded (seen {seen}×).", GRN))
    if seen >= 2:
        print(f"{YEL}  Seen more than once — this has earned promotion to a skill.{OFF}")
        print(f"{DIM}  Start from {core.data_home()}/skills/skill_template.md{OFF}")


def cmd_pattern_list(a):
    conn = core.open_db()
    rows = storage.list_patterns(conn)
    if not rows:
        print("No patterns captured yet.")
    for p in rows:
        flag = _c(" ← promote", YEL) if p["seen_count"] >= 2 and not p["promoted_to_skill"] else ""
        print(f"  {p['seen_count']}×  {p['title']}  {DIM}({p['project_name'] or '—'}){OFF}{flag}")
    conn.close()


def cmd_status(a):
    s = core.status()
    print(f"{BOLD}Lattice{OFF}  {DIM}{s['home']}{OFF}\n")
    b = s["by_side"]
    print(f"  Projects   {s['projects']}   {DIM}({b['work']} work · {b['personal']} personal · {b['unknown']} unfiled){OFF}")
    print(f"  Open tasks {s['open_tasks']}")
    print(f"  Memories   {s['memories']}")
    print(f"  Patterns   {len(s['patterns'])}")
    print(f"  Skills     {len(s['skills'])}")
    print(f"  Roles      {', '.join(s['roles'])}")

    if s["untracked_projects"]:
        print(f"\n  {_c('Not under version control:', YEL)}")
        for n in s["untracked_projects"]:
            print(f"    {n}")
        print(f"{DIM}    lattice project track <name>{OFF}")

    ready = [p for p in s["patterns"] if p["seen_count"] >= 2 and not p["promoted_to_skill"]]
    if ready:
        print(f"\n  {_c('Patterns ready to become skills:', YEL)}")
        for p in ready:
            print(f"    {p['seen_count']}×  {p['title']}")

    if s["recent_memories"]:
        print(f"\n  {BOLD}Recent memory{OFF}")
        for m in s["recent_memories"]:
            print(f"    {DIM}{m['created_at'][:10]}{OFF}  {m['content'][:90]}")


def cmd_review(a):
    print(core.review(a.project))


def cmd_scan(a):
    root = Path(a.path).expanduser().resolve()
    f = secrets.scan(root)
    print(f"Scanned {root}")
    print(secrets.format_findings(f, root))
    if f:
        print(f"\n{DIM}Values are never read out or stored. Rotate anything real, "
              f"then keep the replacement in 1Password.{OFF}")
        raise SystemExit(2)



def cmd_cost(a):
    from . import cost
    if a.write:
        d = cost.sheets()
        print(_c(f"cost sheets written to {d}", GRN)); return
    r = cost.scan()
    print(f"Total measured spend: ${r['total']:,.2f} across {len(r['sessions'])} sessions")
    for s in r["sessions"][:10]:
        print(f"  ${s['cost']:8,.2f}  {s['turns']:5} turns  {s['title'] or s['sid'][:8]}")


def cmd_digest(a):
    from . import digest
    if a.open and not a.serve:
        import webbrowser
        from . import server
        if not server.reachable():
            svc = core.load_config().get("digest_service_id", "com.lattice.digestserver")
            print("digest server is not running — start it with:")
            print(f"  launchctl kickstart -k gui/$(id -u)/{svc}")
            return
        webbrowser.open(server.url())
        print("opened today's digest")
        return
    if a.serve:
        from . import server
        server.triage.migrate()
        print(f"digest server on {server.url()}")
        print(f"{DIM}ctrl-c to stop{OFF}")
        server.serve(open_browser=a.open)
        return
    text = digest.build()
    if a.write:
        path = digest.write(text)
        print(f"{DIM}{path}{OFF}")
    if not a.quiet:
        print(text)


def cmd_snapshot(a):
    """Resume state, for the PostCompact hook. JSON so the harness injects it."""
    import json as _json
    from . import snapshot
    text = snapshot.build()
    if a.plain:
        print(text)
        return
    print(_json.dumps({"hookSpecificOutput": {
        "hookEventName": "PostCompact", "additionalContext": text}}))


def cmd_daily(a):
    """The 05:00 refresh: everything slow, done before the user is awake."""
    from . import digest, gmail_source, memindex, sweep
    path = digest.write()
    print(f"{DIM}{path}{OFF}")
    try:
        snap = gmail_source.cached(max_age_seconds=0, force=True, full=True)
        print(f"mail: {len(snap['items'])} need you"
              + (f" (stale — {snap['error'][:60]})" if snap["stale"] else ""))
    except Exception as exc:
        print(f"mail: skipped — {exc}")
    try:
        res = sweep.run()
        print(f"sweep: {res['seen']} candidates, {res['kept']} kept")
    except Exception as exc:
        print(f"sweep: skipped — {exc}")
    memindex.build()
    from .core import open_db
    gap = open_db().execute(
        """SELECT t.id, p.name FROM tasks t JOIN projects p ON p.id = t.project_id
            WHERE t.status='open'
              AND NOT EXISTS (SELECT 1 FROM task_guidance g WHERE g.task_id = t.id)
            ORDER BY t.id""").fetchall()
    if gap:
        ids = ", ".join(f"{r['id']} ({r['name']})" for r in gap)
        print(f"{len(gap)} task(s) have no write-up: {ids}")
    print("digest, mail, sweep and memory index all refreshed")


def cmd_memory_reindex(a):
    from . import memindex
    changed = memindex.normalize()
    res = memindex.build()
    print(f"{res['facts']} facts across {res['areas']} areas")
    print(f"MEMORY.md {res['before_bytes']} -> {res['after_bytes']} bytes "
          f"(~{res['after_bytes']//4} tokens, loaded every session)")
    if changed:
        print(f"{DIM}frontmatter updated in {sum(changed.values())} file(s){OFF}")


def cmd_memory_supersede(a):
    from . import memindex
    print(memindex.supersede(a.old, a.new))
    memindex.build()


def cmd_context(a):
    print(core.context_pack(a.project, budget="full" if a.full else "short"))


def cmd_brief_write(a):
    from . import briefs
    names = a.projects or [None]
    if a.all:
        names = [r["name"] for r in briefs.survey()]
    for n in names:
        if n is None:
            print("Name a project, or use --all.")
            return
        r = briefs.write(n, remigrate=a.remigrate)
        if not r["ok"]:
            print(f"{RED}{r['error']}{OFF}")
            continue
        verb = "created" if r["created"] else "refreshed"
        warn = ""
        if r["tokens"] > briefs.BUDGET:
            warn = f" {RED}over budget ({r['tokens']}/{briefs.BUDGET}){OFF}"
        elif r["todos"]:
            warn = f" {DIM}{r['todos']} TODO(s) left to fill in{OFF}"
        print(f"{verb} {r['path']} — ~{r['tokens']} tok{warn}")


def cmd_brief_show(a):
    """Fetch one project's brief by name — what T0 promises as `lattice brief <name>`."""
    from . import briefs
    conn = core.open_db(); row = _resolve(conn, a.project); conn.close()
    if not Path(row["path"]).is_dir():
        print(_c(f"Project folder is missing: {row['path']}", RED)); raise SystemExit(1)

    brief = briefs.hand_written(row)
    if not brief:
        print(f"No brief yet. `lattice brief write {row['name']}` creates one.")
        print(f"  Path: {row['path']}")
        return
    print(brief)
    idx, n = briefs.memory_index(row["name"])
    print()
    if idx:
        print(f"Further facts: {n} indexed at `memory/_index/{row['name']}.md`")
    print(f"Path: {row['path']}")


def cmd_brief_list(a):
    from . import briefs
    rows = briefs.survey()
    have = [r for r in rows if r["exists"]]
    over = [r for r in have if r["tokens"] > briefs.BUDGET]
    todo = [r for r in have if r["todos"]]
    print(f"{len(have)}/{len(rows)} project(s) have a brief. "
          f"Budget {briefs.BUDGET} tok.")
    print()
    for r in sorted(rows, key=lambda r: (r["exists"], -r["tokens"])):
        if not r["exists"]:
            flag = f"{RED}no brief{OFF}"
            if r["folder_missing"]:
                flag = f"{RED}no brief, folder missing{OFF}"
            print(f"  {r['name']:<28} {flag}")
        else:
            bar = f"{r['tokens']:>5} tok"
            note = ""
            if r["tokens"] > briefs.BUDGET:
                note = f" {RED}over budget{OFF}"
            elif not r["marked"]:
                note = f" {DIM}unmarked — regenerate to add the split{OFF}"
            elif r["todos"]:
                note = f" {DIM}{r['todos']} TODO{OFF}"
            print(f"  {r['name']:<28} {bar}{note}")
    print()
    if over:
        print(f"{RED}Gate 2 fails:{OFF} {len(over)} brief(s) over "
              f"{briefs.BUDGET} tok — {', '.join(r['name'] for r in over)}")
    missing = len(rows) - len(have)
    if missing:
        print(f"{RED}Gate 2 fails:{OFF} {missing} project(s) with no brief")
    if todo:
        print(f"{DIM}{len(todo)} brief(s) still contain TODO placeholders{OFF}")
    if not over and not missing and not todo:
        print(f"{GRN}Gate 2 brief requirements pass.{OFF}")


def cmd_session_start(a):
    r = core.session_start()
    print(f"{DIM}session {r['session_id']} started{OFF}")


def cmd_session_end(a):
    r = core.session_end(a.summary)
    print(f"{DIM}session {r['session_id']} closed{OFF}" if r["session_id"]
          else f"{DIM}no open session{OFF}")


def cmd_decision_add(a):
    r = core.add_decision(a.project, a.decision, a.why)
    if "error" in r:
        print(_c(r["error"], RED)); raise SystemExit(1)
    print(_c(f"Decision {r['id']} recorded for {r['project']}.", GRN))


def cmd_decision_list(a):
    conn = core.open_db()
    pid = _resolve(conn, a.project)["id"] if a.project else None
    name = _resolve(conn, a.project)["name"] if a.project else None
    rows = storage.list_decisions(conn, pid)
    if not rows:
        print("No decisions recorded.")
    for d in rows:
        who = name or (d["project_name"] if "project_name" in d.keys() else "—")
        print(f"  {DIM}{d['created_at'][:10]}{OFF}  {BOLD}{who}{OFF}  {d['decision']}")
        if d["rationale"]:
            print(f"        {DIM}{d['rationale']}{OFF}")
    conn.close()


def cmd_skill_promote(a):
    r = core.force_promote(a.pattern, a.name) if a.force else core.promote_pattern(a.pattern, a.name)
    if "error" in r:
        print(_c(r["error"], YEL)); raise SystemExit(1)
    print(_c(f"Promoted to skill '{r['name']}' (pattern seen {r['seen']}x).", GRN))
    print(f"  {r['path']}")
    print(f"{DIM}  Fill in 'When to use it' — a skill nobody can trigger is a skill nobody uses.{OFF}")


def cmd_skill_list(a):
    conn = core.open_db()
    rows = storage.list_skills(conn)
    if not rows:
        print("No skills yet.  Promote a pattern once it has recurred:  lattice skill promote <pattern>")
    for s in rows:
        print(f"  {BOLD}{s['name']}{OFF}  {DIM}{s['status']}{OFF}")
        if s["description"]:
            print(f"    {s['description']}")
        if s["path"]:
            print(f"    {DIM}{s['path']}{OFF}")
    conn.close()


def cmd_skill_scan(a):
    r = core.scan_skills()
    print(_c(f"Registered {len(r['found'])} skill(s) from {core.SKILLS_DIR}", GRN))
    for n in r["found"]:
        print(f"  {n}")
    if r.get("removed"):
        print(_c(f"Dropped {len(r['removed'])} whose file no longer exists:", YEL))
        for n in r["removed"]:
            print(f"  {n}")


def cmd_idea_add(a):
    r = core.add_idea(a.title, trigger=a.trigger, side=a.side)
    if "error" in r:
        print(_c(r["error"], YEL)); raise SystemExit(1)
    print(_c(f"Idea captured: {r['title']}", GRN))
    print(f"  {r['path']}")


def cmd_idea_list(a):
    ideas = core.list_ideas()
    if not ideas:
        print("No ideas captured yet.")
    for i in ideas:
        print(f"  {BOLD}{i['title']}{OFF}  {DIM}({i['side']}){OFF}")
        print(f"    {DIM}{i['path']}{OFF}")



def cmd_export(a):
    r = core.export_all()
    total = sum(r["counts"].values())
    print(_c(f"Exported {total} row(s) to {r['dir']}", GRN))
    for k, v in r["counts"].items():
        if v:
            print(f"  {v:>4}  {k}")
    print(f"{DIM}  Commit and push to share with your other machines.{OFF}")


def cmd_import(a):
    r = core.import_all()
    if "error" in r:
        print(_c(r["error"], YEL)); raise SystemExit(1)
    total = sum(r["added"].values())
    done = sum(r.get("completed", {}).values())
    if not total and not done:
        print("Already up to date — nothing new to merge.")
        return
    if total:
        print(_c(f"Merged {total} new row(s).", GRN))
        for k, v in r["added"].items():
            if v:
                print(f"  {v:>4}  {k}")
    if done:
        print(_c(f"Marked {done} task(s) done from the other machine.", GRN))


# --- parser -----------------------------------------------------------------



def cmd_router(a):
    if a.always:
        print(router.always_text(), end="")
        return
    r = router.build(check_only=a.check)
    status = _c("OVER BUDGET", RED) if r["over_budget"] else _c("within budget", GRN)
    print(f"T0: {r['tokens']} tokens / {r['budget']} budget — {status}")
    print(f"  {r['projects']} projects · {r['pref_bullets']} preferences in {r['pref_domains']} domains")
    if r["over_budget"]:
        print(_c("  Refusing to write. Demote something to T1 rather than growing T0.", RED))
        raise SystemExit(1)
    if r["written"]:
        print(f"  Wrote {r['path']}")
    if a.show:
        print()
        print(r["text"])



def build_parser():
    p = argparse.ArgumentParser(
        prog="lattice",
        description="Personal AI development platform — one shared knowledge core.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the local data store").set_defaults(fn=cmd_init)
    sub.add_parser("status", help="platform overview").set_defaults(fn=cmd_status)

    a = sub.add_parser("router", help="regenerate T0, the always-loaded index")
    a.add_argument("--check", action="store_true", help="measure only, do not write")
    a.add_argument("--show", action="store_true", help="print the generated T0")
    a.add_argument("--always", action="store_true",
                   help="print the full text of every 'when: always' prefs domain — "
                        "for the SessionStart hook, not for humans")
    a.set_defaults(fn=cmd_router)

    pr = sub.add_parser("project", help="register and inspect projects")
    prs = pr.add_subparsers(dest="sub", required=True)

    a = prs.add_parser("add", help="register a project directory")
    a.add_argument("path")
    a.add_argument("--name")
    a.add_argument("--side", choices=["work", "personal", "unknown"])
    a.add_argument("--track", action="store_true", help="git init if not already tracked")
    a.add_argument("--no-scan", action="store_true", help="skip the credential scan")
    a.set_defaults(fn=cmd_project_add)

    a = prs.add_parser("list", help="list registered projects")
    a.add_argument("--side", choices=["work", "personal", "unknown"])
    a.set_defaults(fn=cmd_project_list)

    a = prs.add_parser("inspect", help="full detail on one project")
    a.add_argument("project")
    a.add_argument("--scan", action="store_true", help="also scan for credentials")
    a.set_defaults(fn=cmd_project_inspect)

    a = prs.add_parser("track", help="start git tracking a registered project")
    a.add_argument("project"); a.set_defaults(fn=cmd_project_track)

    m = sub.add_parser("memory", help="durable project memory")
    ms = m.add_subparsers(dest="sub", required=True)
    a = ms.add_parser("add"); a.add_argument("project", nargs="?"); a.add_argument("note")
    a.add_argument("--type", default="note"); a.set_defaults(fn=cmd_memory_add)
    a = ms.add_parser("search"); a.add_argument("query"); a.set_defaults(fn=cmd_memory_search)

    t = sub.add_parser("task", help="project tasks")
    ts = t.add_subparsers(dest="sub", required=True)
    a = ts.add_parser("add"); a.add_argument("project"); a.add_argument("title")
    a.set_defaults(fn=cmd_task_add)
    a = ts.add_parser("list"); a.add_argument("project", nargs="?")
    a.add_argument("--all", action="store_true"); a.set_defaults(fn=cmd_task_list)
    a = ts.add_parser("done"); a.add_argument("task_id", type=int); a.set_defaults(fn=cmd_task_done)

    pa = sub.add_parser("pattern", help="reusable patterns")
    pas = pa.add_subparsers(dest="sub", required=True)
    a = pas.add_parser("add"); a.add_argument("project"); a.add_argument("pattern")
    a.set_defaults(fn=cmd_pattern_add)
    pas.add_parser("list").set_defaults(fn=cmd_pattern_list)

    a = sub.add_parser("review", help="generate a review brief")
    a.add_argument("project"); a.set_defaults(fn=cmd_review)

    a = sub.add_parser("scan", help="scan any path for exposed credentials")
    a.add_argument("path"); a.set_defaults(fn=cmd_scan)

    co = sub.add_parser("cost", help="measured Claude spend from transcripts")
    co.add_argument("--write", action="store_true", help="regenerate cost-sheets/")
    co.set_defaults(fn=cmd_cost)

    dg = sub.add_parser("digest", help="morning priorities and open tickets")
    dg.add_argument("--write", action="store_true", help="save to lattice_data/digest/")
    dg.add_argument("--quiet", action="store_true", help="save only, print nothing")
    dg.add_argument("--serve", action="store_true",
                    help="serve the interactive page on 127.0.0.1 (Approve/Deny/Wait/Ignore)")
    dg.add_argument("--open", action="store_true", help="open the interactive digest in the browser")
    dg.set_defaults(fn=cmd_digest)

    mem_sub = [ap for ap in sub.choices if ap == "memory"]
    if mem_sub:
        msub = sub.choices["memory"]._subparsers._group_actions[0]
        ri = msub.add_parser("reindex",
                             help="rebuild MEMORY.md as a router and regenerate _index/")
        ri.set_defaults(fn=cmd_memory_reindex)
        sp = msub.add_parser("supersede",
                             help="mark one memory as replaced by another")
        sp.add_argument("old"); sp.add_argument("new")
        sp.set_defaults(fn=cmd_memory_supersede)

    sub.add_parser("daily", help="the 05:00 refresh: digest, mail, sweep, memory index"
                   ).set_defaults(fn=cmd_daily)

    sn = sub.add_parser("snapshot", help="resume state for the PostCompact hook")
    sn.add_argument("--plain", action="store_true", help="print the text, not hook JSON")
    sn.set_defaults(fn=cmd_snapshot)

    a = sub.add_parser("context", help="context pack for an AI session")
    a.add_argument("project", nargs="?")
    a.add_argument("--full", action="store_true", help="include recent memory across all projects")
    a.set_defaults(fn=cmd_context)

    b = sub.add_parser("brief", help="per-project briefs (CONTEXT.md)")
    bs = b.add_subparsers(dest="brief_cmd", required=True)
    a = bs.add_parser("write", help="create or refresh a project's CONTEXT.md")
    a.add_argument("projects", nargs="*")
    a.add_argument("--all", action="store_true", help="every registered project")
    a.add_argument("--remigrate", action="store_true",
                   help="re-split a brief that was cut in the wrong place")
    a.set_defaults(fn=cmd_brief_write)
    bs.add_parser("list", help="brief coverage and token budget").set_defaults(fn=cmd_brief_list)

    # `lattice brief <name>` — the fetch form T0 advertises. One subparser per
    # registered project, so argparse's own dispatch does the name lookup and
    # `lattice brief --help` doubles as the project list.
    try:
        conn = core.open_db()
        proj_names = [r["name"] for r in storage.list_projects(conn)]
        conn.close()
    except Exception:
        proj_names = []
    for pname in proj_names:
        if pname in ("write", "list"):
            continue  # reserved; a project with this name would need `brief write <name>`
        bs.add_parser(pname, help=f"show {pname}'s brief"
                      ).set_defaults(fn=cmd_brief_show, project=pname)

    # The name the existing CONTEXT.md footers already promise.
    a = sub.add_parser("context-doc", help="alias for `brief write`")
    a.add_argument("projects", nargs="*")
    a.add_argument("--all", action="store_true")
    a.add_argument("--remigrate", action="store_true")
    a.set_defaults(fn=cmd_brief_write)

    se = sub.add_parser("session", help="session bookkeeping (used by hooks)")
    ses = se.add_subparsers(dest="sub", required=True)
    ses.add_parser("start").set_defaults(fn=cmd_session_start)
    a = ses.add_parser("end"); a.add_argument("summary", nargs="?")
    a.set_defaults(fn=cmd_session_end)

    d = sub.add_parser("decision", help="durable decision records")
    ds = d.add_subparsers(dest="sub", required=True)
    a = ds.add_parser("add"); a.add_argument("project"); a.add_argument("decision")
    a.add_argument("--why"); a.set_defaults(fn=cmd_decision_add)
    a = ds.add_parser("list"); a.add_argument("project", nargs="?")
    a.set_defaults(fn=cmd_decision_list)

    sk = sub.add_parser("skill", help="reusable skills")
    sks = sk.add_subparsers(dest="sub", required=True)
    a = sks.add_parser("promote", help="turn a proven pattern into a skill")
    a.add_argument("pattern"); a.add_argument("--name")
    a.add_argument("--force", action="store_true", help="promote even if seen only once")
    a.set_defaults(fn=cmd_skill_promote)
    sks.add_parser("list").set_defaults(fn=cmd_skill_list)
    sks.add_parser("scan", help="register skills already in ~/.claude/skills/").set_defaults(fn=cmd_skill_scan)

    i = sub.add_parser("idea", help="future project ideas")
    isub = i.add_subparsers(dest="sub", required=True)
    a = isub.add_parser("add"); a.add_argument("title")
    a.add_argument("--trigger", help="what the user actually said")
    a.add_argument("--side", default="unknown", choices=["work", "personal", "unknown"])
    a.set_defaults(fn=cmd_idea_add)
    isub.add_parser("list").set_defaults(fn=cmd_idea_list)

    sub.add_parser("export", help="write knowledge to text for syncing").set_defaults(fn=cmd_export)
    sub.add_parser("import", help="merge synced text back in").set_defaults(fn=cmd_import)

    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
