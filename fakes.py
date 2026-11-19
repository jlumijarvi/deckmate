"""Canned data for the sources that need a network or an account: Slack's
state file and Web API, the Claude usage API and the calendar feed. With
DECKMATE_FAKES set to a directory (dev.sh --fakes points it at
tests/fixtures), each source answers from a file there instead of calling
out, so every key can be tried on the deck without a token or an address.
Unset, nothing here is used.

Files, all optional:
  slack-state.json   a root-state.json: workspaces and per-workspace unreads
  slack-api.json     {"users.conversations": reply, "conversations.info": {id: reply},
                      "users.info": {id: reply}}
  usage.json         the usage API's utilization dict; a resets_at of "+3m" means
                      three minutes after the board started (a sample within two
                      minutes of a window's end does not count as a reset), so a window reset can be
                      watched happening
  calendar.ics       an iCalendar feed; {today} is today's date as 20261119 and
                      {now+15m} a time 15 minutes from the start as 20261119T101500,
                      so the meetings are always today's
"""
import json
import os
import re
import time
from datetime import datetime, timedelta

ENV = "DECKMATE_FAKES"
_started = time.time()


def directory():
    """The fixtures directory, or None when fakes are off."""
    d = os.environ.get(ENV)
    return d if d and os.path.isdir(d) else None


def path(name):
    d = directory()
    if d is None:
        return None
    p = os.path.join(d, name)
    return p if os.path.isfile(p) else None


def load_json(name):
    p = path(name)
    if p is None:
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)


_REL = re.compile(r"^\+(\d+)([smh])$")
_UNIT = {"s": 1, "m": 60, "h": 3600}


def relative_seconds(value):
    """'+90s', '+15m', '+2h' -> seconds, else None."""
    m = _REL.match(str(value).strip())
    return int(m.group(1)) * _UNIT[m.group(2)] if m else None


def resolve_times(obj, base=None):
    """Replace '+Ns'-style strings anywhere in a JSON value with ISO times from the start."""
    base = base or _started
    if isinstance(obj, dict):
        return {k: resolve_times(v, base) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_times(v, base) for v in obj]
    if isinstance(obj, str):
        secs = relative_seconds(obj)
        if secs is not None:
            return datetime.fromtimestamp(base + secs).astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
    return obj


_TEMPLATE = re.compile(r"\{(today|now(?:([+-])(\d+)([smh]))?)\}")


def render_times(text, base=None):
    """Fill {today} and {now+15m} in a text (the calendar fixture)."""
    start = datetime.fromtimestamp(base or _started)

    def sub(m):
        if m.group(1) == "today":
            return start.strftime("%Y%m%d")
        t = start
        if m.group(2):
            delta = timedelta(seconds=int(m.group(3)) * _UNIT[m.group(4)])
            t = start + delta if m.group(2) == "+" else start - delta
        return t.strftime("%Y%m%dT%H%M%S")
    return _TEMPLATE.sub(sub, text)


def text(name):
    p = path(name)
    if p is None:
        return None
    with open(p, encoding="utf-8") as f:
        return render_times(f.read())


def slack_api(method, params):
    """A canned Slack reply for a method, or None when the fixture has none."""
    data = load_json("slack-api.json")
    if not data or method not in data:
        return None
    reply = data[method]
    if method in ("conversations.info", "users.info"):
        key = params.get("channel") or params.get("user")
        reply = reply.get(key) if isinstance(reply, dict) else None
    return reply
