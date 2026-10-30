import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from todo import add, load, mark_done


class TodoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "tasks.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_add(self):
        task = add("buy bread", self.path)
        self.assertEqual(task["id"], 1)
        self.assertFalse(task["done"])

    def test_list(self):
        add("one", self.path)
        self.assertEqual(len(load(self.path)), 1)

    def test_done(self):
        add("one", self.path)
        self.assertTrue(mark_done(1, self.path)["done"])


if __name__ == "__main__":
    unittest.main()
