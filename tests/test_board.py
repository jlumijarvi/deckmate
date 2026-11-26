import os
import sys
import tempfile
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from board import PressTracker, SessionView, StateStore  # noqa: E402


@dataclass
class S:
    session_id: str
    name: str
    state: str


class Plain:
    holdable = False


class Holdable:
    holdable = True


class FilterTest(unittest.TestCase):
    def view(self, *states):
        v = SessionView(5)
        v.update([S(f"s{i}", f"n{i}", st) for i, st in enumerate(states)])
        return v

    def test_filter_narrows_and_toggles_off(self):
        v = self.view("waiting", "done", "done")
        v.toggle_filter("done")
        self.assertEqual([s.state for s in v.shown()], ["done", "done"])
        v.toggle_filter("done")
        self.assertIsNone(v.filter)
        self.assertEqual(len(v.shown()), 3)

    def test_zero_counter_does_not_filter(self):
        v = self.view("waiting", "done")
        v.toggle_filter("running")
        self.assertIsNone(v.filter)
        self.assertEqual(len(v.shown()), 2)

    def test_filter_clears_when_its_state_empties(self):
        v = self.view("waiting", "done")
        v.toggle_filter("waiting")
        self.assertEqual(v.filter, "waiting")
        v.update([S("s0", "n0", "done"), S("s1", "n1", "done")])
        self.assertIsNone(v.filter)
        self.assertEqual(len(v.shown()), 2)


class PressTrackerTest(unittest.TestCase):
    def test_plain_key_taps_on_press(self):
        t = PressTracker(0.6)
        k = Plain()
        self.assertEqual(t.feed(3, k, True, 10.0), [("press", 3, k)])
        self.assertEqual(t.feed(3, k, False, 10.1), [])

    def test_holdable_short_press_taps_on_release(self):
        t = PressTracker(0.6)
        k = Holdable()
        self.assertEqual(t.feed(1, k, True, 10.0), [])
        self.assertEqual(t.tick(10.3), [])
        self.assertEqual(t.feed(1, k, False, 10.4), [("press", 1, k)])

    def test_holdable_long_press_holds_once(self):
        t = PressTracker(0.6)
        k = Holdable()
        t.feed(1, k, True, 10.0)
        self.assertEqual(t.tick(10.5), [])
        self.assertEqual(t.tick(10.7), [("hold", 1, k)])
        self.assertEqual(t.tick(11.0), [])                      # fires once
        self.assertEqual(t.feed(1, k, False, 11.1), [("release-after-hold", 1, k)])

    def test_two_keys_independent(self):
        t = PressTracker(0.6)
        a, b = Holdable(), Holdable()
        t.feed(1, a, True, 0.0)
        t.feed(2, b, True, 0.5)
        self.assertEqual(t.tick(0.7), [("hold", 1, a)])
        self.assertEqual(t.feed(2, b, False, 0.8), [("press", 2, b)])


def sessions(*specs):
    return [S(f"id{i}", name, state) for i, (name, state) in enumerate(specs)]


class SessionViewTest(unittest.TestCase):
    def test_sort_order(self):
        v = SessionView(5)
        v.update(sessions(("b", "done"), ("a", "running"), ("c", "waiting"), ("d", "failed"), ("e", "stopped")))
        self.assertEqual([s.state for s in v.shown()], ["waiting", "running", "done", "stopped", "failed"])

    def test_dismiss_hides_until_state_changes(self):
        v = SessionView(5)
        v.update(sessions(("x", "done")))
        self.assertTrue(v.dismiss(v.sessions[0]))
        self.assertEqual(v.shown(), [])
        self.assertEqual(v.count("done"), 0)
        self.assertEqual(len(v.dismissed_sessions()), 1)
        # it runs again: back on the board
        v.update([S("id0", "x", "running")])
        self.assertEqual(len(v.shown()), 1)
        self.assertEqual(v.dismissed, {})
        # and when that run ends it shows as done like any other
        v.update([S("id0", "x", "done")])
        self.assertEqual(len(v.shown()), 1)

    def test_running_cannot_be_dismissed(self):
        v = SessionView(5)
        v.update(sessions(("x", "running")))
        self.assertFalse(v.dismiss(v.sessions[0]))
        self.assertEqual(len(v.shown()), 1)

    def test_dismiss_all_done_and_restore(self):
        v = SessionView(5)
        v.update(sessions(("a", "done"), ("b", "done"), ("c", "waiting"), ("d", "stopped")))
        self.assertEqual(v.dismiss_all_done(), 2)
        self.assertEqual([s.state for s in v.shown()], ["waiting", "stopped"])
        self.assertTrue(v.restore("id0"))
        self.assertEqual(len(v.shown()), 3)
        self.assertFalse(v.restore("nope"))

    def test_vanished_session_drops_dismissal(self):
        v = SessionView(5)
        v.update(sessions(("a", "done")))
        v.dismiss(v.sessions[0])
        v.update([])
        self.assertEqual(v.dismissed, {})

    def test_filter_toggle(self):
        v = SessionView(5)
        v.update(sessions(("a", "done"), ("b", "running"), ("c", "waiting")))
        v.toggle_filter("running")
        self.assertEqual([s.name for s in v.shown()], ["b"])
        v.toggle_filter("running")
        self.assertEqual(len(v.shown()), 3)
        v.toggle_filter("waiting")
        v.toggle_filter("done")           # switching filters, not stacking
        self.assertEqual([s.name for s in v.shown()], ["a"])

    def test_paging(self):
        v = SessionView(2)
        v.update(sessions(("a", "done"), ("b", "done"), ("c", "done"), ("d", "done"), ("e", "done")))
        self.assertEqual(v.page_count(), 3)
        self.assertEqual([s.name for s in v.visible()], ["a", "b"])
        v.turn(1)
        self.assertEqual([s.name for s in v.visible()], ["c", "d"])
        v.turn(1)
        self.assertEqual([s.name if s else None for s in v.visible()], ["e", None])
        v.turn(1)                                   # stays on the last page
        self.assertEqual(v.page, 2)
        v.turn(-5)
        self.assertEqual(v.page, 0)
        # fewer sessions: the page index is clamped
        v.turn(2)
        v.update(sessions(("a", "done")))
        self.assertEqual(v.page, 0)

    def test_state_store_round_trip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "state.json")
        store = StateStore(path)
        v = SessionView(5, store)
        v.update(sessions(("a", "done")))
        v.dismiss(v.sessions[0])
        again = SessionView(5, StateStore(path))
        again.update(sessions(("a", "done")))
        self.assertEqual(again.shown(), [])


class PinTest(unittest.TestCase):
    def view(self):
        v = SessionView(5)
        v.update(sessions(("b", "done"), ("a", "running"), ("c", "waiting")))
        return v

    def test_pinned_sorts_first_and_cannot_be_hidden(self):
        v = self.view()
        b = next(s for s in v.sessions if s.name == "b")
        self.assertTrue(v.toggle_pin(b))
        self.assertEqual([s.name for s in v.shown()], ["b", "c", "a"])
        self.assertFalse(v.dismiss(b))
        self.assertEqual(v.dismiss_all_done(), 0)
        self.assertTrue(v.is_pinned(b))
        self.assertFalse(v.toggle_pin(b))
        self.assertEqual([s.name for s in v.shown()], ["c", "a", "b"])

    def test_pinned_session_stays_as_gone_until_unpinned(self):
        a, b = S("ida", "a", "running"), S("idb", "b", "done")
        v = SessionView(5)
        v.update([a, b])
        v.toggle_pin(b)
        v.update([a])
        gone = v.shown()[0]
        self.assertEqual((gone.name, gone.state, gone.session_id), ("b", "gone", "idb"))
        self.assertEqual(v.count("done"), 0)                  # counters only count live sessions
        self.assertFalse(v.dismiss(gone))
        v.update([a, b])                                      # back alive: a live session again
        self.assertEqual([s.state for s in v.shown()], ["done", "running"])
        v.update([a])
        v.toggle_pin(v.shown()[0])                            # unpin the gone one: it leaves at once
        self.assertEqual([s.name for s in v.shown()], ["a"])

    def test_pin_mode_times_out_and_pins_persist(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = StateStore(os.path.join(tmp, "state.json"))
            v = SessionView(5, store)
            v.update(sessions(("b", "done")))
            v.start_pin_mode(100.0)
            self.assertTrue(v.pin_mode(104.9))
            self.assertFalse(v.pin_mode(105.0))
            v.toggle_pin(v.shown()[0])
            again = SessionView(5, StateStore(os.path.join(tmp, "state.json")))
            again.update([])
            self.assertEqual([(s.name, s.state) for s in again.shown()], [("b", "gone")])
            self.assertEqual(again.pinned["id0"]["name"], "b")


@dataclass
class Timed:
    session_id: str
    name: str
    state: str
    since: float


class AutoHideTest(unittest.TestCase):
    def view(self, store=None, now=10_000.0):
        self.now = now
        return SessionView(5, store, hide_done_after=3600, clock=lambda: self.now)

    def test_done_long_enough_is_hidden_and_returns_when_it_runs(self):
        v = self.view()
        old, fresh = Timed("old", "old", "done", 1000.0), Timed("new", "new", "done", 9000.0)
        asking = Timed("ask", "ask", "waiting", 1000.0)
        v.update([old, fresh, asking])
        self.assertEqual([s.name for s in v.shown()], ["ask", "new"])
        self.assertEqual([s.name for s in v.dismissed_sessions()], ["old"])
        self.assertEqual(v.count("done"), 1)
        self.now = 13_000.0                                    # an hour after "new" finished
        v.update([old, fresh, asking])
        self.assertEqual([s.name for s in v.shown()], ["ask"])
        v.update([Timed("old", "old", "running", 13_000.0), fresh, asking])
        self.assertEqual([s.name for s in v.shown()], ["ask", "old"])

    def test_brought_back_by_hand_stays_until_its_state_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            v = self.view(StateStore(path))
            old = Timed("old", "old", "done", 1000.0)
            v.update([old])
            self.assertEqual(v.shown(), [])
            v.restore("old")
            v.update([old])
            self.assertEqual([s.name for s in v.shown()], ["old"])
            again = self.view(StateStore(path))                # a restart remembers it
            again.update([old])
            self.assertEqual([s.name for s in again.shown()], ["old"])
            again.update([Timed("old", "old", "running", 10_000.0)])
            again.update([Timed("old", "old", "done", 1000.0)])   # a new turn ended long ago: hidden again
            self.assertEqual(again.shown(), [])

    def test_pinned_is_never_hidden_and_off_means_off(self):
        v = self.view()
        old = Timed("old", "old", "done", 1000.0)
        v.hide_done_after = None
        v.update([old])
        self.assertEqual(len(v.shown()), 1)
        v.toggle_pin(old)
        v.hide_done_after = 3600
        v.update([old])
        self.assertEqual(len(v.shown()), 1)

    def test_setting_from_the_layout(self):
        from layout import hide_done_seconds
        self.assertEqual(hide_done_seconds({"sessions": {"hide_done_h": 2}}), 7200)
        for cfg in ({}, {"sessions": {}}, {"sessions": {"hide_done_h": 0}}, {"sessions": {"hide_done_h": "x"}},
                    {"sessions": {"hide_done_h": -1}}):
            self.assertIsNone(hide_done_seconds(cfg))


if __name__ == "__main__":
    unittest.main()
