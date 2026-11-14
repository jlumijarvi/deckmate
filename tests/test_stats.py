import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from stats import PlanUsage, TokenTally, fmt_tokens  # noqa: E402


class PlanUsageTest(unittest.TestCase):
    def test_left_and_reset_detection(self):
        p = PlanUsage("/nonexistent")
        p.apply([{"t": 1000, "u": {"fh": 60, "sd": 30}}, {"t": 2000, "u": {"fh": 70, "sd": 31}}])
        self.assertEqual(p.left("fh"), 30)
        self.assertEqual(p.left("sd"), 69)
        self.assertEqual(p.reset_events, [])
        self.assertFalse(p.new_window["fh"])
        p.apply([{"t": 2000, "u": {"fh": 70, "sd": 31}}, {"t": 3000, "u": {"fh": 5, "sd": 31}}])
        self.assertEqual(p.reset_events, ["fh"])
        self.assertTrue(p.new_window["fh"])
        self.assertFalse(p.new_window["sd"])
        # the next sample clears the marker
        p.apply([{"t": 3000, "u": {"fh": 5, "sd": 31}}, {"t": 4000, "u": {"fh": 8, "sd": 32}}])
        self.assertFalse(p.new_window["fh"])

    def test_missing_file(self):
        p = PlanUsage("/nonexistent")
        p.refresh()
        self.assertIsNone(p.left("fh"))


def line(mid, ts, tin=10, tout=5, cache=100, kind="assistant"):
    return json.dumps({"type": kind, "timestamp": ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                       "message": {"id": mid, "usage": {"input_tokens": tin, "cache_creation_input_tokens": 0,
                                                        "cache_read_input_tokens": cache, "output_tokens": tout}}})


class TokenTallyTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "proj"))
        self.path = os.path.join(self.root, "proj", "s.jsonl")
        self.now = datetime.now().astimezone()

    def write(self, lines, mode="w"):
        with open(self.path, mode) as f:
            f.write("\n".join(lines) + "\n")

    def test_dedupe_by_message_id_and_today_only(self):
        yesterday = self.now - timedelta(days=1)
        self.write([line("m1", self.now), line("m1", self.now), line("m2", self.now, tout=7),
                    line("old", yesterday), line("u", self.now, kind="user")])
        t = TokenTally(self.root)
        t.refresh()
        self.assertEqual(t.messages, 2)
        self.assertEqual(t.tokens_out, 12)
        self.assertEqual(t.tokens_in, 20)
        self.assertEqual(t.cache_read, 200)

    def test_incremental_read(self):
        self.write([line("m1", self.now)])
        t = TokenTally(self.root)
        t.refresh()
        self.assertEqual(t.messages, 1)
        self.write([line("m2", self.now)], mode="a")
        t.refresh()
        self.assertEqual(t.messages, 2)
        t.refresh()                       # nothing new: unchanged
        self.assertEqual(t.messages, 2)

    def test_partial_last_line_waits(self):
        self.write([line("m1", self.now)])
        with open(self.path, "a") as f:
            f.write('{"type":"assistant","message":{"id":"m2"')   # half a line, no newline
        t = TokenTally(self.root)
        t.refresh()
        self.assertEqual(t.messages, 1)
        with open(self.path, "a") as f:
            f.write(',"usage":{"output_tokens":3}},"timestamp":"%s"}\n'
                    % self.now.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"))
        t.refresh()
        self.assertEqual(t.messages, 2)


class FormatTest(unittest.TestCase):
    def test_fmt(self):
        self.assertEqual(fmt_tokens(950), "950")
        self.assertEqual(fmt_tokens(1500), "1.5k")
        self.assertEqual(fmt_tokens(45000), "45k")
        self.assertEqual(fmt_tokens(2_300_000), "2.3M")
        self.assertEqual(fmt_tokens(12_000_000), "12M")


if __name__ == "__main__":
    unittest.main()
