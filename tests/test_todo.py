import os
import sys
import tempfile
import unittest
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import todo  # noqa: E402

TODAY = date(2026, 11, 14)


class ParseTest(unittest.TestCase):
    def test_parse_fields_and_order(self):
        text = "\n".join([
            f"- [ ] Renew passport  @{(TODAY + timedelta(6)).isoformat()}  [notes](~/n/passport.md)",
            f"- [ ] Call the garage @{TODAY.isoformat()}",
            f"- [ ] Late thing @{(TODAY - timedelta(3)).isoformat()}",
            "- [ ] Bare path ~/n/backup.md",
            "* [ ] Undated - with - dashes",
            "- [x] Done",
            "- [ ]",
            "not an item",
        ])
        items = todo.parse(text)
        self.assertEqual([i.title for i in items],
                         ["Late thing", "Call the garage", "Renew passport", "Bare path", "Undated - with - dashes"])
        self.assertEqual(items[2].link, "~/n/passport.md")
        self.assertEqual(items[3].link, "~/n/backup.md")
        self.assertIsNone(items[4].due)

    def test_urgency(self):
        t = todo.TodoList("/nonexistent", soon_days=0)
        t.items = todo.parse(f"- [ ] a @{TODAY.isoformat()}\n- [ ] b")
        self.assertEqual(t.urgency(TODAY), "soon")
        t.items = todo.parse(f"- [ ] a @{(TODAY - timedelta(1)).isoformat()}")
        self.assertEqual(t.urgency(TODAY), "overdue")
        t.items = todo.parse("- [ ] b")
        self.assertEqual(t.urgency(TODAY), "ok")
        t.items = []
        self.assertEqual(t.urgency(TODAY), "empty")

    def test_soon_days_window(self):
        t = todo.TodoList("/nonexistent", soon_days=2)
        t.items = todo.parse(f"- [ ] a @{(TODAY + timedelta(2)).isoformat()}")
        self.assertEqual(t.urgency(TODAY), "soon")

    def test_due_text(self):
        self.assertEqual(todo.due_text(TODAY, TODAY), "today")
        self.assertEqual(todo.due_text(TODAY + timedelta(1), TODAY), "tomorrow")
        self.assertEqual(todo.due_text(TODAY - timedelta(3), TODAY), "3d late")
        self.assertEqual(todo.due_text(TODAY + timedelta(3), TODAY), "Tue 17.11.")


class MarkDoneTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "todo.md")

    def test_marks_the_right_line(self):
        with open(self.path, "w") as f:
            f.write("# list\n- [ ] First @2026-11-20\n- [ ] Second  [n](a.md)\n- [x] Old\n")
        t = todo.TodoList(self.path)
        t.refresh()
        second = next(i for i in t.items if i.title == "Second")
        self.assertTrue(t.mark_done(second))
        self.assertIn("- [x] Second  [n](a.md)", open(self.path).read())
        t.refresh()
        self.assertEqual([i.title for i in t.items], ["First"])

    def test_refuses_when_file_changed(self):
        with open(self.path, "w") as f:
            f.write("- [ ] First\n")
        t = todo.TodoList(self.path)
        t.refresh()
        first = t.items[0]
        with open(self.path, "w") as f:
            f.write("- [ ] Other\n- [ ] First\n")
        self.assertFalse(t.mark_done(first))
        self.assertNotIn("[x]", open(self.path).read())



class LinkTest(unittest.TestCase):
    def test_web_link_with_parentheses(self):
        items = todo.parse("- [ ] Search [list](https://example.com/search?q=owner()) now\n")
        self.assertEqual(items[0].link, "https://example.com/search?q=owner()")
        self.assertEqual(items[0].title, "Search now")

    def test_plain_web_link_and_file_link(self):
        items = todo.parse("- [ ] A [docs](https://example.com/page)\n- [ ] B [n](notes/b.md)\n")
        self.assertEqual([i.link for i in items], ["https://example.com/page", "notes/b.md"])


class MtimeTest(unittest.TestCase):
    def test_list_remembers_when_the_file_was_written(self):
        import tempfile
        from todo import TodoList
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "todo.md")
            with open(path, "w") as f:
                f.write("- [ ] one\n")
            t = TodoList(path)
            self.assertIsNone(t.mtime)
            t.refresh()
            self.assertAlmostEqual(t.mtime, os.path.getmtime(path))


if __name__ == "__main__":
    unittest.main()
