import json
import unittest
from pathlib import Path

from helpers import core, make_project

import cd_ai_api as ai_api


class ApiRun(unittest.TestCase):
    def test_answer_is_validated(self):
        task = {"source_lang": "en", "target_lang": "pl"}
        inputs = {"f|1": {"en": "Press {Key:A}.", "pl": "Wciśnij."}, "f|2": {"en": "Hi.", "pl": "Hej."}}
        answer = "\n".join([
            "Here you go:",
            json.dumps({"id": "f|1", "pl": "Wciśnij {Key:A}.", "reason": "missing button"}),
            json.dumps({"id": "f|2", "pl": "Cześć™"}),  # character outside the font
            json.dumps({"id": "f|9", "pl": "?"}),  # unknown id
        ])
        results, rejected = ai_api.parse_answer(answer, inputs, task, "Wciśnij {Key:A}. Cześć")
        self.assertEqual([r["id"] for r in results], ["f|1"])
        self.assertEqual(rejected, 2)

    def test_run_task_with_fake_model(self):
        p = make_project({"questdialog.paloc": [("k1", "Hello.", "Siema.", "Siema."), ("k2", "Bye.", "Pa.", "Pa.")]})
        core.AI_TASKS_DIR, old = Path(p.path) / "tasks", core.AI_TASKS_DIR
        try:
            out, n = core.generate_ai_task(p, ["questdialog.paloc"], "all", 200)
        finally:
            core.AI_TASKS_DIR = old
        sent = []

        def fake(provider, model, key, system, prompt, url=None):
            sent.append(prompt)
            return json.dumps({"id": "questdialog.paloc|k1", "pl": "Witaj.", "reason": "tone"})

        ai_api.complete, real = fake, ai_api.complete
        try:
            parts, fixes, rejected = ai_api.run_task(out, "anthropic", "m", "key", log=lambda _t: None)
        finally:
            ai_api.complete = real
        self.assertEqual((parts, fixes, rejected), (1, 1, 0))
        self.assertIn("Hello.", sent[0])
        applied, problems = core.import_ai_results(p, out)
        self.assertEqual((applied, problems), (1, []))
        # a second run skips parts that already have results
        self.assertEqual(ai_api.run_task(out, "anthropic", "m", "key", log=lambda _t: None)[0], 0)


if __name__ == "__main__":
    unittest.main()
