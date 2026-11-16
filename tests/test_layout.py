import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from deckhid import KEY_COUNT, KEY_PX  # noqa: E402
from layout import load_board  # noqa: E402
from pages import nav  # noqa: E402
from sessionkeys import DismissedPage, PagerKey, SessionSlot  # noqa: E402
from statskeys import StatsPage  # noqa: E402
from todokeys import TodoKey, TodoPage  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def every_type(todo_file):
    """A layout that uses every key type the board knows."""
    return {
        "brightness": 60,
        "keys": {
            "0": {"type": "clock"},
            "1": {"title": "A very long title that needs wrapping/and-shrinking", "color": "#1f6feb", "run": "true"},
            "2": {"type": "mute", "target": "source", "title": "Mic"},
            "3": {"type": "volume", "step": -5},
            "4": {"type": "slack"},
            "6": {"type": "media"},
            "7": {"type": "media", "action": "next"},
            "8": {"type": "brightness", "steps": [20, 80]},
            "9": {"type": "todo", "file": todo_file, "soon_days": 2},
            "10": {"type": "counter", "state": "waiting"},
            "11": {"type": "counter", "state": "done"},
            "12": {"type": "stats", "tiles": ["tokens", "cache", "context", "bogus"]},
            "13": {"type": "pager", "dir": "prev"},
            "14": {"type": "pager", "dir": "next"},
            "99": {"type": "command"},
        },
        "sessions": {"keys": [5]},
        "screensaver": {"image": "clock", "timeout_min": 5},
    }


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.todo = os.path.join(self.tmp.name, "todo.md")
        with open(self.todo, "w") as f:
            f.write("- [ ] First thing  @2026-01-01\n- [ ] Second [n](notes.md)\n- [x] Done\n")
        self.path = os.path.join(self.tmp.name, "keys.json")
        with open(self.path, "w") as f:
            json.dump(every_type(self.todo), f)

    def tearDown(self):
        self.tmp.cleanup()

    def render_all(self, keys):
        for i, k in enumerate(keys):
            for pressed in (False, True):
                img = k.render(pressed)
                self.assertEqual(img.size, (KEY_PX, KEY_PX), f"key {i} ({type(k).__name__})")

    def test_every_key_type_loads_and_renders(self):
        keys, brightness, providers, saver = load_board(self.path)
        self.assertEqual(len(keys), KEY_COUNT)
        self.assertEqual(brightness, 60)
        self.assertTrue(saver.enabled and saver.is_tiles)
        self.assertEqual([type(k).__name__ for k in keys[5:9]], ["SessionSlot", "Media", "Media", "Brightness"])
        self.assertIsInstance(keys[9], TodoKey)
        self.assertEqual(keys[9].todo.soon_days, 2)
        keys[9].todo.refresh()
        self.assertEqual(len(keys[9].todo.items), 2)
        self.render_all(keys)
        for k in keys:
            if k.holdable:
                self.assertEqual(k.render_held().size, (KEY_PX, KEY_PX))

    def test_pages_render(self):
        keys, *_ = load_board(self.path)
        nav.reset(keys)
        board = next(k for k in keys if isinstance(k, SessionSlot)).board
        for page in (StatsPage(board), StatsPage(board, keys[12].cfg["tiles"]), DismissedPage(board),
                     TodoPage(keys[9].todo, keys[9].cfg)):
            page_keys = page.keys()
            self.assertEqual(len(page_keys), KEY_COUNT)
            self.render_all(page_keys)

    def test_old_label_field_is_migrated_to_title(self):
        with open(self.path, "w") as f:
            json.dump({"keys": {"1": {"label": "Old", "run": "true"}, "2": {"type": "mute", "label": "Mic"}},
                       "todo": {"label": "Tasks"}}, f)
        keys, *_ = load_board(self.path)
        self.assertEqual(keys[1].cfg["title"], "Old")
        self.assertEqual(keys[2].cfg["title"], "Mic")
        with open(self.path) as f:
            text = f.read()
        self.assertNotIn("label", text)
        self.assertIn('"title": "Tasks"', text)

    def test_sample_list_only_for_the_default_file(self):
        other = os.path.join(self.tmp.name, "work.md")
        with open(self.path, "w") as f:
            json.dump({"keys": {"0": {"type": "todo", "file": other}}}, f)
        keys, *_ = load_board(self.path)
        self.assertFalse(os.path.exists(other))
        keys[0].todo.refresh()
        self.assertFalse(keys[0].todo.exists)
        self.assertEqual(keys[0].render(False).size, (KEY_PX, KEY_PX))   # draws "no file"

    def test_unknown_type_and_bad_index_are_ignored(self):
        with open(self.path, "w") as f:
            json.dump({"keys": {"3": {"type": "nonsense"}, "99": {"type": "clock"}}}, f)
        keys, *_ = load_board(self.path)
        self.assertEqual({type(k).__name__ for k in keys}, {"Blank"})

    def test_example_layout(self):
        keys, brightness, providers, saver = load_board(os.path.join(HERE, "keys.example.json"))
        self.assertEqual(len(keys), KEY_COUNT)
        self.assertIsInstance(keys[13], PagerKey)
        self.render_all(keys)

    def test_brightness_key_writes_to_the_layout_not_the_todo_file(self):
        keys, *_ = load_board(self.path)
        self.assertEqual(keys[8].level, 60)
        self.assertFalse(keys[14].holdable)    # the next pager has no hold any more
        with open(self.todo) as f:
            todo_before = f.read()
        keys[8].press()    # 60 -> 80 with the key's own steps
        with open(self.path) as f:
            self.assertEqual(json.load(f)["brightness"], 80)
        keys, *_ = load_board(self.path)
        self.assertEqual(keys[8].level, 80)
        keys[8].press()    # 80 -> wraps to 20
        with open(self.path) as f:
            self.assertEqual(json.load(f)["brightness"], 20)
        with open(self.todo) as f:
            self.assertEqual(f.read(), todo_before)

    def test_prev_pager_on_the_first_page_stays_put(self):
        keys, *_ = load_board(self.path)
        nav.reset(keys)
        board = keys[13].board
        keys[13].press()
        self.assertEqual(nav.stack, [])
        self.assertEqual(board.view.page, 0)
        keys[12].press()    # the stats key is the way to the stats page
        self.assertIsInstance(nav.stack[-1], StatsPage)


if __name__ == "__main__":
    unittest.main()
