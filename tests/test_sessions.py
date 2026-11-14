import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sessions import analyze_tail, context_window_for, elapsed_text  # noqa: E402


def human(text, ts="2026-11-14T13:00:00.000Z"):
    return json.dumps({"type": "user", "timestamp": ts,
                       "message": {"role": "user", "content": [{"type": "text", "text": text}]}})


def asst(blocks, mid="msg_1", ts="2026-11-14T13:00:05.000Z", model="claude-fable-5-1", err=False):
    e = {"type": "assistant", "timestamp": ts, "message": {
        "id": mid, "model": model, "content": blocks,
        "usage": {"input_tokens": 2, "cache_read_input_tokens": 50000,
                  "cache_creation_input_tokens": 100, "output_tokens": 300}}}
    if err:
        e["isApiErrorMessage"] = True
    return json.dumps(e)


def tool_use(name, tid):
    return {"type": "tool_use", "id": tid, "name": name, "input": {}}


def result(tid, ts="2026-11-14T13:00:06.000Z"):
    return json.dumps({"type": "user", "timestamp": ts, "toolUseResult": True,
                       "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid}]}})


class ClassifyTest(unittest.TestCase):
    def state(self, lines):
        r = analyze_tail(lines)
        return r["state"] + (" " + r["waiting_kind"] if r["waiting_kind"] else "")

    def test_done(self):
        self.assertEqual(self.state([human("hi"), asst([{"type": "text", "text": "done"}])]), "done")

    def test_permission_prompt(self):
        self.assertEqual(self.state([human("do"), asst([tool_use("Bash", "t1")])]), "waiting !")

    def test_question_and_plan(self):
        self.assertEqual(self.state([human("do"), asst([tool_use("AskUserQuestion", "t1")])]), "waiting ?")
        self.assertEqual(self.state([human("do"), asst([tool_use("ExitPlanMode", "t1")])]), "waiting ?")

    def test_answered_tool_is_done(self):
        lines = [human("do"), asst([tool_use("Bash", "t1")]), result("t1"),
                 asst([{"type": "text", "text": "ok"}], mid="msg_2")]
        self.assertEqual(self.state(lines), "done")

    def test_one_of_two_tools_pending(self):
        lines = [human("do"), asst([tool_use("Bash", "t1")]), asst([tool_use("Bash", "t2")]), result("t1")]
        self.assertEqual(self.state(lines), "waiting !")

    def test_failed(self):
        self.assertEqual(self.state([human("do"), asst([{"type": "text", "text": "API Error"}], err=True)]), "failed")

    def test_no_reply_yet_is_running(self):
        lines = [human("hi"), asst([{"type": "text", "text": "x"}]), human("again", ts="2026-11-14T13:01:00.000Z")]
        self.assertEqual(self.state(lines), "running")

    def test_pending_tool_from_earlier_turn_ignored(self):
        lines = [human("a"), asst([tool_use("Bash", "t0")]), human("b", ts="2026-11-14T13:02:00.000Z"),
                 asst([{"type": "text", "text": "x"}], mid="msg_3")]
        self.assertEqual(self.state(lines), "done")

    def test_sidechain_ignored(self):
        side = json.dumps({"type": "assistant", "isSidechain": True,
                           "message": {"content": [tool_use("Bash", "s1")]}})
        self.assertEqual(self.state([human("hi"), asst([{"type": "text", "text": "done"}]), side]), "done")

    def test_context_usage_and_window(self):
        r = analyze_tail([human("hi"), asst([{"type": "text", "text": "x"}])])
        self.assertEqual(r["ctx_used"], 50402)
        self.assertEqual(r["ctx_window"], 1_000_000)
        self.assertEqual(context_window_for("claude-opus-5-5"), 200_000)
        self.assertEqual(context_window_for("claude-fable-5-1"), 1_000_000)

    def test_usage_past_200k_steps_up_to_1m(self):
        big = json.loads(asst([{"type": "text", "text": "x"}], model="claude-opus-5-5"))
        big["message"]["usage"]["cache_read_input_tokens"] = 300_000
        r = analyze_tail([human("hi"), json.dumps(big)])
        self.assertEqual(r["ctx_window"], 1_000_000)

    def test_ai_title(self):
        title = json.dumps({"type": "ai-title", "aiTitle": "Nice title"})
        r = analyze_tail([title, human("hi"), asst([{"type": "text", "text": "x"}])])
        self.assertEqual(r["title"], "Nice title")

    def test_elapsed_text(self):
        now = 1000.0
        self.assertEqual(elapsed_text(now - 5, now), "5s")
        self.assertEqual(elapsed_text(now - 300, now), "5m")
        self.assertEqual(elapsed_text(now - 4000, now), "1h06")
        self.assertEqual(elapsed_text(now - 90000, now), "1d")


if __name__ == "__main__":
    unittest.main()
