"""Translation patches: small, shareable files with only the corrected lines.

A patch is a JSON Lines file (UTF-8). The first line is a header, every
other line is one corrected entry, sorted by id, so patches diff and merge
well in git:

    {"format": "cd-localization-patch", "version": 1, "source_lang": "en", "target_lang": "pl", ...}
    {"id": "questdialog.paloc|questdialog_main_50831", "text": "...", "src": "1a2b3c4d", "reviewed": true}

"src" is a short hash of the original-language line the correction was made
for. When a game update changes that line, the correction is skipped instead
of being applied to a different sentence.
"""

import datetime
import hashlib
import json
from pathlib import Path

import cd_core as core
from i18n import tr

FORMAT = "cd-localization-patch"
VERSION = 1
SUFFIX = ".cdpatch.jsonl"


def text_hash(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]


def export_patch(project, path, author="", only_reviewed=False):
    """Write every edited entry of the project into a patch file. Returns the number of lines."""
    lines = []
    for name in project.files:
        for e in project.load_file(name):
            if e["target"] == e["original"] or (only_reviewed and not e.get("reviewed")):
                continue
            rec = {"id": f"{name}|{e['key']}", "text": e["target"], "src": text_hash(e["source"])}
            if e.get("reviewed"):
                rec["reviewed"] = True
            lines.append(rec)
    lines.sort(key=lambda r: r["id"])
    header = {
        "format": FORMAT,
        "version": VERSION,
        "source_lang": project.src_lang,
        "target_lang": project.tgt_lang,
        "target_folder": project.tgt_folder,
        "author": author,
        "created": datetime.date.today().isoformat(),
        "count": len(lines),
    }
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for rec in [header] + lines:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(lines)


def read_patch(path):
    """(header, [entries]) of a patch file."""
    with open(path, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    if not rows or rows[0].get("format") != FORMAT:
        raise ValueError(tr("patch.not_patch", path=Path(path).name))
    if rows[0].get("version", 0) > VERSION:
        raise ValueError(tr("patch.too_new"))
    return rows[0], rows[1:]


def _group(entries):
    by_file = {}
    for rec in entries:
        name, key = rec["id"].split("|", 1)
        by_file.setdefault(name, {})[key] = rec
    return by_file


def _find_folder(game, lang):
    for r in core.scan_game(game):
        if r["lang"] == lang:
            return r["folder"]
    return None


def plan_patch(game_dir, path, log=print):
    """Check a patch against the installed game.

    Returns (header, target folder, {file: {key: text}} to apply, number of outdated lines skipped).
    """
    header, entries = read_patch(path)
    game = Path(game_dir)
    tgt_folder = header.get("target_folder")
    if not tgt_folder or not (game / tgt_folder / "0.pamt").is_file():
        tgt_folder = _find_folder(game, header["target_lang"])
    src_folder = _find_folder(game, header["source_lang"])
    if not tgt_folder or not src_folder:
        raise RuntimeError(tr("patch.no_language", lang=core.lang_name(header["target_lang"])))
    src_entries = core.list_paloc_entries(game / src_folder)
    texts, skipped = {}, 0
    for name, recs in _group(entries).items():
        if name not in src_entries:
            skipped += len(recs)
            continue
        sources = core.read_texts(src_entries[name])
        for key, rec in recs.items():
            if key in sources and text_hash(sources[key]) == rec.get("src"):
                texts.setdefault(name, {})[key] = rec["text"]
            else:
                skipped += 1
    log(tr("patch.plan", n=sum(len(t) for t in texts.values()), skipped=skipped, folder=tgt_folder))
    return header, tgt_folder, texts, skipped


def apply_patch(game_dir, path, log=print):
    """Install a patch into the game (with backups). Returns the number of changed files."""
    _, folder, texts, _ = plan_patch(game_dir, path, log)
    changes = core.compute_changes(game_dir, folder, texts)
    if not changes:
        log(tr("core.nothing_to_install"))
        return 0
    return core.write_changes(game_dir, folder, changes, log)


def import_into_project(project, path):
    """Load a patch (e.g. from another contributor) as suggestions to review. Returns (applied, skipped)."""
    header, entries = read_patch(path)
    if header["target_lang"] != project.tgt_lang or header["source_lang"] != project.src_lang:
        raise ValueError(tr("core.task_lang_mismatch"))
    who = header.get("author") or Path(path).name
    applied = skipped = 0
    for name, recs in _group(entries).items():
        if name not in project.files:
            skipped += len(recs)
            continue
        index = {e["key"]: e for e in project.load_file(name)}
        for key, rec in recs.items():
            e = index.get(key)
            if e is None or text_hash(e["source"]) != rec.get("src"):
                skipped += 1
                continue
            if rec["text"] == e["target"]:
                continue
            e["ai"] = rec["text"]
            e["ai_reason"] = tr("patch.from", author=who)
            core.refresh_issues(e)
            applied += 1
        project.dirty.add(name)
    project.save_all()
    return applied, skipped
