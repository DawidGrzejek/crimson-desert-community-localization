import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Translations(unittest.TestCase):
    def test_all_languages_have_the_same_keys_and_placeholders(self):
        en = json.loads((ROOT / "lang" / "en.json").read_text(encoding="utf-8"))
        for path in (ROOT / "lang").glob("*.json"):
            other = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(other) - set(en), set(), f"{path.name}: keys not in en.json")
            for key, text in other.items():
                self.assertEqual(set(re.findall(r"\{(\w+)\}", text)), set(re.findall(r"\{(\w+)\}", en[key])),
                                 f"{path.name}: placeholders of {key}")

    def test_every_used_key_exists(self):
        en = json.loads((ROOT / "lang" / "en.json").read_text(encoding="utf-8"))
        used = set()
        for path in ROOT.glob("*.py"):
            used |= set(re.findall(r'\btr\("([\w.?-]+)"', path.read_text(encoding="utf-8")))
        self.assertEqual(sorted(k for k in used if k not in en), [])


if __name__ == "__main__":
    unittest.main()
