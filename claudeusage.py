"""Live plan usage from Anthropic's API: the same call Claude Code makes for its
own usage panel. Reads the login token Claude Code keeps in
~/.claude/.credentials.json and GETs /api/oauth/usage with it, every five
minutes in a background thread. The token is sent to api.anthropic.com and
nowhere else; nothing is written. The call reports percentages, it is not a
model request, so it uses no plan quota.

Run `python3 claudeusage.py` to print what the call returns (never the token).
"""
import json
import os
import threading
import time
import urllib.error
import urllib.request

import fakes

CREDENTIALS = os.path.expanduser("~/.claude/.credentials.json")
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
POLL_S = 5 * 60
RETRY_S = 60


def read_token(path=CREDENTIALS):
    """Claude Code's OAuth access token, or None when there is none or it has expired."""
    try:
        with open(path) as f:
            oauth = (json.load(f) or {}).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None
    token = oauth.get("accessToken")
    expires = oauth.get("expiresAt")
    if not token or (expires and expires / 1000.0 < time.time()):
        return None
    return token


def fetch_usage(path=CREDENTIALS, timeout=15):
    """The utilization dict (five_hour, seven_day, limits, ...) or None without a token.
    Raises urllib.error.URLError / HTTPError on a failed call."""
    if fakes.directory():
        canned = fakes.load_json("usage.json")
        if canned is not None:
            return fakes.resolve_times(canned)
    token = read_token(path)
    if not token:
        return None
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Accept": "application/json",
        "User-Agent": "deckmate",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class LiveUsage:
    """Polls the API in a background thread; snapshot() is what the board reads."""

    def __init__(self, path=CREDENTIALS, poll_s=POLL_S):
        self.path, self.poll_s = path, poll_s
        self._lock = threading.Lock()
        self._data, self._at = None, None
        self.error = ""
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            wait = self.poll_s
            try:
                data = fetch_usage(self.path)
                with self._lock:
                    if data is not None:
                        self._data, self._at = data, time.time()
                        self.error = ""
                    else:
                        self.error = "no Claude Code login"
            except urllib.error.HTTPError as e:
                self.error = f"usage API: HTTP {e.code}"
                wait = RETRY_S if e.code in (401, 429, 500, 502, 503) else self.poll_s
            except Exception as e:                   # network down, bad JSON
                self.error = f"usage API: {e}"
                wait = RETRY_S
            time.sleep(wait)

    def snapshot(self):
        with self._lock:
            return self._data, self._at


if __name__ == "__main__":
    data = fetch_usage()
    if data is None:
        print("no usable token in", CREDENTIALS)
    else:
        print("keys:", sorted(data.keys()))
        for k in ("five_hour", "seven_day"):
            print(k, data.get(k))
        for lim in data.get("limits") or []:
            scope = ((lim.get("scope") or {}).get("model") or {}).get("display_name") if lim.get("scope") else None
            print({k: lim.get(k) for k in ("kind", "percent", "resets_at", "is_active")}, "scope:", scope)
