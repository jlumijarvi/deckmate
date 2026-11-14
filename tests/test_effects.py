import os
import sys
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from board import Effects, breath, detect_pulses, scroll_offset  # noqa: E402


@dataclass
class S:
    session_id: str
    name: str
    state: str


class PulseDetectTest(unittest.TestCase):
    def test_first_sighting_is_silent(self):
        pulses, states = detect_pulses({}, [S("a", "a", "done")])
        self.assertEqual(pulses, [])
        self.assertEqual(states, {"a": "done"})

    def test_running_to_done_pulses_and_to_running_does_not(self):
        pulses, _ = detect_pulses({"a": "running"}, [S("a", "a", "done")])
        self.assertEqual([(s.session_id, st) for s, st in pulses], [("a", "done")])
        pulses, _ = detect_pulses({"a": "done"}, [S("a", "a", "running")])
        self.assertEqual(pulses, [])

    def test_waiting_failed_stopped_pulse(self):
        for st in ("waiting", "failed", "stopped"):
            pulses, _ = detect_pulses({"a": "running"}, [S("a", "a", st)])
            self.assertEqual(len(pulses), 1, st)


class EffectsTest(unittest.TestCase):
    def test_sweep_moves_left_to_right_and_ends(self):
        e = Effects(cols=5)
        e.pulse(10.0, (1, 2, 3))
        self.assertTrue(e.active(10.0))
        self.assertTrue(e.overlays(10.0, 0))                              # the band starts on column 0
        self.assertFalse(e.overlays(10.0, 1))                             # column 1 not yet
        self.assertTrue(e.overlays(10.13, 0))                             # pos ~1.08: col 0 still lit, fading
        self.assertFalse(e.overlays(10.13, 4))                            # col 4 not yet
        self.assertTrue(e.overlays(10.59, 4))                             # last column at the end
        self.assertFalse(e.active(11.1))                                  # gone after 1 s

    def test_glow_only_on_its_key_and_fades(self):
        e = Effects()
        e.pulse(0.0, (9, 9, 9), key_index=7)
        glow = [o for o in e.overlays(0.5, 7) if o[0] == "glow"]
        self.assertEqual(len(glow), 1)
        self.assertAlmostEqual(glow[0][2], 0.5, places=2)
        self.assertEqual([o for o in e.overlays(0.5, 8) if o[0] == "glow"], [])


class ScrollTest(unittest.TestCase):
    def test_fits_never_scrolls(self):
        self.assertEqual(scroll_offset(5.0, 50, 66), 0)

    def test_pause_then_scroll_then_repeat(self):
        w, view = 100, 66
        self.assertEqual(scroll_offset(1.0, w, view), 0)          # pausing
        self.assertEqual(scroll_offset(3.0, w, view), 30)         # 1 s after the pause at 30 px/s
        travel = w + 24
        cycle = 2.0 + travel / 30.0
        self.assertEqual(scroll_offset(cycle + 0.5, w, view), 0)  # back at the start
        self.assertEqual(scroll_offset(3.05, w, view) % 2, 0)     # quantised


class BreathTest(unittest.TestCase):
    def test_range_and_quantisation(self):
        values = {breath(t / 100.0) for t in range(0, 300)}
        self.assertGreaterEqual(min(values), 0.16)
        self.assertLessEqual(max(values), 0.30)
        self.assertLessEqual(len(values), 13)


if __name__ == "__main__":
    unittest.main()
