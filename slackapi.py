"""Which Slack conversations are unread, from Slack's Web API. Optional: the
Slack key uses it when a user token is set on the key.

Slack's local state file gives only per-workspace totals, so naming the
unread channel needs the API. With a user token (xoxp-…, from a Slack app
of your own with the user scopes channels:read, groups:read, im:read,
mpim:read and users:read) a background thread lists the conversations the
user is a member of (users.conversations, refreshed every ten minutes) and
asks conversations.info for each, which carries unread_count_display and
last_read for the token's user. That is one request per conversation per
round, so a round runs only while the local state file says something is
unread, at most once a minute, over at most MAX_CONVERSATIONS conversations,
and backs off when Slack answers 429. The token is sent to slack.com and
nowhere else; it is never logged.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

log = logging.getLogger("deck")

API = "https://slack.com/api/"
POLL_S = 60
LIST_S = 600
MAX_CONVERSATIONS = 80
CALL_GAP_S = 0.15
SCOPES = "channels:read, groups:read, im:read, mpim:read, users:read"


@dataclass
class Unread:
    name: str          # "#general", "@anna", or the group's name
    count: int
    last_read: float   # the ts of the last read message: the smallest is the oldest unread


def call(token, method, timeout=15, **params):
    """One Web API call. Returns the JSON reply; raises on HTTP errors and on ok=false."""
    url = API + method + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "deckmate"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.load(r)
    if not data.get("ok"):
        raise RuntimeError(data.get("error") or "slack: not ok")
    return data


def conversation_name(conv, users=None):
    """How the key names a conversation: #channel, @person for a DM, the group's name."""
    if conv.get("is_im"):
        uid = conv.get("user") or ""
        return "@" + ((users or {}).get(uid) or uid)
    name = conv.get("name") or conv.get("id") or "?"
    if conv.get("is_mpim"):
        return name.replace("mpdm-", "").replace("--", ", ").rsplit("-", 1)[0] or name
    return "#" + name


def parse_conversations(reply):
    """[conversation dicts] from users.conversations, archived ones left out."""
    return [c for c in (reply.get("channels") or []) if not c.get("is_archived")]


def unread_from_info(reply, conv, users=None):
    """An Unread from conversations.info, or None when nothing is unread there."""
    ch = reply.get("channel") or {}
    count = int(ch.get("unread_count_display") or 0)
    if count <= 0:
        return None
    try:
        last_read = float(ch.get("last_read") or 0)
    except ValueError:
        last_read = 0.0
    return Unread(conversation_name({**conv, **ch}, users), count, last_read)


def caption(unreads):
    """What the key's bottom line says: the one unread conversation, or the one
    with the oldest unread message plus how many others."""
    if not unreads:
        return ""
    oldest = min(unreads, key=lambda u: u.last_read)
    more = len(unreads) - 1
    return oldest.name + (f" +{more}" if more else "")


class SlackApi:
    """Polls the API in a background thread; unreads() is what the key reads."""

    def __init__(self, token, start=True):
        self.token = token
        self.wanted = True            # the key sets this: False while nothing is unread locally
        self.error = ""
        self._lock = threading.Lock()
        self._unreads = []
        self._convs, self._convs_at = [], 0.0
        self._users = {}
        self._poll_s = POLL_S
        if start:
            threading.Thread(target=self._loop, daemon=True).start()

    def unreads(self):
        with self._lock:
            return list(self._unreads)

    def _conversations(self):
        now = time.monotonic()
        if now - self._convs_at < LIST_S and self._convs:
            return self._convs
        convs, cursor = [], ""
        while True:
            reply = call(self.token, "users.conversations", types="public_channel,private_channel,mpim,im",
                         exclude_archived="true", limit=200, **({"cursor": cursor} if cursor else {}))
            convs += parse_conversations(reply)
            cursor = (reply.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor or len(convs) >= MAX_CONVERSATIONS:
                break
        self._convs, self._convs_at = convs[:MAX_CONVERSATIONS], now
        return self._convs

    def _user_name(self, uid):
        if uid and uid not in self._users:
            try:
                u = call(self.token, "users.info", user=uid).get("user") or {}
                profile = u.get("profile") or {}
                self._users[uid] = profile.get("display_name") or u.get("real_name") or u.get("name") or uid
            except Exception:
                self._users[uid] = uid
        return self._users.get(uid, uid)

    def _round(self):
        unreads = []
        for conv in self._conversations():
            reply = call(self.token, "conversations.info", channel=conv["id"])
            if conv.get("is_im"):
                self._user_name(conv.get("user"))
            u = unread_from_info(reply, conv, self._users)
            if u is not None:
                unreads.append(u)
            time.sleep(CALL_GAP_S)
        return unreads

    def _loop(self):
        while True:
            wait = self._poll_s
            if self.wanted:
                try:
                    unreads = self._round()
                    with self._lock:
                        self._unreads = unreads
                    self.error = ""
                    self._poll_s = POLL_S
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        retry = int(e.headers.get("Retry-After") or 30)
                        self._poll_s = min(600, max(self._poll_s * 2, retry))
                        self.error = "slack api: rate limited"
                    else:
                        self.error = f"slack api: HTTP {e.code}"
                    wait = self._poll_s
                    log.info(self.error)
                except Exception as e:                   # network, bad token (invalid_auth), bad JSON
                    self.error = f"slack api: {e}"
                    log.info(self.error)
                    wait = max(self._poll_s, 120)
            else:
                with self._lock:
                    self._unreads = []
                wait = 5
            time.sleep(wait)


_apis = {}


def slack_api(token):
    """The one poller per token, shared by every key that uses it."""
    if token not in _apis:
        _apis[token] = SlackApi(token)
    return _apis[token]
