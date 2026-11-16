import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import todokeys  # noqa: E402
from todo import Item  # noqa: E402


class FakeList:
    path = "/home/x/lists/todo.md"
    soon_days = 0


class ItemPressTest(unittest.TestCase):
    def launches(self, link, cfg=None):
        calls = []
        todokeys.launch = calls.append
        key = todokeys.TodoItemKey(Item(title="t", due=None, link=link, line=1), FakeList(), cfg or {})
        key.press()
        return calls

    def test_web_link_opens_in_the_browser(self):
        self.assertEqual(self.launches("https://example.com/a?b=(c)"), ["xdg-open 'https://example.com/a?b=(c)'"])

    def test_relative_file_opens_next_to_the_list(self):
        self.assertEqual(self.launches("notes/b.md"), ["code /home/x/lists/notes/b.md"])

    def test_absolute_file_uses_open_with(self):
        self.assertEqual(self.launches("/tmp/x.md", {"open_with": "gedit"}), ["gedit /tmp/x.md"])

    def test_no_link_does_nothing(self):
        self.assertEqual(self.launches(None), [])


if __name__ == "__main__":
    unittest.main()
