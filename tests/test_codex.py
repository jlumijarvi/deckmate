import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import codexsessions  # noqa: E402
from codexsessions import analyze_tail, call_activity, open_rollouts, thread_id_from_name, thread_names  # noqa: E402


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

    def test_names_and_open_rollouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = os.path.join(tmp, "session_index.jsonl")
            with open(index, "w") as f:
                f.write(json.dumps({"id": "a", "thread_name": "Old"}) + "\n" + "junk\n"
                        + json.dumps({"id": "a", "thread_name": "New"}) + "\n")
            self.assertEqual(thread_names(index), {"a": "New"})
            self.assertEqual(thread_names(os.path.join(tmp, "missing")), {})
            # a fake /proc/<pid>/fd: links to a rollout, a database and a socket
            fd = os.path.join(tmp, "fd")
            os.makedirs(fd)
            rollout = os.path.join(tmp, "sessions", "2026", "11", "17", "rollout-2026-11-17T20-49-23-abc.jsonl")
            os.makedirs(os.path.dirname(rollout))
            open(rollout, "w").close()
            os.symlink(rollout, os.path.join(fd, "3"))
            os.symlink(os.path.join(tmp, "history.sqlite"), os.path.join(fd, "4"))
            os.symlink("socket:[123]", os.path.join(fd, "5"))
            self.assertEqual(open_rollouts(0, fd), [rollout])
            self.assertEqual(open_rollouts(0, os.path.join(tmp, "no-such-process")), [])

    def test_daemon_pid_missing(self):
        self.assertIsNone(codexsessions.daemon_pid("/nonexistent/daemon.pid"))


if __name__ == "__main__":
    unittest.main()
