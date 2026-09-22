"""Gmail OAuth for Lattice, with the credential in the macOS Keychain.

Lattice has no Google access of its own otherwise: the connector in a Claude
session belongs to that session, and no session is running at 10:00.

What is actually secret here is the REFRESH TOKEN — a string that grants standing
access to read and send mail as the user until it is revoked. It goes in the Keychain,
encrypted at rest, never in a file and never in the repo. Google explicitly does
not treat an installed-app client secret as confidential, but it lives in the
Keychain too rather than lying around in ~/Downloads.

Secrets are written to `security` over stdin, never as an argv value, so they
never appear in the process list.
"""

from __future__ import annotations

import base64
import hashlib
import json
import http.server
import secrets
import socket
import subprocess
import threading
import urllib.parse
import urllib.request

SERVICE_CLIENT = "lattice-gmail-client"
SERVICE_REFRESH = "lattice-gmail-refresh"
ACCOUNT = "lattice"
AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify",
          "https://www.googleapis.com/auth/gmail.send"]


# ------------------------------------------------------------------ keychain

# `security` reads a piped secret through a 128-byte prompt buffer and silently
# truncates anything longer — which corrupts an OAuth client blob or a long
# refresh token with no error at all. Secrets are therefore stored in chunks
# under this size. Passing the value as -w instead would avoid chunking but put
# the secret in argv, where any local process can read it out of `ps`.
CHUNK = 100


def _raw_set(service: str, value: str) -> None:
    proc = subprocess.run(
        ["security", "add-generic-password", "-U", "-a", ACCOUNT, "-s", service, "-w"],
        input=f"{value}\n{value}\n", text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"keychain write failed: {proc.stderr.strip()}")


def _raw_get(service: str) -> str | None:
    proc = subprocess.run(
        ["security", "find-generic-password", "-a", ACCOUNT, "-s", service, "-w"],
        text=True, capture_output=True)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _raw_del(service: str) -> None:
    subprocess.run(["security", "delete-generic-password", "-a", ACCOUNT, "-s", service],
                   capture_output=True)


def keychain_set(service: str, value: str) -> None:
    """Chunked, then read back and verified. A silent truncation must never be
    mistaken for a successful write."""
    parts = [value[i:i + CHUNK] for i in range(0, len(value), CHUNK)] or [""]
    for i, part in enumerate(parts):
        _raw_set(f"{service}.{i}", part)
    _raw_set(f"{service}.n", str(len(parts)))
    if keychain_get(service) != value:
        raise RuntimeError("keychain stored a value that did not read back intact")


def keychain_get(service: str) -> str | None:
    n = _raw_get(f"{service}.n")
    if not n:
        return None
    out = []
    for i in range(int(n)):
        part = _raw_get(f"{service}.{i}")
        if part is None:
            return None
        out.append(part)
    return "".join(out)


def keychain_delete(service: str) -> None:
    n = _raw_get(f"{service}.n")
    for i in range(int(n or 0)):
        _raw_del(f"{service}.{i}")
    _raw_del(f"{service}.n")


def have_credentials() -> bool:
    return bool(keychain_get(SERVICE_CLIENT) and keychain_get(SERVICE_REFRESH))


def import_client(path: str) -> dict:
    """Take Google's downloaded JSON into the Keychain. The caller deletes the file."""
    with open(path) as fh:
        blob = json.load(fh)
    cfg = blob.get("installed") or blob.get("web")
    if not cfg:
        raise ValueError("this is not an installed/desktop OAuth client file")
    payload = json.dumps({"client_id": cfg["client_id"],
                          "client_secret": cfg["client_secret"]})
    keychain_set(SERVICE_CLIENT, payload)          # raises if it did not round-trip
    back = json.loads(keychain_get(SERVICE_CLIENT))
    if back["client_secret"] != cfg["client_secret"]:
        raise RuntimeError("stored client secret does not match the file — not safe to delete it")
    return {"project_id": cfg.get("project_id"), "stored_ok": True}


def _client() -> dict:
    raw = keychain_get(SERVICE_CLIENT)
    if not raw:
        raise RuntimeError("no Gmail client in the Keychain — run `lattice gmail setup` first")
    return json.loads(raw)


# ------------------------------------------------------------------ oauth

def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def authorize(open_browser: bool = True) -> str:
    """One-time consent. Returns the account the token belongs to."""
    cfg = _client()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)

    with socket.socket() as s:                     # any free loopback port is
        s.bind(("127.0.0.1", 0))                   # allowed for a desktop client
        port = s.getsockname()[1]
    redirect = f"http://127.0.0.1:{port}/"
    result: dict = {}
    done = threading.Event()

    class Catch(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            result.update({k: v[0] for k, v in q.items()})
            ok = result.get("state") == state and "code" in result
            msg = ("Lattice is connected to Gmail. You can close this tab."
                   if ok else "Authorisation failed. Nothing was stored.")
            page = f"<!doctype html><meta charset=utf-8><title>Lattice</title>" \
                   f"<body style='font:16px -apple-system;padding:60px'>{msg}</body>"
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(page.encode())
            done.set()

    server = http.server.HTTPServer(("127.0.0.1", port), Catch)
    threading.Thread(target=server.handle_request, daemon=True).start()

    url = AUTH_URI + "?" + urllib.parse.urlencode({
        "client_id": cfg["client_id"], "redirect_uri": redirect,
        "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    else:
        print(url)

    if not done.wait(timeout=300):
        raise TimeoutError("no response from the consent screen within 5 minutes")
    server.server_close()
    if result.get("state") != state:
        raise RuntimeError("state mismatch — refusing this response")
    if "code" not in result:
        raise RuntimeError(f"consent failed: {result.get('error', 'no code returned')}")

    tok = _post(TOKEN_URI, {
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "code": result["code"], "code_verifier": verifier,
        "grant_type": "authorization_code", "redirect_uri": redirect})
    if "refresh_token" not in tok:
        raise RuntimeError("Google returned no refresh token — revoke the app's access "
                           "at myaccount.google.com/permissions and try again")
    keychain_set(SERVICE_REFRESH, tok["refresh_token"])
    return profile(tok["access_token"]).get("emailAddress", "unknown")


_cache: dict = {}


def access_token() -> str:
    """Short-lived token, refreshed on demand and held only in memory."""
    import time
    if _cache.get("exp", 0) > time.time() + 60:
        return _cache["tok"]
    cfg, refresh = _client(), keychain_get(SERVICE_REFRESH)
    if not refresh:
        raise RuntimeError("Gmail is not authorised yet — run `lattice gmail auth`")
    tok = _post(TOKEN_URI, {
        "client_id": cfg["client_id"], "client_secret": cfg["client_secret"],
        "refresh_token": refresh, "grant_type": "refresh_token"})
    _cache.update({"tok": tok["access_token"], "exp": time.time() + tok.get("expires_in", 3600)})
    return _cache["tok"]


def api(path: str, method: str = "GET", body: dict | None = None, **params) -> dict:
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    if params:
        # doseq: a list must become repeated params (metadataHeaders=From&
        # metadataHeaders=To), not the repr of a Python list.
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {access_token()}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def profile(token: str | None = None) -> dict:
    tok = token or access_token()
    req = urllib.request.Request("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                                 headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())
