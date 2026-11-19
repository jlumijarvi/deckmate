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
                        tiles=["tokens"], connection="Office", calendar={"url": "", "warn_min": 5}, token="")
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


if __name__ == "__main__":
    unittest.main()
