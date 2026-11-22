import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import system  # noqa: E402
from system import claim_pid, dev_board_active, pid_from, release_pid  # noqa: E402


class PidFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "x.pid")

    def tearDown(self):
        self.tmp.cleanup()

    def test_claim_release(self):
        self.assertTrue(claim_pid(self.path))
        self.assertEqual(pid_from(self.path), os.getpid())
        self.assertTrue(claim_pid(self.path))         # our own file again: fine
        release_pid(self.path)
        self.assertFalse(os.path.exists(self.path))
        release_pid(self.path)                        # nothing there: no error

    def test_second_copy_refused_and_does_not_delete_the_first(self):
        with open(self.path, "w") as f:
            f.write("1")                              # init: alive, not us
        self.assertFalse(claim_pid(self.path))
        release_pid(self.path)                        # the refused copy exits
        self.assertEqual(pid_from(self.path), 1)      # the running one's file is untouched

    def test_dead_pid_is_taken_over(self):
        with open(self.path, "w") as f:
            f.write("4194304")                        # above pid_max on a default Linux: no such process
        self.assertTrue(claim_pid(self.path))
        self.assertEqual(pid_from(self.path), os.getpid())


class DevBoardTest(unittest.TestCase):
    def test_active_only_when_systemd_says_so(self):
        saved = system.sh
        try:
            for out, expected in (("active\n", True), ("inactive\n", False), ("", False), ("activating\n", False)):
                system.sh = lambda cmd, out=out: out if "deckmate-dev.service" in cmd else "?"
                self.assertEqual(dev_board_active(), expected, out)
        finally:
            system.sh = saved


class BoardPidFileTest(unittest.TestCase):
    def test_board_releases_only_its_own_pid_file(self):
        """0.13.0: the board removed deck.pid on exit whoever wrote it, so a dev board
        quitting after an update left the updated board without one ("not running")."""
        import ast
        tree = ast.parse(open(os.path.join(os.path.dirname(__file__), "..", "deck.py")).read())
        removed = [ast.unparse(n.args[0]) for n in ast.walk(tree)
                   if isinstance(n, ast.Call) and ast.unparse(n.func) == "os.remove" and n.args]
        self.assertNotIn("PID_FILE", removed)
        self.assertNotIn("f", removed, "a loop over (PID_FILE, STATUS_FILE) removes the pid file too")
        called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertIn("release_pid", called)


if __name__ == "__main__":
    unittest.main()
