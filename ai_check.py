#!/usr/bin/env python3
"""Checks the files in results/ before they are imported into the comparison program.

Run it in the task folder:  python3 check.py
"""

import collections
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOKEN_RE = re.compile(r"\{([^{}#]*)(#[^{}]*)?\}|<([^<>]*)>")

# (task file, data folder, results folder, allowed characters); the second
# layout belongs to tasks created by older, Polish-only versions.
LAYOUTS = (
    ("task.json", "data", "results", "allowed_chars.txt"),
    ("zadanie.json", "dane", "wyniki", "dozwolone_znaki.txt"),
)


def structure(text):
    tokens = collections.Counter()
    for m in TOKEN_RE.finditer(text):
        if m.group(3) is not None:
            tag = m.group(3).replace(" ", "").lower()
            if not tag.startswith("br"):
                tokens[f"<{tag}>"] += 1
        else:
            tokens["{" + m.group(1).strip().lower() + ("#…" if m.group(2) else "") + "}"] += 1
    return tokens


def main():
    meta, data_dir, results_dir, charset = next(
        (layout for layout in LAYOUTS if (HERE / layout[0]).is_file()), LAYOUTS[0])
    task = json.loads((HERE / meta).read_text(encoding="utf-8"))
    src, tgt = task["source_lang"], task["target_lang"]
    allowed = set((HERE / charset).read_text(encoding="utf-8")) | {"\n"}

    inputs = {}
    for path in sorted((HERE / data_dir).glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                inputs[rec["id"]] = rec

    errors = 0
    changed = 0
    done = set()
    for path in sorted((HERE / results_dir).glob("*.jsonl")):
        done.add(path.name)
        seen = set()
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            where = f"{path.name}:{n}"

            def bad(msg):
                nonlocal errors
                errors += 1
                print(f"ERROR {where}: {msg}")  # noqa: B023 – called within the same iteration

            try:
                rec = json.loads(line)
            except ValueError as e:
                bad(f"invalid JSON ({e})")
                continue
            if not isinstance(rec, dict) or "id" not in rec or tgt not in rec:
                bad(f'required fields: "id" and "{tgt}"')
                continue
            rid, text = rec["id"], rec[tgt]
            if rid not in inputs:
                bad(f"unknown id {rid!r}")
                continue
            if rid in seen:
                bad(f"id {rid!r} repeated in the file")
            seen.add(rid)
            if not isinstance(text, str) or not text.strip():
                bad("empty translation")
                continue
            source = inputs[rid][src]
            missing = structure(source) - structure(text)
            extra = structure(text) - structure(source)
            if missing:
                bad(f"{rid}: missing tags {sorted(missing)}")
            if extra:
                bad(f"{rid}: extra tags {sorted(extra)}")
            if text.count("{") != text.count("}"):
                bad(f"{rid}: unclosed brace {{ }}")
            odd = sorted(set(text) - allowed)
            if odd:
                bad(f"{rid}: characters outside the allowed set: {''.join(odd)!r}")
            if text == inputs[rid][tgt]:
                print(f"NOTE {where}: {rid} – text identical to the current one, this line can be removed")
            changed += 1

    missing_parts = [p for p in task["parts"] if p not in done]
    print()
    print(f"Corrections: {changed}, errors: {errors}")
    if missing_parts:
        print(f"Parts without a results file ({len(missing_parts)}): {', '.join(missing_parts[:10])}"
              + (" …" if len(missing_parts) > 10 else ""))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
