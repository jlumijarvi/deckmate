import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codexsessions  # noqa: E402
from codexsessions import analyze_tail, call_activity, match, name_time, thread_id_from_name, thread_names  # noqa: E402


def ev(t, pt=None, ts="2026-11-17T18:50:19.000Z", **payload):
    p = {"type": pt, **payload} if pt else payload
    return json.dumps({"timestamp": ts, "type": t, "payload": p})


def tool_call(call_id, text, name="exec", ts="2026-11-17T18:54:33.000Z"):
    return ev("response_item", "custom_tool_call", ts, call_id=call_id, name=name, input=text, status="completed")


def tool_output(call_id, ts="2026-11-17T18:56:21.000Z"):
    return ev("response_item", "custom_tool_call_output", ts, call_id=call_id, output="")


ESCALATED = ('const r = await tools.exec_command({cmd:"touch ~/codex-test.txt",sandbox_permissions:"require_escalated",'
             'justification:"May I create codex-test.txt in your home directory?"}); text(r.output);')
PLAIN = 'const r = await tools.exec_command({cmd:"ls -l ~/codex-test.txt"}); text(r.output);'
TOKENS = ev("event_msg", "token_count", info={"last_token_usage": {"input_tokens": 15394, "output_tokens": 13},
                                             "model_context_window": 475000})


class TailTest(unittest.TestCase):
    def test_running_then_done(self):
        started = ev("event_msg", "task_started", turn_id="t1")
        self.assertEqual(analyze_tail([started])["state"], "running")
        complete = ev("event_msg", "task_complete", "2026-11-17T18:50:22.000Z", turn_id="t1")
        done = analyze_tail([started, TOKENS, complete])
        self.assertEqual(done["state"], "done")
        self.assertEqual((done["ctx_used"], done["ctx_window"]), (15407, 475000))
        self.assertAlmostEqual(done["since"], 1794941422.0)
        self.assertEqual(analyze_tail([started, ev("event_msg", "turn_aborted", turn_id="t1")])["state"], "done")

    def test_escalated_call_without_output_is_waiting(self):
        info = analyze_tail([ev("event_msg", "task_started", turn_id="t1"), tool_call("c1", ESCALATED)])
        self.assertEqual((info["state"], info["waiting_kind"]), ("waiting", "!"))
        self.assertEqual(info["activity"], "May I create codex-test.txt in your home directory?")
        self.assertAlmostEqual(info["since"], 1794941673.0)
        after = analyze_tail([ev("event_msg", "task_started", turn_id="t1"), tool_call("c1", ESCALATED),
                              tool_output("c1")])
        self.assertEqual(after["state"], "running")

    def test_plain_call_is_running_with_the_command(self):
        info = analyze_tail([ev("event_msg", "task_started", turn_id="t1"), tool_call("c2", PLAIN)])
        self.assertEqual((info["state"], info["activity"]), ("running", "ls -l ~/codex-test.txt"))
        self.assertEqual(call_activity("wait", '{"cell_id":"1"}'), "wait")

    def test_user_input_request_and_error(self):
        started = ev("event_msg", "task_started", turn_id="t1")
        ask = ev("response_item", "function_call", call_id="q", name="request_user_input", arguments="{}")
        info = analyze_tail([started, ask])
        self.assertEqual((info["state"], info["waiting_kind"]), ("waiting", "?"))
        self.assertEqual(analyze_tail([started, ev("event_msg", "error", message="boom")])["state"], "failed")
        self.assertEqual(analyze_tail(["not json", ""])["state"], "done")

    def test_calls_from_an_earlier_turn_do_not_count(self):
        lines = [ev("event_msg", "task_started", turn_id="t1"), tool_call("c1", ESCALATED),
                 ev("event_msg", "task_complete", turn_id="t1"), ev("event_msg", "task_started", turn_id="t2")]
        self.assertEqual(analyze_tail(lines)["state"], "running")


class FilesTest(unittest.TestCase):
    def test_thread_id_from_name(self):
        self.assertEqual(thread_id_from_name("rollout-2026-11-17T20-49-23-01a1ead7-a547-7383-84e4-abd7970dcbb1.jsonl"),
                         "01a1ead7-a547-7383-84e4-abd7970dcbb1")
        forked = ("rollout-2026-11-17T20-45-47-01a1ead3-ff12-79d2-a226-1139f6d8aa77"
                  "_01a1ead4-5a7d-7d62-9179-050beea98da7.jsonl")
        self.assertEqual(thread_id_from_name(forked), "01a1ead4-5a7d-7d62-9179-050beea98da7")

    def test_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "session_index.jsonl")
            with open(index, "w") as f:
                f.write(json.dumps({"id": "a", "thread_name": "Old"}) + "\n" + "junk\n"
                        + json.dumps({"id": "a", "thread_name": "New"}) + "\n")
            self.assertEqual(thread_names(index), {"a": "New"})
            self.assertEqual(thread_names(os.path.join(tmp, "missing")), {})

    def test_name_time(self):
        import time
        t = name_time("/x/rollout-2026-11-17T20-49-23-abc.jsonl")
        self.assertEqual(time.localtime(t)[:6], (2026, 11, 17, 20, 49, 23))
        self.assertIsNone(name_time("/x/other.jsonl"))

    def test_match_processes_to_rollouts(self):
        # (path, mtime, folder, named start)
        rollouts = [("old", 1000.0, "/p", 900.0), ("mine", 2000.0, "/p", 1500.0), ("other", 2100.0, "/q", 1600.0)]
        self.assertEqual(match([(1500.0, "/p")], rollouts), [("mine", 1500.0)])      # not "old": written before start
        self.assertEqual(match([(1600.0, "/q")], rollouts), [("other", 1600.0)])
        self.assertEqual(match([(3000.0, "/p")], rollouts), [])                       # no rollout yet: no prompt made
        self.assertEqual(match([], rollouts), [])
        # two clients in one folder: each gets its own rollout, the closest by start time
        two = rollouts + [("second", 2500.0, "/p", 2400.0)]
        self.assertEqual(match([(2400.0, "/p"), (1500.0, "/p")], two), [("mine", 1500.0), ("second", 2400.0)])


if __name__ == "__main__":
    unittest.main()
