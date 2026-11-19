import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import slackapi  # noqa: E402
from slackapi import Unread, caption, conversation_name, parse_conversations, unread_from_info  # noqa: E402


class ParseTest(unittest.TestCase):
    def test_names(self):
        self.assertEqual(conversation_name({"name": "general"}), "#general")
        self.assertEqual(conversation_name({"is_im": True, "user": "U1"}, {"U1": "anna"}), "@anna")
        self.assertEqual(conversation_name({"is_im": True, "user": "U2"}), "@U2")
        self.assertEqual(conversation_name({"is_mpim": True, "name": "mpdm-anna--bob--carl-1"}), "anna, bob, carl")

    def test_conversations_and_unread(self):
        listing = {"ok": True, "channels": [{"id": "C1", "name": "general"},
                                            {"id": "C2", "name": "old", "is_archived": True},
                                            {"id": "D1", "is_im": True, "user": "U1"}]}
        convs = parse_conversations(listing)
        self.assertEqual([c["id"] for c in convs], ["C1", "D1"])
        info = {"ok": True, "channel": {"id": "C1", "name": "general", "unread_count_display": 3,
                                        "last_read": "1700000000.000100"}}
        u = unread_from_info(info, convs[0])
        self.assertEqual((u.name, u.count), ("#general", 3))
        self.assertAlmostEqual(u.last_read, 1700000000.0001)
        self.assertIsNone(unread_from_info({"ok": True, "channel": {"unread_count_display": 0}}, convs[0]))
        dm = unread_from_info({"ok": True, "channel": {"unread_count_display": 1, "last_read": "5"}}, convs[1],
                              {"U1": "anna"})
        self.assertEqual(dm.name, "@anna")

    def test_caption(self):
        self.assertEqual(caption([]), "")
        self.assertEqual(caption([Unread("#general", 2, 10.0)]), "#general")
        self.assertEqual(caption([Unread("#general", 2, 10.0), Unread("@anna", 1, 5.0), Unread("#dev", 1, 7.0)]),
                         "@anna +2")

    def test_poller_without_a_thread(self):
        api = slackapi.SlackApi("xoxp-test", start=False)
        self.assertEqual(api.unreads(), [])
        calls = []

        def fake_call(token, method, timeout=15, **params):
            calls.append(method)
            if method == "users.conversations":
                return {"ok": True, "channels": [{"id": "C1", "name": "general"},
                                                 {"id": "D1", "is_im": True, "user": "U1"}]}
            if method == "conversations.info":
                ch = {"C1": {"unread_count_display": 0},
                      "D1": {"unread_count_display": 2, "last_read": "3"}}[params["channel"]]
                return {"ok": True, "channel": ch}
            if method == "users.info":
                return {"ok": True, "user": {"profile": {"display_name": "anna"}}}
            raise AssertionError(method)
        slackapi.call, slackapi.CALL_GAP_S = fake_call, 0
        try:
            unreads = api._round()
        finally:
            slackapi.CALL_GAP_S = 0.15
        self.assertEqual([(u.name, u.count) for u in unreads], [("@anna", 2)])
        self.assertEqual(calls, ["users.conversations", "conversations.info", "conversations.info", "users.info"])
        self.assertEqual(caption(unreads), "@anna")


if __name__ == "__main__":
    unittest.main()
