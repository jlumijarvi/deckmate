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
        self.assertEqual(context_window_for("claude-opus-5-5"), 1_000_000)
        self.assertEqual(context_window_for("claude-fable-5-1"), 1_000_000)
        self.assertEqual(context_window_for("claude-opus-4-6"), 1_000_000)
        self.assertEqual(context_window_for("claude-sonnet-4-6"), 1_000_000)
        self.assertEqual(context_window_for("claude-haiku-4-5"), 200_000)
        self.assertEqual(context_window_for("claude-haiku-4-5-20251001"), 200_000)
        self.assertEqual(context_window_for("claude-sonnet-4-5"), 200_000)
        self.assertEqual(context_window_for("claude-opus-4-5-20251101"), 200_000)
        self.assertEqual(context_window_for("claude-3-7-sonnet-latest"), 200_000)
        self.assertEqual(context_window_for(None), 1_000_000)

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



class ActivityTest(unittest.TestCase):
    def test_texts(self):
        from sessions import activity_text
        self.assertEqual(activity_text("Bash", {"command": "ls", "description": "List files"}), "List files")
        self.assertEqual(activity_text("Bash", {"command": "ls"}), "Bash")
        self.assertEqual(activity_text("AskUserQuestion", {"questions": [{"header": "Prev pager"}]}), "Prev pager")
        self.assertEqual(activity_text("AskUserQuestion", {}), "question")
        self.assertEqual(activity_text("ExitPlanMode", {}), "approve plan")
        self.assertEqual(activity_text("Edit", {"file_path": "/a/b/deck.py"}), "Edit deck.py")
        self.assertEqual(activity_text("mcp__claude_ai_Gmail__send_message", {}), "send_message")
        self.assertEqual(activity_text("Agent", {"description": "Find callers"}), "Find callers")
        self.assertEqual(activity_text("Weird", None), "Weird")

    def test_pending_call_sets_the_activity(self):
        call = {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"description": "Run tests"}}
        r = analyze_tail([human("do"), asst([call])])
        self.assertEqual((r["state"], r["activity"]), ("waiting", "Run tests"))
        r = analyze_tail([human("do"), asst([call]), result("t1"), asst([{"type": "text", "text": "ok"}], mid="m2")])
        self.assertEqual((r["state"], r["activity"]), ("done", ""))

    def test_subagent_count(self):
        import tempfile
        import time
        from sessions import subagent_count
        with tempfile.TemporaryDirectory() as tmp:
            transcript = os.path.join(tmp, "sess.jsonl")
            sub = os.path.join(tmp, "sess", "subagents")
            os.makedirs(sub)
            for name, age in (("agent-a.jsonl", 5), ("agent-b.jsonl", 30), ("agent-old.jsonl", 600)):
                p = os.path.join(sub, name)
                open(p, "w").close()
                os.utime(p, (time.time() - age, time.time() - age))
            self.assertEqual(subagent_count(transcript), 2)
            self.assertEqual(subagent_count(os.path.join(tmp, "other.jsonl")), 0)
            self.assertEqual(subagent_count(""), 0)


class LiveStateTest(unittest.TestCase):
    """The registry entry of a live process, with or without the transcript's view."""
    DONE = {"state": "done", "waiting_kind": "", "since": 100.0, "activity": ""}
    ASKING = {"state": "waiting", "waiting_kind": "?", "since": 200.0, "activity": "Approach"}

    def test_waiting_before_the_question_reaches_the_transcript(self):
        # 0.13.2 read the transcript alone here and flashed the session green as done
        from sessions import live_state
        reg = {"status": "waiting", "waitingFor": "input needed", "statusUpdatedAt": 300_000}
        self.assertEqual(live_state(reg, self.DONE), ("waiting", "?", 300.0, ""))
        self.assertEqual(live_state(reg, None), ("waiting", "?", 300.0, ""))
        reg["waitingFor"] = "dialog open"
        self.assertEqual(live_state(reg, self.DONE), ("waiting", "!", 300.0, ""))

    def test_waiting_takes_the_details_from_the_transcript_once_there(self):
        from sessions import live_state
        reg = {"status": "waiting", "waitingFor": "input needed", "statusUpdatedAt": 300_000}
        self.assertEqual(live_state(reg, self.ASKING), ("waiting", "?", 200.0, "Approach"))

    def test_busy_and_idle(self):
        from sessions import live_state
        self.assertEqual(live_state({"status": "busy", "statusUpdatedAt": 5000}, self.ASKING),
                         ("running", "", 5.0, "Approach"))
        self.assertEqual(live_state({"status": "idle"}, self.DONE), ("done", "", 100.0, ""))
        self.assertEqual(live_state({"status": "idle"}, self.ASKING), ("waiting", "?", 200.0, "Approach"))
        self.assertEqual(live_state({"status": "idle", "statusUpdatedAt": 7000}, None), ("done", "", 7.0, ""))


class DesktopRecordsTest(unittest.TestCase):
    def test_archived_is_read_from_the_apps_record_and_follows_it(self):
        import tempfile
        from sessions import DesktopRecords
        with tempfile.TemporaryDirectory() as tmp:
            folder = os.path.join(tmp, "account", "org")
            os.makedirs(folder)
            path = os.path.join(folder, "local_abc-123.json")
            records = DesktopRecords(tmp)
            self.assertFalse(records.archived("local_abc-123"))        # no record yet
            with open(path, "w") as f:
                json.dump({"isArchived": False}, f)
            self.assertFalse(records.archived("local_abc-123"))
            with open(path, "w") as f:
                json.dump({"isArchived": True}, f)
            os.utime(path, ns=(1, 1))
            self.assertTrue(records.archived("local_abc-123"))
            with open(path, "w") as f:
                f.write("{")                                           # the app is mid-write
            os.utime(path, ns=(2, 2))
            self.assertTrue(records.archived("local_abc-123"))         # the last answer stands
            os.remove(path)
            self.assertFalse(records.archived("local_abc-123"))
            self.assertFalse(records.archived(None))
            self.assertFalse(records.archived("../../etc/passwd"))

    def test_scan_leaves_out_an_archived_desktop_session(self):
        import tempfile
        from unittest import mock
        import sessions
        with tempfile.TemporaryDirectory() as tmp:
            reg_dir, desk = os.path.join(tmp, "sessions"), os.path.join(tmp, "desk", "a", "o")
            os.makedirs(reg_dir)
            os.makedirs(desk)
            for pid, host, status in ((1, "local_kept", "idle"), (2, "local_archived", "idle"),
                                      (3, "local_busy", "busy")):
                with open(os.path.join(reg_dir, f"{pid}.json"), "w") as f:
                    json.dump({"pid": pid, "sessionId": "", "cwd": f"/work/p{pid}", "status": status,
                               "entrypoint": "claude-desktop", "hostSessionId": host}, f)
                with open(os.path.join(desk, host + ".json"), "w") as f:
                    json.dump({"isArchived": host != "local_kept"}, f)
            with mock.patch.object(sessions, "SESSIONS_DIR", reg_dir), \
                    mock.patch.object(sessions, "is_alive", lambda pid, start: True):
                scanner = sessions.SessionScanner()
                scanner.desktop = sessions.DesktopRecords(os.path.join(tmp, "desk"))
                found = {s.pid: s.state for s in scanner.scan()}
            self.assertEqual(found, {1: "done", 3: "running"})         # archived but still working: shown


if __name__ == "__main__":
    unittest.main()
