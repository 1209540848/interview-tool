import os
import tempfile
import unittest

from interview_tool.notes import list_markdown_notes, read_markdown_note


class NotesTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.notes_dir = self.tempdir.name

    def tearDown(self):
        self.tempdir.cleanup()

    def _write(self, relative, content):
        path = os.path.join(self.notes_dir, relative)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_lists_markdown_recursively_and_ignores_other_files(self):
        self._write("B.md", "b")
        self._write("folder/a.markdown", "a")
        self._write("ignored.txt", "x")
        self._write(".hidden/secret.md", "secret")

        notes = list_markdown_notes(self.notes_dir)

        self.assertEqual([item["label"] for item in notes],
                         ["B.md", "folder/a.markdown"])

    def test_reads_utf8_and_truncates_large_notes(self):
        path = self._write("中文.md", "测试内容")
        self.assertEqual(read_markdown_note(path, self.notes_dir), "测试内容")

        long_path = self._write("long.md", "abcdef")
        content = read_markdown_note(long_path, self.notes_dir, max_chars=3)
        self.assertTrue(content.startswith("abc"))
        self.assertIn("未显示", content)

    def test_rejects_paths_outside_notes_directory(self):
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as handle:
            outside = handle.name
        try:
            with self.assertRaises(ValueError):
                read_markdown_note(outside, self.notes_dir)
        finally:
            os.unlink(outside)


if __name__ == "__main__":
    unittest.main()
