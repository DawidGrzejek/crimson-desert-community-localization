"""Builds small fake projects, so the tests need neither the game nor its texts."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cd_core as core  # noqa: E402
import i18n  # noqa: E402

i18n.set_language("en")


def make_project(entries_by_file, src="en", tgt="pl"):
    """entries_by_file: {file: [(key, source, target, original)]} -> core.Project in a temp folder."""
    root = Path(tempfile.mkdtemp())
    (root / "data").mkdir()
    meta = {"version": 1, "game_dir": str(root / "game"), "files": sorted(entries_by_file),
            "source": {"folder": "0020", "lang": src}, "target": {"folder": "0029", "lang": tgt}}
    (root / "project.json").write_text(json.dumps(meta), encoding="utf-8")
    for name, rows in entries_by_file.items():
        entries = [{"key": k, "source": s, "target": t, "original": o} for k, s, t, o in rows]
        (root / "data" / f"{name}.json").write_text(json.dumps({"file": name, "entries": entries}),
                                                     encoding="utf-8")
    return core.Project(root)
