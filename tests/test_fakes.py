import os
import sys
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import calfeed  # noqa: E402
import claudeusage  # noqa: E402
import fakes  # noqa: E402
import slackapi  # noqa: E402
import slackstate  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


class TemplateTest(unittest.TestCase):
    def test_relative_times(self):
        self.assertEqual(fakes.relative_seconds("+90s"), 90)
        self.assertEqual(fakes.relative_seconds("+2h"), 7200)
        self.assertIsNone(fakes.relative_seconds("2026-11-19T10:00:00Z"))
        base = 1794940000.0
        out = fakes.resolve_times({"a": {"resets_at": "+1m"}, "b": ["+0s", 5]}, base)
        self.assertEqual(out["b"][1], 5)
        self.assertEqual(datetime.strptime(out["a"]["resets_at"], "%Y-%m-%dT%H:%M:%S%z").timestamp(), base + 60)
        self.assertEqual(datetime.strptime(out["b"][0], "%Y-%m-%dT%H:%M:%S%z").timestamp(), base)

    def test_calendar_template(self):
        base = time.mktime((2026, 11, 19, 10, 0, 0, 0, 0, -1))
        text = fakes.render_times("DTSTART;VALUE=DATE:{today}\nDTSTART:{now+15m}\nDTEND:{now-1h}\n", base)
        self.assertEqual(text, "DTSTART;VALUE=DATE:20261119\nDTSTART:20261119T101500\nDTEND:20261119T090000\n")


class SourcesTest(unittest.TestCase):
    def setUp(self):
        self.before = os.environ.get(fakes.ENV)
        os.environ[fakes.ENV] = FIXTURES

    def tearDown(self):
        if self.before is None:
            os.environ.pop(fakes.ENV, None)
        else:
            os.environ[fakes.ENV] = self.before

    def test_off_without_the_variable(self):
        os.environ.pop(fakes.ENV, None)
        self.assertIsNone(fakes.directory())
        self.assertIsNone(fakes.load_json("usage.json"))

    def test_slack_state_and_api(self):
        self.assertEqual(slackstate.state_path(), os.path.join(FIXTURES, "slack-state.json"))
        s = slackstate.SlackState()
        s.refresh()
        self.assertEqual(s.unreads("T0FAKE"), (3, 1))
        reply = slackapi.call("xoxp-none", "conversations.info", channel="D1")
        self.assertEqual(reply["channel"]["unread_count_display"], 1)
        api = slackapi.SlackApi("xoxp-none", start=False)
        slackapi.CALL_GAP_S = 0
        try:
            unreads = api._round()
        finally:
            slackapi.CALL_GAP_S = 0.15
        self.assertEqual(slackapi.caption(unreads), "@anna +1")

    def test_usage_and_calendar(self):
        usage = claudeusage.fetch_usage(path="/nonexistent/credentials.json")
        self.assertEqual(usage["five_hour"]["utilization"], 97)
        reset = datetime.strptime(usage["five_hour"]["resets_at"], "%Y-%m-%dT%H:%M:%S%z").timestamp()
        self.assertAlmostEqual(reset, fakes._started + 180, delta=1)
        text = calfeed.fetch("https://example.invalid/never-called.ics")
        events = calfeed.parse_ics(text)
        today = calfeed.occurrences(events, datetime.now().astimezone().date())
        self.assertIn("Planning with the platform team", [o.summary for o in today])


if __name__ == "__main__":
    unittest.main()
