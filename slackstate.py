"""Read Slack's local state for the unread badge. Read-only.

Slack keeps a JSON state file per install:
  snap:     ~/snap/slack/current/.config/Slack/storage/root-state.json
  deb/rpm:  ~/.config/Slack/storage/root-state.json
  flatpak:  ~/.var/app/com.slack.Slack/config/Slack/storage/root-state.json

`workspaces.<teamId>` has name, domain and icon URLs; `webapp.teams.<teamId>.unreads`
has `unreads` (conversations with unread messages), `unreadHighlights` (mentions
and DMs) and `showBullet`. The file only holds per-workspace totals.
"""
import glob
import json
import os

SNAP_ICON = "/snap/slack/current/usr/share/pixmaps/slack.png"


def state_path():
    """The newest root-state.json among the known install locations, or None."""
    candidates = [
        os.path.expanduser("~/snap/slack/current/.config/Slack/storage/root-state.json"),
        os.path.expanduser("~/.config/Slack/storage/root-state.json"),
        os.path.expanduser("~/.var/app/com.slack.Slack/config/Slack/storage/root-state.json"),
    ]
    # the snap's "current" link can dangle after an update; look at every revision too
    candidates += glob.glob(os.path.expanduser("~/snap/slack/[0-9]*/.config/Slack/storage/root-state.json"))
    existing = [p for p in candidates if os.path.isfile(p)]
    if not existing:
        return None
    return max(existing, key=os.path.getmtime)


class SlackState:
    """Parses the state file again only when it changed; keeps the last good
    reading while Slack is in the middle of rewriting it."""

    def __init__(self):
        self.path = None
        self._key = None
        self.data = {}

    def refresh(self):
        path = state_path()
        if path is None:
            self.path, self.data = None, {}
            return self.data
        try:
            st = os.stat(path)
            key = (path, st.st_size, st.st_mtime_ns)
            if key == self._key:
                return self.data
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return self.data  # half-written: keep what we had
        self.path, self._key, self.data = path, key, data
        return self.data

    def workspaces(self):
        """[{id, name, domain, icon_url}] in Slack's sidebar order."""
        out = []
        for tid, w in (self.data.get("workspaces") or {}).items():
            icon = w.get("icon") or {}
            out.append({
                "id": tid,
                "name": w.get("name") or w.get("domain") or tid,
                "domain": w.get("domain", ""),
                "icon_url": icon.get("image_230") or icon.get("image_88") or icon.get("image_68"),
                "order": w.get("order", 0),
            })
        out.sort(key=lambda w: w["order"])
        return out

    def unreads(self, team_id):
        """(conversations with unreads, mentions) for a workspace, or None if unknown."""
        t = ((self.data.get("webapp") or {}).get("teams") or {}).get(team_id)
        if not t or "unreads" not in t:
            return None
        u = t["unreads"] or {}
        return int(u.get("unreads", 0) or 0), int(u.get("unreadHighlights", 0) or 0)


if __name__ == "__main__":
    s = SlackState()
    s.refresh()
    print("state file:", s.path or "none found")
    for w in s.workspaces():
        print(f"  {w['id']}  {w['name']:20s} {w['domain']:15s} unreads={s.unreads(w['id'])}")
