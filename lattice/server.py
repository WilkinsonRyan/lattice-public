"""The local server behind the digest's buttons.

A file:// page cannot write to the database, so the digest is served from
127.0.0.1 instead. That opens a door, so it is nailed shut deliberately:

  * loopback bind only — never reachable from the network
  * Host header allowlisted, which defeats DNS rebinding (a rebound page would
    otherwise be same-origin with us and could simply read the token out of the HTML)
  * a shared token, 0600 on disk, required on every mutating request, which
    defeats blind cross-origin POSTs from any tab that happens to be open
  * Origin checked on POST
  * no path, command or file ever comes from the request. The client sends a task
    id and a decision word; everything else is looked up in the database.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from . import ask, core, gmail_source, page, sweep, triage
from .core import data_home

HOST, PORT = "127.0.0.1", 8787
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
DECISIONS = {"approve", "deny", "wait", "ignore", "never"}


def token() -> str:
    """Stable across restarts so a bookmarked URL keeps working."""
    p = data_home() / ".digest-token"
    if not p.exists():
        p.write_text(secrets.token_urlsafe(32))
        p.chmod(0o600)
    return p.read_text().strip()


class Handler(BaseHTTPRequestHandler):
    server_version = "LatticeDigest"

    def log_message(self, fmt, *args):
        pass  # the access log is noise; real events go to task_events

    def handle_one_request(self):
        # Every request runs on its own thread; hand its database connections
        # back rather than letting them pile up for the life of the process.
        try:
            super().handle_one_request()
        finally:
            core.close_db()

    # ---- guards -------------------------------------------------------
    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _token_ok(self, supplied: str) -> bool:
        return hmac.compare_digest(supplied or "", token())

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc in ALLOWED_HOSTS

    def _deny(self, code: int, msg: str):
        body = json.dumps({"ok": False, "detail": msg}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- routes -------------------------------------------------------
    def do_GET(self):
        if not self._host_ok():
            return self._deny(421, "bad host")
        url = urlparse(self.path)
        q = parse_qs(url.query)

        if url.path == "/":
            if not self._token_ok((q.get("t") or [""])[0]):
                return self._deny(403, "missing or bad token")
            html = page.render(token()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
            return

        if url.path == "/api/sweep_info":
            if not self._token_ok((q.get("t") or [""])[0]):
                return self._deny(403, "missing or bad token")
            iid = (q.get("item") or [""])[0]
            if not iid or len(iid) > 128:
                return self._deny(400, "bad item id")
            try:
                return self._json(sweep.info(iid))
            except Exception as exc:
                return self._json({"error": str(exc)}, 500)

        if url.path == "/api/labels":
            if not self._token_ok((q.get("t") or [""])[0]):
                return self._deny(403, "missing or bad token")
            try:
                return self._json({"labels": gmail_source.labels()})
            except Exception as exc:
                return self._json({"labels": [], "error": str(exc)})

        if url.path == "/api/info":
            if not self._token_ok((q.get("t") or [""])[0]):
                return self._deny(403, "missing or bad token")
            try:
                tid = int((q.get("task") or ["x"])[0])
            except ValueError:
                return self._deny(400, "bad task id")
            return self._json(page.info_for(tid))

        self._deny(404, "no such path")

    def do_POST(self):
        if not self._host_ok():
            return self._deny(421, "bad host")
        if not self._origin_ok():
            return self._deny(403, "cross-origin request refused")
        route = urlparse(self.path).path
        if route not in ("/api/resolve", "/api/mail", "/api/sweep", "/api/ask"):
            return self._deny(404, "no such path")

        length = int(self.headers.get("Content-Length") or 0)
        if length > 4096:
            return self._deny(413, "too large")
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return self._deny(400, "bad json")

        if not self._token_ok(payload.get("token")):
            return self._deny(403, "missing or bad token")

        if route == "/api/ask":
            try:
                tid = int(payload.get("task"))
            except (TypeError, ValueError):
                return self._deny(400, "bad task id")
            try:
                return self._json(ask.ask(tid, payload.get("question", "")))
            except Exception as exc:
                return self._json({"ok": False, "detail": str(exc)}, 500)

        if route == "/api/sweep":
            action = payload.get("action")
            if action not in ("adopt", "queue", "dismiss"):
                return self._deny(400, "unknown sweep action")
            iid = payload.get("id")
            if not isinstance(iid, str) or not iid or len(iid) > 128:
                return self._deny(400, "bad item id")
            try:
                fn = {"adopt": sweep.adopt, "queue": sweep.queue_item,
                      "dismiss": sweep.dismiss}[action]
                return self._json({"ok": True, "detail": fn(iid)})
            except Exception as exc:
                return self._json({"ok": False, "detail": str(exc)}, 500)

        if route == "/api/mail":
            action = payload.get("action")
            if action not in ("star", "label", "reply", "ignore",
                              "spam", "unspam", "block"):
                return self._deny(400, "unknown mail action")
            mid = payload.get("id")
            if not isinstance(mid, str) or not mid or len(mid) > 64:
                return self._deny(400, "bad message id")
            try:
                detail = gmail_source.act(
                    action, mid,
                    text=payload.get("text"), label_id=payload.get("label_id", ""),
                    new_name=payload.get("new_name", ""),
                    sender=payload.get("sender", ""))
                return self._json({"ok": True, "detail": detail})
            except Exception as exc:
                return self._json({"ok": False, "detail": str(exc)}, 500)

        decision = payload.get("decision")
        if decision not in DECISIONS:
            return self._deny(400, "unknown decision")
        try:
            tid = int(payload.get("task"))
        except (TypeError, ValueError):
            return self._deny(400, "bad task id")

        choice = payload.get("choice")
        if choice is not None and (not isinstance(choice, str) or len(choice) > 64):
            return self._deny(400, "bad choice")

        try:
            return self._json(triage.resolve(tid, decision, choice))
        except Exception as exc:
            return self._json({"ok": False, "state": "failed", "detail": str(exc)}, 500)


def url() -> str:
    return f"http://{HOST}:{PORT}/?t={token()}"


def reachable(timeout: float = 0.4) -> bool:
    import socket
    with socket.socket() as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((HOST, PORT)) == 0


def _mail_refresher(every: int = 900) -> None:
    """A full 24h sweep takes ~30s, so it never happens while a page waits."""
    import time
    while True:
        try:
            # Incremental: one list call plus the genuinely new arrivals.
            gmail_source.cached(max_age_seconds=every - 60, force=False)
        except Exception:
            pass          # a missing credential or a blip must not kill the server
        try:
            sweep.run_if_stale()
        except Exception:
            pass
        time.sleep(every)


def serve(open_browser: bool = False) -> None:
    triage.migrate()
    gmail_source.migrate()
    threading.Thread(target=_mail_refresher, daemon=True).start()
    httpd = ThreadingHTTPServer((HOST, PORT), Handler)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url())).start()
    httpd.serve_forever()
