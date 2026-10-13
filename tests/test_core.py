import struct
import unittest

from helpers import core, make_project


class PalocFormat(unittest.TestCase):
    def test_round_trip(self):
        records = [(b"\x01" * 8, "key_1", "Hello"), (b"\x02" * 8, "key_2", "Zażółć gęślą jaźń <br/>")]
        raw = core.build_paloc(records, b"TAIL")
        parsed, tail = core.parse_paloc(raw)
        self.assertEqual(parsed, records)
        self.assertEqual(tail, b"TAIL")

    def test_container(self):
        raw = core.build_paloc([(b"\x00" * 8, "k", "text " * 50)], b"")
        wrapped = core.wrap_paloc(raw)
        self.assertTrue(wrapped.startswith(b"paloc"))
        self.assertEqual(core.unwrap_paloc(wrapped), (raw, True))
        self.assertEqual(core.unwrap_paloc(raw), (raw, False))


class Checks(unittest.TestCase):
    def test_tags(self):
        src = "Press {Key:Key_Jump} to see {StaticInfo:Knowledge:K_1#the tower}."
        errors, _ = core.check_entry(src, "Naciśnij {Key:Key_Jump}, aby zobaczyć {StaticInfo:Knowledge:K_1#wieżę}.")
        self.assertEqual(errors, [])
        errors, _ = core.check_entry(src, "Naciśnij, aby zobaczyć wieżę.")
        self.assertEqual(len(errors), 1)
        self.assertIn("{key:key_jump}", errors[0])

    def test_empty_and_untranslated(self):
        self.assertEqual(core.check_entry("Hello", " ")[0], ["empty translation"])
        self.assertTrue(core.check_entry("Hello there", "Hello there")[1])

    def test_visible_text(self):
        self.assertEqual(core.visible_text("Go to {StaticInfo:Region:R#the fort}<br/>now {Key:K}"), "Go to the fort now")

    def test_timing(self):
        e = {"source": "Short line of text.", "_dur": 1.0}
        self.assertEqual(core.timing_warnings(e, "Krótka linia."), [])
        warnings = core.timing_warnings(e, "Bardzo, bardzo długa linijka tekstu, której nikt nie przeczyta.")
        self.assertEqual(len(warnings), 2)  # much longer and too fast to read

    def test_voice_warning(self):
        e = {"_heard": {"text": "something else", "ratio": 0.2}}
        self.assertIn("something else", core.voice_warning(e))
        self.assertIsNone(core.voice_warning({"_heard": {"text": "same", "ratio": 0.95}}))


class GameUpdates(unittest.TestCase):
    def test_new_entry(self):
        e = core.merge_entry(None, "k", "Hi", "Cześć")
        self.assertEqual((e["target"], e["original"]), ("Cześć", "Cześć"))

    def test_installed_edit_is_not_an_official_change(self):
        old = {"key": "k", "source": "Hi", "target": "Hej", "original": "Cześć"}
        e = core.merge_entry(old, "k", "Hi", "Hej")
        self.assertEqual((e["target"], e["original"]), ("Hej", "Cześć"))
        self.assertNotIn("outdated", e)

    def test_official_change_of_unedited_line(self):
        old = {"key": "k", "source": "Hi", "target": "Cześć", "original": "Cześć"}
        e = core.merge_entry(old, "k", "Hi", "Witaj")
        self.assertEqual((e["target"], e["original"]), ("Witaj", "Witaj"))

    def test_changed_original_marks_correction_outdated(self):
        old = {"key": "k", "source": "Hi", "target": "Hej", "original": "Cześć", "reviewed": True}
        e = core.merge_entry(old, "k", "Hello there", "Cześć")
        self.assertTrue(e["outdated"])
        self.assertEqual(e["old_source"], "Hi")
        self.assertEqual(e["target"], "Hej")
        self.assertTrue(e["reviewed"])


class ProjectFeatures(unittest.TestCase):
    def test_neighbours_and_stats(self):
        p = make_project({"questdialog.paloc": [(f"q_{i}", f"Line {i}", f"Linia {i}", f"Linia {i}") for i in range(6)]})
        before, after = p.neighbours("questdialog.paloc", "q_3")
        self.assertEqual([e["key"] for e in before], ["q_1", "q_2"])
        self.assertEqual([e["key"] for e in after], ["q_4", "q_5"])
        self.assertEqual(p.file_stats("questdialog.paloc")["total"], 6)

    def test_terminology(self):
        link = "{StaticInfo:Knowledge:Fort_1#Fort Ironclad}"
        p = make_project({"questdialog.paloc": [
            ("a", f"Go to {link}.", "Idź do {StaticInfo:Knowledge:Fort_1#Fortu Żelaznego}.", ""),
            ("b", f"Defend {link}!", "Bronić {StaticInfo:Knowledge:Fort_1#Fortu Żelaznego}!", ""),
            ("c", f"Back to {link}.", "Wróć do {StaticInfo:Knowledge:Fort_1#Fortu Pancernego}.", ""),
            ("d", "Fort Ironclad has fallen.", "Fort Pancerny upadł.", ""),
        ]})
        report = core.terminology_report(p)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["expected"], "Fortu Żelaznego")
        self.assertIn("Fortu Pancernego", report[0]["variants"])
        self.assertIn(("questdialog.paloc", "d"), report[0]["entries"])


class Audio(unittest.TestCase):
    def test_wem_duration(self):
        import cd_audio
        fmt = bytearray(66)
        struct.pack_into("<I", fmt, 4, 48000)
        struct.pack_into("<I", fmt, 24, 96000)
        header = b"RIFF" + b"\0" * 4 + b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + bytes(fmt)
        self.assertEqual(cd_audio.wem_duration(header), 2.0)
        self.assertIsNone(cd_audio.wem_duration(b"OggS"))

    def test_similarity(self):
        import cd_stt
        self.assertEqual(cd_stt.similarity("Hello, {Key:K} there!", "hello there"), 1.0)
        self.assertLess(cd_stt.similarity("We march at dawn.", "Bring me the ale."), 0.3)


if __name__ == "__main__":
    unittest.main()
