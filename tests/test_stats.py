import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from stats import PlanUsage, TokenTally, Usage, age_text, fmt_tokens, parse_usage_cache  # noqa: E402

T0 = 1_800_000_000.0   # an arbitrary "now"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


def cache(session=10, week=40, fable=5, at=T0, session_reset=T0 + 3600, week_reset=T0 + 5 * 86400):
    return {"cachedUsageUtilization": {"fetchedAtMs": int(at * 1000), "utilization": {
        "five_hour": {"utilization": session, "resets_at": iso(session_reset)},
        "seven_day": {"utilization": week, "resets_at": iso(week_reset)},
        "limits": [
            {"kind": "session", "group": "session", "percent": session, "resets_at": iso(session_reset)},
            {"kind": "weekly_all", "group": "weekly", "percent": week, "resets_at": iso(week_reset)},
            {"kind": "weekly_scoped", "group": "weekly", "percent": fable, "resets_at": iso(week_reset),
             "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        ]}}}


class ParseTest(unittest.TestCase):
    def test_limits(self):
        lims = parse_usage_cache(cache()["cachedUsageUtilization"])
        self.assertEqual(sorted(lims), ["scoped", "session", "week"])
        self.assertEqual(lims["session"].used_pct, 10)
        self.assertEqual(lims["week"].used_pct, 40)
        self.assertEqual((lims["scoped"].label, lims["scoped"].used_pct), ("Fable", 5))
        self.assertAlmostEqual(lims["session"].resets_at, T0 + 3600)
        self.assertAlmostEqual(lims["session"].at, T0)

    def test_older_cache_without_limits_list(self):
        c = cache()["cachedUsageUtilization"]
        del c["utilization"]["limits"]
        lims = parse_usage_cache(c)
        self.assertEqual(sorted(lims), ["session", "week"])
        self.assertEqual(lims["week"].used_pct, 40)

    def test_nothing(self):
        self.assertEqual(parse_usage_cache(None), {})
        self.assertEqual(parse_usage_cache({"utilization": {"limits": ["junk"]}}), {})


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache_path = os.path.join(self.tmp.name, "claude.json")
        self.desktop_path = os.path.join(self.tmp.name, "plan.json")
        self.now = T0

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, data):
        with open(path, "w") as f:
            json.dump(data, f)
        os.utime(path, (self.now, self.now))

    def usage(self):
        return Usage(self.cache_path, self.desktop_path, clock=lambda: self.now)

    def test_reads_claude_code_cache(self):
        self.write(self.cache_path, cache())
        u = self.usage()
        u.refresh()
        self.assertEqual((u.used("session"), u.used("week"), u.used("scoped")), (10, 40, 5))
        self.assertEqual(u.reset_events, [])
        self.assertFalse(u.stale("session"))
        self.assertEqual(u.limits["session"].source, "code")

    def test_newer_desktop_sample_wins_for_session_and_week(self):
        self.write(self.cache_path, cache(session=10, week=40, at=T0 - 7200))
        self.write(self.desktop_path, {"samples": [{"t": int((T0 - 60) * 1000), "u": {"fh": 55, "sd": 42}}]})
        u = self.usage()
        u.refresh()
        self.assertEqual((u.used("session"), u.used("week"), u.used("scoped")), (55, 42, 5))
        self.assertEqual(u.limits["session"].source, "desktop")
        self.assertAlmostEqual(u.limits["session"].resets_at, T0 + 3600)   # reset time kept from the cache

    def test_no_reset_event_on_start_and_one_when_the_window_ends(self):
        self.write(self.cache_path, cache(session_reset=T0 + 600))
        u = self.usage()
        u.refresh()
        self.assertEqual(u.reset_events, [])
        self.now = T0 + 300
        u.refresh()
        self.assertEqual(u.reset_events, [])
        self.now = T0 + 601
        u.refresh()
        self.assertEqual(u.reset_events, ["session"])
        self.assertTrue(u.new_window["session"])
        self.assertEqual(u.used("session"), 0)
        u.refresh()
        self.assertEqual(u.reset_events, [])        # fires once
        # fresh data from after the reset ends the "new window" state
        self.now = T0 + 700
        self.write(self.cache_path, cache(session=3, at=T0 + 650, session_reset=T0 + 650 + 18000))
        u.refresh()
        self.assertFalse(u.new_window["session"])
        self.assertEqual(u.used("session"), 3)
        self.assertEqual(u.reset_events, [])

    def test_reset_seen_in_data_first_fires_once(self):
        self.write(self.cache_path, cache(session_reset=T0 + 600))
        u = self.usage()
        u.refresh()
        self.now = T0 + 100
        self.write(self.cache_path, cache(session=1, at=T0 + 100, session_reset=T0 + 100 + 18000))
        u.refresh()
        self.assertEqual(u.reset_events, ["session"])
        self.assertFalse(u.new_window["session"])

    def test_stale_and_age(self):
        self.write(self.cache_path, cache(at=T0 - 3 * 3600))
        u = self.usage()
        u.refresh()
        self.assertTrue(u.stale("session"))
        self.assertEqual(age_text(u.age("session")), "3 h")
        self.write(self.cache_path, cache(at=T0 - 3600))
        fresh = self.usage()
        fresh.refresh()
        self.assertFalse(fresh.stale("session"))      # an hour old is still shown in colour
        self.assertEqual(u.reset_text("session"), datetime.fromtimestamp(T0 + 3600).strftime("%H:%M"))
        self.assertEqual(u.reset_text("week"), datetime.fromtimestamp(T0 + 5 * 86400).strftime("%a %H:%M"))

    def test_session_without_a_window_is_unknown_not_full(self):
        c = cache()
        for lim in c["cachedUsageUtilization"]["utilization"]["limits"]:
            if lim["kind"] == "session":
                lim["percent"], lim["resets_at"] = 0, None
        c["cachedUsageUtilization"]["utilization"]["five_hour"] = {"utilization": 0, "resets_at": None}
        self.write(self.cache_path, c)
        u = self.usage()
        u.refresh()
        self.assertTrue(u.limits["session"].idle)
        self.assertIsNone(u.used("session"))
        self.assertEqual(u.used("week"), 40)

    def test_missing_files(self):
        u = self.usage()
        u.refresh()
        self.assertEqual(u.limits, {})
        self.assertIsNone(u.used("session"))
        self.assertTrue(u.stale("session"))

    def test_half_written_cache_keeps_the_last_values(self):
        self.write(self.cache_path, cache())
        u = self.usage()
        u.refresh()
        with open(self.cache_path, "w") as f:
            f.write('{"cachedUsageUtilization": {"fetchedAt')
        os.utime(self.cache_path, (self.now + 1, self.now + 1))
        u.refresh()
        self.assertEqual(u.used("week"), 40)


class PlanUsageTest(unittest.TestCase):
    def test_desktop_samples(self):
        p = PlanUsage("/nonexistent")
        p.apply([{"t": 1000, "u": {"fh": 60, "sd": 30}}, {"t": 2000, "u": {"fh": 70, "sd": 31}}])
        self.assertEqual(p.limit("session").used_pct, 70)
        self.assertEqual(p.limit("week").used_pct, 31)
        self.assertEqual(p.sampled_at, 2.0)

    def test_missing_file(self):
        p = PlanUsage("/nonexistent")
        p.refresh()
        self.assertIsNone(p.limit("session"))


class AgeTextTest(unittest.TestCase):
    def test_texts(self):
        self.assertEqual(age_text(None), "no data")
        self.assertEqual(age_text(30), "just now")
        self.assertEqual(age_text(600), "10 min")
        self.assertEqual(age_text(7200), "2 h")
        self.assertEqual(age_text(3 * 86400), "3 d")


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

    def write(self, lines, mode="w", path=None):
        with open(path or self.path, mode) as f:
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

    def test_subagent_transcripts_count(self):
        self.write([line("m1", self.now)])
        sub = os.path.join(self.root, "proj", "s", "subagents")
        os.makedirs(sub)
        self.write([line("a1", self.now, tout=11)], path=os.path.join(sub, "agent-1.jsonl"))
        t = TokenTally(self.root)
        t.refresh()
        self.assertEqual(t.messages, 2)
        self.assertEqual(t.tokens_out, 16)

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
