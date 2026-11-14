import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from slackstate import SlackState  # noqa: E402


class SlackParseTest(unittest.TestCase):
    def test_workspaces_and_unreads(self):
        s = SlackState()
        s.data = {
            "workspaces": {
                "T2": {"name": "Second", "domain": "second", "order": 1, "icon": {"image_88": "u88"}},
                "T1": {"name": "First", "domain": "first", "order": 0, "icon": {"image_230": "u230", "image_88": "u88"}},
            },
            "webapp": {"teams": {"T1": {"unreads": {"unreads": 3, "unreadHighlights": 1, "showBullet": True}}}},
        }
        ws = s.workspaces()
        self.assertEqual([w["id"] for w in ws], ["T1", "T2"])
        self.assertEqual(ws[0]["icon_url"], "u230")
        self.assertEqual(s.unreads("T1"), (3, 1))
        self.assertIsNone(s.unreads("T2"))

    def test_missing_data(self):
        s = SlackState()
        self.assertEqual(s.workspaces(), [])
        self.assertIsNone(s.unreads("T1"))


if __name__ == "__main__":
    unittest.main()
