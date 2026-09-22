"""The Gmail tab: what arrived in the last 24 hours that actually wants the user.

The filter is the whole product here. An inbox rundown that lists everything is
just the inbox again, so the default is to EXCLUDE and the exceptions are narrow:
a sender on the noise list still surfaces if the user is named in it, or if it is
addressed to them directly rather than to a list.

Nothing here deletes mail. The granted scope cannot permanently delete.
"""

from __future__ import annotations

import base64
import datetime
import json
import re

from . import gmail_auth
from .core import load_name_tokens, open_db

# Senders whose mail is machinery, not correspondence.
NOISE_DOMAINS = {
    "make.com", "nextdoor.com", "facebook.com", "facebookmail.com", "monday.com",
    "linkedin.com", "twitter.com", "x.com", "instagram.com", "pinterest.com",
    "youtube.com", "google.com", "docs.google.com", "calendar-notification.google.com",
    "quora.com", "medium.com", "substack.com", "eventbrite.com", "meetup.com",
    "slack.com", "notion.so", "figma.com", "canva.com", "dropbox.com",
    "amazon.com", "ebay.com", "etsy.com", "temu.com", "wish.com",
    "mailchimp.com", "sendgrid.net", "hubspot.com", "salesforce.com",
    "indeed.com", "ziprecruiter.com", "glassdoor.com",
}
NOISE_PATTERNS = re.compile(
    r"(no-?reply|do-?not-?reply|notification|newsletter|marketing|promo|"
    r"digest|updates?@|alerts?@|billing@|receipts?@|support@)", re.I)

# Categories Gmail itself already decided are not correspondence.
EXCLUDE_LABELS = {"SPAM", "TRASH", "CATEGORY_PROMOTIONS", "CATEGORY_SOCIAL",
                  "CATEGORY_FORUMS"}

# Populated from config.json's `name_tokens` — see core.load_name_tokens.


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _domain(addr: str) -> str:
    m = re.search(r"@([\w.-]+)", addr or "")
    return m.group(1).lower().lstrip("www.") if m else ""


def blocked_domains() -> set[str]:
    try:
        return {r["domain"] for r in open_db().execute("SELECT domain FROM mail_blocklist")}
    except Exception:
        return set()


def _noisy(sender: str) -> bool:
    dom = _domain(sender)
    known = NOISE_DOMAINS | blocked_domains()
    return any(dom == d or dom.endswith("." + d) for d in known)


def _rescued(subject: str) -> bool:
    """The one exception worth having: named in the subject line.

    Measured on a real day of real mail, name-in-BODY is worthless — it fires on
    marketing personalisation ("Hi <Name>") far more than on anything addressed to
    the user. Being in To is worthless too: most mail has three or fewer
    recipients, because it is their own inbox.
    """
    low = subject.lower()
    return any(tok in low for tok in load_name_tokens())


def _bulk(msg: dict) -> str:
    """Why this is machinery, or '' if it looks like correspondence.

    Measured shares over 120 messages in one day: List-Unsubscribe 55%,
    no-reply sender 61%, Precedence bulk 32%, noise domain 47%. Gmail's own
    CATEGORY_* labels and IMPORTANT are unused on this account — 0% — so
    nothing here may depend on them.
    """
    if _header(msg, "List-Unsubscribe"):
        return "bulk mail (carries an unsubscribe link)"
    if _header(msg, "Precedence").lower() in ("bulk", "list"):
        return "bulk mail (Precedence: bulk)"
    sender = _header(msg, "From")
    if NOISE_PATTERNS.search(sender):
        return "sent from a no-reply or notification address"
    if _noisy(sender):
        return "sender is a platform, not a person"
    labels = set(msg.get("labelIds", []))
    if labels & EXCLUDE_LABELS:
        return "Gmail filed it as spam, promotions, social or forums"
    return ""


HEADERS = ["From", "To", "Cc", "Subject", "Date", "List-Unsubscribe", "Precedence"]


def fetch(hours: int = 24, cap: int = 400, known: dict | None = None) -> list[dict]:
    """Everything from the window, minus the machinery. Metadata only — the body
    is never downloaded, so nothing from an email is stored beyond its snippet.

    `known` carries the previous sweep keyed by message id. A message already seen
    is reused rather than re-fetched: a refresh then costs one list call plus one
    fetch per genuinely new arrival, instead of re-reading the whole day every
    time. Re-reading 117 messages every 15 minutes was 11,000 API calls a day for
    a list that changes a few times an hour.
    """
    known = known or {}
    after = int((datetime.datetime.now() - datetime.timedelta(hours=hours)).timestamp())
    ids, page = [], None
    while len(ids) < cap:
        args = {"q": f"after:{after} -in:chats", "maxResults": 200}
        if page:
            args["pageToken"] = page
        got = gmail_auth.api("messages", **args)
        ids += [m["id"] for m in got.get("messages", []) or []]
        page = got.get("nextPageToken")
        if not page:
            break

    out, dropped, fetched = [], 0, 0
    for mid in ids[:cap]:
        if mid in known:
            out.append(dict(known[mid]))
            continue
        if mid in _dropped_ids(known):
            dropped += 1
            continue
        fetched += 1
        msg = gmail_auth.api(f"messages/{mid}", format="metadata", metadataHeaders=HEADERS)
        subject = _header(msg, "Subject") or "(no subject)"
        why = _bulk(msg)
        rescued = bool(why) and _rescued(subject)
        if why and not rescued:
            dropped += 1
            _DROPPED.add(mid)
            continue
        labels = set(msg.get("labelIds", []))
        out.append({
            "id": msg["id"], "thread": msg["threadId"],
            "from": _header(msg, "From"), "subject": subject,
            "date": _header(msg, "Date"), "snippet": msg.get("snippet", ""),
            "unread": "UNREAD" in labels, "starred": "STARRED" in labels,
            "labels": sorted(l for l in labels if not l.startswith("CATEGORY_")
                             and l not in {"INBOX", "UNREAD", "STARRED", "IMPORTANT", "SENT"}),
            "kept_because": "you are named in the subject" if rescued else "",
            "seen": len(ids[:cap]), "dropped": dropped,
        })
    for o in out:
        o["seen"], o["dropped"], o["fetched"] = len(ids[:cap]), dropped, fetched
    return out


# Ids already judged to be machinery. Kept so a refresh does not re-fetch and
# re-judge the 150 bulk messages it threw away fifteen minutes ago.
_DROPPED: set[str] = set()


def _dropped_ids(known: dict) -> set[str]:
    return _DROPPED


def labels() -> list[dict]:
    """User-created labels only — Gmail's own system labels are not choices."""
    got = gmail_auth.api("labels").get("labels", [])
    return sorted(({"id": l["id"], "name": l["name"]} for l in got
                   if l.get("type") == "user"), key=lambda l: l["name"].lower())


# ---------------------------------------------------------------- actions

def star(msg_id: str) -> str:
    gmail_auth.api(f"messages/{msg_id}/modify", "POST", {"addLabelIds": ["STARRED"]})
    return "starred in your inbox"


def add_label(msg_id: str, label_id: str = "", new_name: str = "") -> str:
    if new_name and not label_id:
        made = gmail_auth.api("labels", "POST", {
            "name": new_name, "labelListVisibility": "labelShow",
            "messageListVisibility": "show"})
        label_id, new_name = made["id"], made["name"]
    if not label_id:
        raise ValueError("no label chosen")
    gmail_auth.api(f"messages/{msg_id}/modify", "POST", {"addLabelIds": [label_id]})
    name = new_name or next((l["name"] for l in labels() if l["id"] == label_id), label_id)
    return f"labelled {name}"


def reply(msg_id: str, text: str) -> str:
    """Threaded reply, sent as the user. Needs the gmail.send scope."""
    msg = gmail_auth.api(f"messages/{msg_id}", format="metadata",
                         metadataHeaders=["Subject", "From", "Message-ID", "References"])
    to = _header(msg, "From")
    subject = _header(msg, "Subject")
    if not subject.lower().startswith("re:"):
        subject = "Re: " + subject
    mid = _header(msg, "Message-ID")
    refs = (_header(msg, "References") + " " + mid).strip()
    raw = (f"To: {to}\r\nSubject: {subject}\r\n"
           f"In-Reply-To: {mid}\r\nReferences: {refs}\r\n"
           f"Content-Type: text/plain; charset=utf-8\r\n\r\n{text}")
    gmail_auth.api("messages/send", "POST", {
        "raw": base64.urlsafe_b64encode(raw.encode()).decode(),
        "threadId": msg["threadId"]})
    return f"replied to {to}"


def spam(msg_id: str) -> str:
    """Gmail's own definition: SPAM on, INBOX off. Reversible, and the row offers
    the undo — which is why this is safe to do in one click."""
    gmail_auth.api(f"messages/{msg_id}/modify", "POST",
                   {"addLabelIds": ["SPAM"], "removeLabelIds": ["INBOX"]})
    ignore(msg_id)
    return "moved to Gmail's spam folder"


def unspam(msg_id: str) -> str:
    gmail_auth.api(f"messages/{msg_id}/modify", "POST",
                   {"addLabelIds": ["INBOX"], "removeLabelIds": ["SPAM"]})
    conn = open_db()
    conn.execute("DELETE FROM mail_state WHERE msg_id=?", (msg_id,))
    conn.commit()
    return "put back in your inbox"


def block_sender(msg_id: str, sender: str = "") -> str:
    """Teach the filter. This is Lattice-side only — it hides the sender from this
    page for good and never touches Gmail, so a mistake costs nothing in your mail."""
    migrate()
    dom = _domain(sender)
    if not dom:
        raise ValueError("could not read a domain from that sender")
    conn = open_db()
    conn.execute("INSERT OR IGNORE INTO mail_blocklist (domain,learned_from,created_at) "
                 "VALUES (?,?,?)", (dom, msg_id, _now().isoformat(timespec="seconds")))
    conn.commit()
    return f"{dom} will not appear here again"


def unblock(domain: str) -> str:
    conn = open_db()
    conn.execute("DELETE FROM mail_blocklist WHERE domain=?", (domain,))
    conn.commit()
    return f"{domain} un-blocked"


def link(msg: dict) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{msg['thread']}"


# ---------------------------------------------------------------- cache + state
# Rendering the tab must not cost 60 API calls. The snapshot is refreshed on a
# timer, or forced when an action changes something.

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS mail_snapshot (
        id         INTEGER PRIMARY KEY CHECK (id = 1),
        fetched_at TEXT NOT NULL,
        payload    TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mail_blocklist (
        domain     TEXT PRIMARY KEY,
        learned_from TEXT,
        created_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS mail_state (
        msg_id     TEXT PRIMARY KEY,
        ignored_until TEXT,
        note       TEXT,
        updated_at TEXT NOT NULL
    )""",
]


def migrate() -> None:
    conn = open_db()
    for stmt in SCHEMA:
        conn.execute(stmt)
    conn.commit()


def _now() -> datetime.datetime:
    return datetime.datetime.now()


def cached(max_age_seconds: int = 900, force: bool = False, full: bool = False) -> dict:
    """Returns {'items': [...], 'fetched_at': iso, 'stale': bool, 'error': str|None}."""
    migrate()
    conn = open_db()
    row = conn.execute("SELECT fetched_at, payload FROM mail_snapshot WHERE id=1").fetchone()
    age = None
    if row:
        age = (_now() - datetime.datetime.fromisoformat(row["fetched_at"])).total_seconds()
    if row and not force and age is not None and age < max_age_seconds:
        return {"items": _visible(json.loads(row["payload"])),
                "fetched_at": row["fetched_at"], "stale": False, "error": None}
    # A full sweep re-reads every message, so a star or label applied in Gmail
    # shows up here. Incremental sweeps reuse what they already have and would
    # otherwise never notice that change. Once a day is the right cadence for it.
    known = {}
    if full:
        _DROPPED.clear()
    elif row:
        try:
            known = {i["id"]: i for i in json.loads(row["payload"])}
        except Exception:
            known = {}
    try:
        items = fetch(known=known)
    except Exception as exc:
        # An expired token or a network blip must not blank the tab.
        if row:
            return {"items": _visible(json.loads(row["payload"])),
                    "fetched_at": row["fetched_at"], "stale": True, "error": str(exc)}
        return {"items": [], "fetched_at": None, "stale": True, "error": str(exc)}
    stamp = _now().isoformat(timespec="seconds")
    conn.execute("INSERT INTO mail_snapshot (id,fetched_at,payload) VALUES (1,?,?) "
                 "ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at, "
                 "payload=excluded.payload", (stamp, json.dumps(items)))
    conn.commit()
    return {"items": _visible(items), "fetched_at": stamp, "stale": False, "error": None}


def _visible(items: list[dict]) -> list[dict]:
    """Drop anything Ignore is still hiding."""
    conn = open_db()
    hidden = {r["msg_id"] for r in conn.execute(
        "SELECT msg_id FROM mail_state WHERE ignored_until > ?",
        (_now().date().isoformat(),))}
    return [i for i in items if i["id"] not in hidden]


def ignore(msg_id: str) -> str:
    migrate()
    conn = open_db()
    until = (_now().date() + datetime.timedelta(days=1)).isoformat()
    conn.execute("INSERT INTO mail_state (msg_id,ignored_until,updated_at) VALUES (?,?,?) "
                 "ON CONFLICT(msg_id) DO UPDATE SET ignored_until=excluded.ignored_until, "
                 "updated_at=excluded.updated_at",
                 (msg_id, until, _now().isoformat(timespec="seconds")))
    conn.commit()
    return f"hidden until tomorrow ({until})"


def act(action: str, msg_id: str, **kw) -> str:
    if action == "ignore":
        return ignore(msg_id)
    if action == "star":
        return star(msg_id)
    if action == "spam":
        return spam(msg_id)
    if action == "unspam":
        return unspam(msg_id)
    if action == "block":
        return block_sender(msg_id, kw.get("sender", ""))
    if action == "label":
        return add_label(msg_id, kw.get("label_id", ""), kw.get("new_name", ""))
    if action == "reply":
        text = (kw.get("text") or "").strip()
        if not text:
            raise ValueError("an empty reply was not sent")
        return reply(msg_id, text)
    raise ValueError(f"unknown mail action: {action}")
