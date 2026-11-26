import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from settings import key_kind, key_summary, set_key  # noqa: E402
except (ImportError, ValueError):   # no GTK on this machine
    set_key = None
import locale  # noqa: E402

locale.setlocale(locale.LC_ALL, "C")   # importing GTK switched to the system locale; other tests format dates


@unittest.skipIf(set_key is None, "GTK not available")
class SetKeyTest(unittest.TestCase):
    def test_folder_keeps_its_keys_on_resave(self):
        cfg = {"keys": {}, "sessions": {"keys": [5]}}
        set_key(cfg, 3, "folder", label="Work", color="#123456")
        self.assertEqual(cfg["keys"]["3"], {"type": "folder", "title": "Work", "color": "#123456", "keys": {}})
        inside = cfg["keys"]["3"]
        set_key(inside, 1, "command", label="Jira", color="#000000", run="xdg-open x")
        set_key(inside, 2, "vpn", connection="Office")
        self.assertEqual(sorted(inside["keys"]), ["1", "2"])
        self.assertNotIn("sessions", inside)                      # folders have no session slots
        set_key(cfg, 3, "folder", label="Work renamed", color="#123456")
        self.assertEqual(sorted(cfg["keys"]["3"]["keys"]), ["1", "2"])
        self.assertEqual(cfg["keys"]["3"]["title"], "Work renamed")
        set_key(cfg, 3, "clock")                                   # another type drops the folder
        self.assertEqual(cfg["keys"]["3"], {"type": "clock"})
        self.assertEqual(cfg["sessions"]["keys"], [5])

    def test_every_kind_round_trips_and_loads(self):
        import json
        import tempfile
        from settings import KEY_KINDS
        from test_layout import isolate
        from layout import load_board
        with tempfile.TemporaryDirectory() as tmp:
            isolate(self, tmp)
            todo = {"file": os.path.join(tmp, "work.md"), "open_with": "code", "soon_days": 1, "hold_done": True}
            for kind, _ in KEY_KINDS:
                cfg = {"keys": {}}
                set_key(cfg, 1, kind, label="T", color="#112233", run="true", team="T1", todo=todo,
                        tiles=["tokens"], connection="Office", calendar={"url": "", "warn_min": 5}, token="",
                        weather={"place": "60.17, 24.94", "units": "celsius"}, player="browser")
                self.assertEqual(key_kind(cfg, 1)[0], kind, kind)
                path = os.path.join(tmp, "keys.json")
                with open(path, "w") as f:
                    json.dump(cfg, f)
                keys, *_ = load_board(path)
                cls = type(keys[1]).__name__
                if kind == "empty":
                    self.assertEqual(cls, "Blank", kind)
                else:
                    self.assertNotEqual(cls, "Blank", f"{kind} saved by the dialog loads as an empty key")

    def test_resave_keeps_fields_the_dialog_does_not_show(self):
        cfg = {"keys": {"1": {"type": "brightness", "steps": [20, 80]},
                        "2": {"type": "media", "action": "next", "player": "chromium"}}}
        set_key(cfg, 1, "brightness")
        self.assertEqual(cfg["keys"]["1"]["steps"], [20, 80])
        set_key(cfg, 2, "media_next")
        self.assertEqual(cfg["keys"]["2"]["player"], "chromium")
        set_key(cfg, 2, "clock")                      # a new type starts clean
        self.assertEqual(cfg["keys"]["2"], {"type": "clock"})

    def test_media_player_and_weather_fields(self):
        cfg = {"keys": {"2": {"type": "media", "action": "next", "player": "chromium"}}}
        set_key(cfg, 2, "media_next", player="browser")
        self.assertEqual(cfg["keys"]["2"], {"type": "media", "action": "next", "player": "browser"})
        self.assertEqual(key_summary(cfg, 2), "Browser\nnext")
        set_key(cfg, 2, "media_next", player="spotify")                  # the default is not written
        self.assertEqual(cfg["keys"]["2"], {"type": "media", "action": "next"})
        self.assertEqual(key_summary(cfg, 2), "Spotify\nnext")
        cfg = {"keys": {"1": {"type": "weather", "lat": 60.17, "lon": 24.94, "open": "https://x"}}}
        self.assertEqual(key_summary(cfg, 1), "Weather\nset")
        set_key(cfg, 1, "weather", label="Home", weather={"place": "Oulu", "units": "fahrenheit"})
        self.assertEqual(cfg["keys"]["1"], {"type": "weather", "place": "Oulu", "units": "fahrenheit",
                                            "title": "Home", "open": "https://x"})   # lat/lon gone, open kept
        self.assertEqual(key_summary(cfg, 1), "Weather\nHome")
        set_key(cfg, 1, "weather", label="", weather={"place": "Oulu", "units": "celsius"})
        self.assertNotIn("title", cfg["keys"]["1"])
        self.assertEqual(key_summary(cfg, 1), "Weather\nOulu")

    def test_default_todo_file_stays_implicit(self):
        cfg = {"keys": {}}
        set_key(cfg, 3, "todo", todo={"open_with": "code", "soon_days": 0, "hold_done": True})
        self.assertNotIn("file", cfg["keys"]["3"])

    def test_kind_and_summary(self):
        cfg = {"keys": {"1": {"type": "folder", "title": "Work", "keys": {}}, "2": {"title": "A", "run": "x"}}}
        self.assertEqual(key_kind(cfg, 1)[0], "folder")
        self.assertEqual(key_summary(cfg, 1), "Folder\nWork")
        self.assertEqual(key_kind(cfg, 2)[0], "command")
        self.assertEqual(key_kind(cfg, 9)[0], "empty")


class HideDoneRowTest(unittest.TestCase):
    """0.13.3 put the timer on the General page; it is about the session keys, so
    it belongs in their dialog, beside the corner-mark switch."""

    def test_the_row_lives_in_the_key_dialog(self):
        import ast
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "settings.py")
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        owners = {}
        for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            for node in ast.walk(cls):
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        if isinstance(t, ast.Attribute) and t.attr in ("hide_done", "mark_row"):
                            owners.setdefault(t.attr, set()).add(cls.name)
        self.assertEqual(len(owners["mark_row"]), 1)
        self.assertEqual(owners["hide_done"], owners["mark_row"])

    @unittest.skipIf(set_key is None, "GTK not available")
    def test_value_for_the_row(self):
        from settings import hide_done_hours
        self.assertEqual(hide_done_hours({"sessions": {"hide_done_h": 4}}), 4.0)
        self.assertEqual(hide_done_hours({"sessions": {"hide_done_h": 9999}}), 168.0)
        for cfg in ({}, {"sessions": {}}, {"sessions": {"hide_done_h": "x"}}, {"sessions": {"hide_done_h": -3}}):
            self.assertEqual(hide_done_hours(cfg), 0.0)


if __name__ == "__main__":
    unittest.main()
