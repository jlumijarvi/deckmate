"""The Slack key: workspace logo and name with an unread badge."""
from PIL import Image

from deckhid import KEY_PX
from keys import Key
from render import draw_centered_lines, fit_lines, font, tile
from slackstate import SNAP_ICON, SlackState
from system import launch


_slack_state = None


def slack_state():
    global _slack_state
    if _slack_state is None:
        _slack_state = SlackState()
    return _slack_state


class Slack(Key):
    """Workspace logo, name, and a badge with the number of conversations that
    have unread messages. Mentions and DMs are shown separately when non-zero."""
    dynamic = True
    _logo = None

    def workspace(self):
        state = slack_state()
        state.refresh()
        wss = state.workspaces()
        team = self.cfg.get("team")
        if team:
            for w in wss:
                if w["id"] == team:
                    return w
            return {"id": team, "name": team, "domain": ""}
        return wss[0] if wss else None

    @classmethod
    def logo(cls):
        if cls._logo is None:
            try:
                cls._logo = Image.open(SNAP_ICON).convert("RGBA").resize((26, 26))
            except OSError:
                cls._logo = False
        return cls._logo or None

    def render(self, pressed):
        img, d, fg = tile((74, 21, 75), pressed)   # Slack aubergine
        ws = self.workspace()
        logo = self.logo()
        if logo:
            img.paste(logo, (6, 6), logo)
        else:
            d.text((6, 6), "S", font=font(22), fill=fg)
        if ws is None:
            draw_centered_lines(d, ["Slack", "no data"], fg, size=12, top=36)
            return img
        counts = slack_state().unreads(ws["id"])
        unread, mentions = counts if counts else (None, None)
        # badge top-right
        text = "?" if unread is None else str(unread)
        badge = (225, 40, 40) if unread else (90, 90, 100)
        f = font(16 if len(text) < 3 else 12)
        w = d.textlength(text, font=f)
        r = 13
        cx, cy = KEY_PX - 6 - r, 6 + r
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=badge if not pressed else (0, 0, 0))
        box = f.getbbox(text)
        d.text((cx - w / 2, cy - (box[3] - box[1]) / 2 - box[1]), text, font=f, fill=(255, 255, 255))
        # the name is a caption under the badge, not a title: small and quiet
        lines, size = fit_lines(ws["name"], max_lines=1, sizes=(10, 9))
        name_fg = (210, 200, 215) if not pressed else fg
        if mentions:   # mentions line in the middle, the name on the bottom edge
            d.text((6, 35), f"@{mentions}", font=font(12), fill=(255, 200, 80) if not pressed else fg)
            draw_centered_lines(d, lines, name_fg, size=size, top=KEY_PX - size - 7, bold=False)
        else:
            draw_centered_lines(d, lines, name_fg, size=size, top=KEY_PX - size - 14, bold=False)
        return img

    def press(self):
        ws = self.workspace()
        if ws:
            launch(f"xdg-open 'slack://open?team={ws['id']}'")
