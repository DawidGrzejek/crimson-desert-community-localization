import tempfile
import unittest
from pathlib import Path

from helpers import make_project

import cd_patch as patch


class Patches(unittest.TestCase):
    def setUp(self):
        self.author = make_project({"questdialog.paloc": [
            ("k1", "Hello.", "Witaj.", "Siema."),
            ("k2", "Bye.", "Pa.", "Pa."),
            ("k3", "Run!", "Uciekaj!", "Biegnij!"),
        ]})
        e = self.author.load_file("questdialog.paloc")[2]
        e["reviewed"] = True
        self.path = Path(tempfile.mkdtemp()) / f"fix{patch.SUFFIX}"

    def test_export_contains_only_corrections_sorted(self):
        n = patch.export_patch(self.author, self.path, "tester")
        header, rows = patch.read_patch(self.path)
        self.assertEqual(n, 2)
        self.assertEqual(header["author"], "tester")
        self.assertEqual([r["id"] for r in rows], ["questdialog.paloc|k1", "questdialog.paloc|k3"])
        self.assertTrue(rows[1]["reviewed"])

    def test_only_reviewed(self):
        self.assertEqual(patch.export_patch(self.author, self.path, only_reviewed=True), 1)

    def test_import_as_suggestions_skips_changed_originals(self):
        patch.export_patch(self.author, self.path)
        other = make_project({"questdialog.paloc": [
            ("k1", "Hello.", "Siema.", "Siema."),
            ("k3", "Run away!", "Biegnij!", "Biegnij!"),  # original changed since the patch was made
        ]})
        applied, skipped = patch.import_into_project(other, self.path)
        self.assertEqual((applied, skipped), (1, 1))
        self.assertEqual(other.load_file("questdialog.paloc")[0]["ai"], "Witaj.")

    def test_rejects_other_files(self):
        self.path.write_text('{"format": "something"}\n', encoding="utf-8")
        with self.assertRaises(ValueError):
            patch.read_patch(self.path)


if __name__ == "__main__":
    unittest.main()
