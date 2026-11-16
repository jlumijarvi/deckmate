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

    def test_kind_and_summary(self):
        cfg = {"keys": {"1": {"type": "folder", "title": "Work", "keys": {}}, "2": {"title": "A", "run": "x"}}}
        self.assertEqual(key_kind(cfg, 1)[0], "folder")
        self.assertEqual(key_summary(cfg, 1), "Folder\nWork")
        self.assertEqual(key_kind(cfg, 2)[0], "command")
        self.assertEqual(key_kind(cfg, 9)[0], "empty")


if __name__ == "__main__":
    unittest.main()
