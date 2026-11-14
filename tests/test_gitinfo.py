import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gitinfo import GitState, dot_kind, probe  # noqa: E402


class DotKindTest(unittest.TestCase):
    def test_kinds(self):
        self.assertIsNone(dot_kind(None))
        self.assertIsNone(dot_kind(GitState(False, True, 3, 0)))
        self.assertEqual(dot_kind(GitState(True, True, 0, 0)), "hollow")
        self.assertEqual(dot_kind(GitState(True, False, 2, 0)), "filled")
        self.assertIsNone(dot_kind(GitState(True, False, 0, 0)))


class ProbeTest(unittest.TestCase):
    def test_probe_on_a_fresh_repo(self):
        d = tempfile.mkdtemp()
        self.assertFalse(probe(d).is_repo)
        env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t", "PATH": os.environ["PATH"], "HOME": d}
        subprocess.run(["git", "init", "-q", d], check=True, env=env)
        with open(os.path.join(d, "f"), "w") as f:
            f.write("x")
        st = probe(d)
        self.assertTrue(st.is_repo)
        self.assertTrue(st.dirty)            # untracked file
        self.assertEqual(st.ahead, 0)        # no upstream: counts as 0
        subprocess.run(["git", "-C", d, "add", "f"], check=True, env=env)
        subprocess.run(["git", "-C", d, "commit", "-q", "-m", "x"], check=True, env=env)
        self.assertFalse(probe(d).dirty)


if __name__ == "__main__":
    unittest.main()
