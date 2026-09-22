"""Measured Claude spend, read from Claude Code's own transcripts.

Every assistant message records a `usage` block with real token counts. This
sums them per session and per model and prices them, so the cost sheets are
measurement rather than estimate.
"""

from __future__ import annotations

import datetime
import glob
import json
import os
from pathlib import Path

TRANSCRIPTS = Path.home() / ".claude" / "projects"

# $ per million tokens. Cache reads are 0.1x base input; cache writes are 1.25x
# for the 5-minute TTL and 2x for the 1-hour TTL, which is what Claude Code uses.
PRICES = {
    "claude-opus-5":      dict(inp=5.00,  out=25.00),
    "claude-opus-4-8":    dict(inp=5.00,  out=25.00),
    "claude-opus-4-7":    dict(inp=5.00,  out=25.00),
    "claude-sonnet-5":    dict(inp=2.00,  out=10.00),
    "claude-sonnet-4-6":  dict(inp=3.00,  out=15.00),
    "claude-haiku-4-5":   dict(inp=1.00,  out=5.00),
    "claude-fable-5-1":   dict(inp=10.00, out=50.00),
}
DEFAULT = dict(inp=5.00, out=25.00)


def price(model: str) -> dict:
    for k, v in PRICES.items():
        if model and model.startswith(k):
            return v
    return DEFAULT


def cost_of(u: dict, model: str) -> float:
    p = price(model)
    cc = u.get("cache_creation") or {}
    w1h = cc.get("ephemeral_1h_input_tokens", 0)
    w5m = cc.get("ephemeral_5m_input_tokens", 0)
    if not (w1h or w5m):                       # older records: bulk figure only
        w5m = u.get("cache_creation_input_tokens", 0)
    return (
        u.get("input_tokens", 0)            * p["inp"]        / 1e6
        + u.get("cache_read_input_tokens", 0) * p["inp"] * 0.1 / 1e6
        + w5m                                 * p["inp"] * 1.25 / 1e6
        + w1h                                 * p["inp"] * 2.0  / 1e6
        + u.get("output_tokens", 0)         * p["out"]        / 1e6
    )


def scan() -> dict:
    """Per-session totals plus the single most expensive turns seen."""
    sessions: dict[str, dict] = {}
    turns: list[dict] = []
    by_day: dict[str, float] = {}
    by_model: dict[str, dict] = {}

    for path in glob.glob(str(TRANSCRIPTS / "*" / "*.jsonl")):
        sid = os.path.basename(path)[:-6]
        s = sessions.setdefault(sid, dict(
            sid=sid, title="", cost=0.0, turns=0, out=0, cache_read=0,
            cache_write=0, models=set(), first="", last=""))
        try:
            fh = open(path, errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line and '"aiTitle"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") == "ai-title" and d.get("aiTitle"):
                    s["title"] = d["aiTitle"]
                    continue
                msg = d.get("message") or {}
                u = msg.get("usage")
                if not u:
                    continue
                model = msg.get("model") or "unknown"
                c = cost_of(u, model)
                s["cost"] += c
                s["turns"] += 1
                s["out"] += u.get("output_tokens", 0)
                s["cache_read"] += u.get("cache_read_input_tokens", 0)
                s["cache_write"] += u.get("cache_creation_input_tokens", 0)
                s["models"].add(model)
                ts = (d.get("timestamp") or "")[:10]
                if ts:
                    by_day[ts] = by_day.get(ts, 0) + c
                    s["last"] = max(s["last"], ts)
                    s["first"] = min(s["first"] or ts, ts)
                m = by_model.setdefault(model, dict(cost=0.0, turns=0))
                m["cost"] += c; m["turns"] += 1
                turns.append(dict(
                    cost=c, sid=sid, ts=d.get("timestamp", ""), model=model,
                    ctx=u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                        + u.get("input_tokens", 0),
                    out=u.get("output_tokens", 0)))

    for s in sessions.values():
        s["models"] = sorted(s["models"])
    turns.sort(key=lambda t: -t["cost"])
    return dict(
        sessions=sorted(sessions.values(), key=lambda s: -s["cost"]),
        turns=turns[:40],
        by_day=dict(sorted(by_day.items())),
        by_model=by_model,
        total=sum(s["cost"] for s in sessions.values()),
    )


def _money(x: float) -> str:
    return f"${x:,.2f}"


def sheets(out_dir: Path | None = None) -> Path:
    """Write the cost sheets. Facts only — RECOMMENDATIONS.md is hand-written."""
    r = scan()
    d = out_dir or (Path.home() / "Lattice" / "cost-sheets")
    d.mkdir(parents=True, exist_ok=True)
    today = datetime.date.today()

    # ---- SPEND.md -------------------------------------------------------
    L = [f"# Token consumption, valued at API rates", "",
         f"Generated {today:%Y-%m-%d} by `lattice cost --write`. Token counts are "
         f"read from the `usage` blocks Claude Code writes into every transcript "
         f"and priced at published per-model API rates.", "",
         f"> **This is not a bill.** If the user is on a flat-fee seat "
         f"(`billingType: stripe_subscription`, `seatTier: team_standard`) — a flat "
         f"monthly fee, not pay-per-token. The figures below are the *API-equivalent "
         f"value* of the tokens consumed. They are the right way to compare one "
         f"session against another and to see where usage goes, but they are not "
         f"money charged. Because extra usage is enabled on the account, the real "
         f"exposure is hitting the usage limit and incurring overage.", "",
         f"**Total token value: {_money(r['total'])}** across {len(r['sessions'])} sessions.", "",
         "## By model", "",
         "| Model | Token value | Turns | per turn |", "|:--|--:|--:|--:|"]
    for m, v in sorted(r["by_model"].items(), key=lambda x: -x[1]["cost"]):
        if v["cost"] < 0.01:
            continue
        L.append(f"| `{m}` | {_money(v['cost'])} | {v['turns']:,} | "
                 f"{_money(v['cost']/max(v['turns'],1))} |")

    L += ["", "## By session", "",
          "`ctx/turn` is the average context re-read on every single message — "
          "the number that actually drives the bill.", "",
          "| Session | Token value | Turns | ctx/turn | Output |",
          "|:--|--:|--:|--:|--:|"]
    for s in r["sessions"]:
        if s["cost"] < 1:
            continue
        per = (s["cache_read"] + s["cache_write"]) // max(s["turns"], 1)
        L.append(f"| {s['title'] or s['sid'][:8]} | {_money(s['cost'])} | "
                 f"{s['turns']:,} | {per:,} | {s['out']:,} |")

    L += ["", "## By day", "", "| Day | Token value |", "|:--|--:|"]
    for day, c in sorted(r["by_day"].items(), key=lambda x: x[0]):
        if c >= 1:
            L.append(f"| {day} | {_money(c)} |")
    (d / "SPEND.md").write_text("\n".join(L) + "\n")

    # ---- TOP-10-EXPENSIVE-EVENTS.md ------------------------------------
    byname = {s["sid"]: (s["title"] or s["sid"][:8]) for s in r["sessions"]}
    T = ["# The ten most expensive things that have happened", "",
         "> Valued at API rates for comparison. On a flat-fee seat this is not a real charge — "
         "these are not charges. See the note in `SPEND.md`.", "",
         f"Generated {today:%Y-%m-%d}. Two views: the costliest *sessions* "
         "(where the usage actually went) and the costliest *single turns* "
         "(what a bloated context does to one message).", "",
         "## Costliest sessions", "",
         "| # | Session | Token value | Why it cost that much |",
         "|--:|:--|--:|:--|"]
    for i, s in enumerate(r["sessions"][:10], 1):
        per = (s["cache_read"] + s["cache_write"]) // max(s["turns"], 1)
        why = (f"{s['turns']:,} turns, each re-reading ~{per:,} tokens of "
               f"accumulated context on {', '.join(m.split('-2025')[0] for m in s['models'])}")
        T.append(f"| {i} | {s['title'] or s['sid'][:8]} | {_money(s['cost'])} | {why} |")

    T += ["", "## Costliest single turns", "",
          "| # | Token value | Context read | Output | Session |",
          "|--:|--:|--:|--:|:--|"]
    for i, t in enumerate(r["turns"][:10], 1):
        T.append(f"| {i} | {_money(t['cost'])} | {t['ctx']:,} | {t['out']:,} | "
                 f"{byname.get(t['sid'], t['sid'][:8])} |")
    T += ["", "**Read the context column, not the output column.** Output is a "
          "few thousand tokens on even the most expensive turn. The cost is "
          "re-reading the conversation."]
    (d / "TOP-10-EXPENSIVE-EVENTS.md").write_text("\n".join(T) + "\n")
    return d
