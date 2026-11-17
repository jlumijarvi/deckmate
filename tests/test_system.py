import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from system import claim_pid, pid_from, release_pid  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
