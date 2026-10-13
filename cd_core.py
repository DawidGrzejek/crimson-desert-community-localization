"""Core logic for the Crimson Desert translation comparer (no GUI).

Reads .paloc text files straight from the game's PAZ archives, pairs two
languages by key, stores editable comparison projects as JSON, prepares
task packages for AI agents, and writes edited translations back into
the game.
"""

import collections
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from i18n import tr

BASE_DIR = Path(__file__).resolve().parent
TOOLS_DIR = BASE_DIR / "tools"
CONFIG_PATH = BASE_DIR / "translator_config.json"


def _data_dir(name, legacy):
    """Folder in BASE_DIR; keeps using the Polish name from older versions if it already exists."""
    old = BASE_DIR / legacy
    return old if old.is_dir() and not (BASE_DIR / name).exists() else BASE_DIR / name


PROJECTS_DIR = _data_dir("projects", "projekty")
AI_TASKS_DIR = _data_dir("ai_tasks", "ai_zadania")
BACKUPS_DIR = _data_dir("backups", "kopie_zapasowe")

sys.path.insert(0, str(TOOLS_DIR))
import lz4.block  # noqa: E402
from paz_parse import parse_pamt  # noqa: E402
from paz_crypto import decrypt  # noqa: E402
from paz_repack import repack_entry  # noqa: E402

GAME_DIR_NAME = "Crimson Desert"

# Names of the files in an AI task folder. Tasks created by older, Polish-only
# versions use the second layout and can still be continued and imported.
TASK_LAYOUTS = (
    {"meta": "task.json", "data": "data", "results": "results", "part": "part",
     "glossary": "glossary.tsv", "charset": "allowed_chars.txt", "check": "check.py",
     "instructions": "INSTRUCTIONS.md", "report": "REPORT.md",
     "issues": "issues", "note": "note", "reason": "reason"},
    {"meta": "zadanie.json", "data": "dane", "results": "wyniki", "part": "czesc",
     "glossary": "slownik.tsv", "charset": "dozwolone_znaki.txt", "check": "sprawdz.py",
     "instructions": "INSTRUKCJA.md", "report": "RAPORT.md",
     "issues": "uwagi", "note": "notatka", "reason": "powod"},
)


def task_layout(task_dir):
    """File names used by an existing AI task folder, or None if it is not one."""
    for layout in TASK_LAYOUTS:
        if (Path(task_dir) / layout["meta"]).is_file():
            return layout
    return None


def lang_name(code, lang=None):
    return tr(f"lang.{code}", lang)


def file_hint(name, lang=None):
    """Probable meaning of a text file, shown as a tooltip and used in the AI instructions."""
    hint = tr(f"file_hint.{name}", lang)
    return "" if hint == f"file_hint.{name}" else hint

# {Key:...}, {StaticInfo:...#display text}, <br/> and other tags
TOKEN_RE = re.compile(r"\{([^{}#]*)(#[^{}]*)?\}|<([^<>]*)>")

STOPWORDS = {
    "en": "the and of to you your is with for this that are be".split(),
    "pl": "się nie na jest że do to oraz lub jak po przez aby".split(),
    "de": "der die und das den ist sie zu mit nicht ein eine des".split(),
    "fr": "le les des et une est vous pour du avec dans au".split(),
    "es": "el los las y que para con del una por se al".split(),
    "it": "il di che per gli della delle non è nel con una".split(),
    "pt": "os não uma com para do da você são ao pelo".split(),
    "tr": "ve bir bu için ile olan veya daha çok".split(),
}
SPECIAL_CHARS = {
    "pl": "ąęłśźżćń",
    "tr": "ığş",
    "es": "ñ¿¡",
    "pt": "ãõ",
    "de": "ßäöü",
    "fr": "èêœ",
    "it": "òù",
}
TRAD_CHARS = set("們這個說時會來國對開關與為從見發還過讓單劍門頭體點聽寫氣藥")
SIMP_CHARS = set("们这个说时会来国对开关与为从见发还过让单剑门头体点听写气药")

CONTAINER_MAGIC = b"paloc"
CONTAINER_DATA_OFFSET = 0x200


# ---------------------------------------------------------------- config

def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def find_game_dirs():
    """Find Crimson Desert installs in all Steam libraries."""
    home = Path.home()
    vdfs = [
        home / ".local/share/Steam/steamapps/libraryfolders.vdf",
        home / ".steam/steam/steamapps/libraryfolders.vdf",
    ]
    libs = []
    for vdf in vdfs:
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        libs += re.findall(r'"path"\s+"([^"]+)"', text)
    libs += [str(home / ".local/share/Steam")]
    found = []
    for lib in libs:
        p = Path(lib) / "steamapps" / "common" / GAME_DIR_NAME
        if p.is_dir() and str(p) not in found:
            found.append(str(p))
    return found


# ---------------------------------------------------------------- paloc I/O

def list_paloc_entries(folder):
    """Return {file name: PazEntry} for gamedata/*.paloc in a game folder."""
    folder = Path(folder)
    pamt = folder / "0.pamt"
    if not pamt.exists():
        return {}
    result = {}
    for e in parse_pamt(str(pamt), paz_dir=str(folder)):
        path = e.path.replace("\\", "/")
        if path.startswith("gamedata/") and path.endswith(".paloc"):
            result[path.split("/")[-1]] = e
    return result


def unwrap_paloc(data):
    """Strip the compressed 'paloc' container used by newer game versions."""
    if data.startswith(CONTAINER_MAGIC):
        comp_size, raw_size = struct.unpack_from("<II", data, 9)
        payload = data[CONTAINER_DATA_OFFSET:CONTAINER_DATA_OFFSET + comp_size]
        return lz4.block.decompress(payload, uncompressed_size=raw_size), True
    return data, False


def wrap_paloc(raw):
    comp = lz4.block.compress(raw, store_size=False)
    header = bytearray(CONTAINER_DATA_OFFSET)
    header[0:5] = CONTAINER_MAGIC
    struct.pack_into("<II", header, 9, len(comp), len(raw))
    return bytes(header) + comp


def read_paloc(entry):
    """Read, decrypt and unpack a .paloc entry. Returns (raw, wrapped)."""
    read_size = entry.comp_size if entry.compressed else entry.orig_size
    with open(entry.paz_file, "rb") as f:
        f.seek(entry.offset)
        data = f.read(read_size)
    if entry.encrypted:
        data = decrypt(data, os.path.basename(entry.path))
    if entry.compressed and entry.compression_type == 2:
        data = lz4.block.decompress(data, uncompressed_size=entry.orig_size)
    return unwrap_paloc(data)


def parse_paloc(raw):
    """Parse raw paloc data into ([(marker, key, text)], tail bytes)."""
    records = []
    i = 0
    n = len(raw)
    while i + 12 <= n:
        marker = raw[i:i + 8]
        k_len = struct.unpack_from("<I", raw, i + 8)[0]
        if i + 12 + k_len + 4 > n:
            break
        key = raw[i + 12:i + 12 + k_len]
        v_len = struct.unpack_from("<I", raw, i + 12 + k_len)[0]
        v_start = i + 16 + k_len
        if v_start + v_len > n:
            break
        value = raw[v_start:v_start + v_len]
        records.append((marker, key.decode("utf-8"), value.decode("utf-8")))
        i = v_start + v_len
    return records, raw[i:]


def build_paloc(records, tail):
    out = bytearray()
    for marker, key, text in records:
        kb = key.encode("utf-8")
        vb = text.encode("utf-8")
        out += marker
        out += struct.pack("<I", len(kb)) + kb
        out += struct.pack("<I", len(vb)) + vb
    out += tail
    return bytes(out)


def read_texts(entry):
    """Return {key: text} for one paloc entry."""
    raw, _ = read_paloc(entry)
    return {k: v for _, k, v in parse_paloc(raw)[0]}


# ---------------------------------------------------------------- languages

def detect_language(texts):
    s = "\n".join(texts)

    def count(lo, hi):
        return sum(1 for ch in s if lo <= ch <= hi)

    if count("가", "힣") > 30:
        return "ko"
    if count("぀", "ヿ") > 30:
        return "ja"
    if count("一", "鿿") > 30:
        trad = sum(1 for ch in s if ch in TRAD_CHARS)
        simp = sum(1 for ch in s if ch in SIMP_CHARS)
        return "zh-TW" if trad >= simp else "zh-CN"
    if count("Ѐ", "ӿ") > 30:
        return "ru"
    if count("؀", "ۿ") > 30:
        return "ar"
    lower = s.lower()
    words = collections.Counter(re.findall(r"[^\W\d_]+", lower))
    scores = {}
    for lang, sw in STOPWORDS.items():
        score = sum(words[w] for w in sw)
        score += 3 * sum(lower.count(ch) for ch in SPECIAL_CHARS.get(lang, ""))
        scores[lang] = score
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "??"


def scan_game(game_dir, progress=None):
    """Find language folders. Returns list of dicts sorted by folder."""
    game = Path(game_dir)
    folders = sorted(p for p in game.iterdir() if p.is_dir() and p.name.isdigit())
    result = []
    for i, folder in enumerate(folders):
        if progress:
            progress(i, len(folders), folder.name)
        entries = list_paloc_entries(folder)
        if not entries:
            continue
        probe = entries.get("ui.paloc") or next(iter(entries.values()))
        texts = list(read_texts(probe).values())
        lang = detect_language(texts[:600])
        sample = next((t for t in texts if 8 < len(t) < 60 and "{" not in t), texts[0] if texts else "")
        result.append({
            "folder": folder.name,
            "lang": lang,
            "files": sorted(entries),
            "sample": sample,
        })
    return result


def scan_labels(result):
    """Display names for scanned languages, with the folder added when a language appears twice."""
    counts = collections.Counter(r["lang"] for r in result)
    return [f"{lang_name(r['lang'])} ({r['folder']})" if counts[r["lang"]] > 1 else lang_name(r["lang"])
            for r in result]


# ---------------------------------------------------------------- validation

def _structure(text):
    tokens = collections.Counter()
    brs = 0
    for m in TOKEN_RE.finditer(text):
        if m.group(3) is not None:
            tag = m.group(3).replace(" ", "").lower()
            if tag.startswith("br"):
                brs += 1
                continue
            tokens[f"<{tag}>"] += 1
        else:
            hash_part = "#…" if m.group(2) else ""
            tokens["{" + m.group(1).strip().lower() + hash_part + "}"] += 1
    return tokens, brs


def check_entry(source, target, lang=None):
    """Return (errors, warnings) for a translated text, described in the UI language (or lang)."""
    errors = []
    warnings = []
    if source.strip() and not target.strip():
        errors.append(tr("check.empty", lang))
        return errors, warnings
    st, sbr = _structure(source)
    tt, tbr = _structure(target)
    missing = st - tt
    extra = tt - st
    if missing:
        errors.append(tr("check.missing", lang, tags=", ".join(sorted(missing))))
    if extra:
        errors.append(tr("check.extra", lang, tags=", ".join(sorted(extra))))
    if target.count("{") != target.count("}"):
        errors.append(tr("check.unclosed", lang))
    if sbr != tbr:
        warnings.append(tr("check.br", lang, src=sbr, tgt=tbr))
    if source == target and re.search(r"[^\W\d_]{3,}", source):
        warnings.append(tr("check.same", lang))
    return errors, warnings


# ---------------------------------------------------------------- projects

def project_dir_name(src_lang, src_folder, tgt_lang, tgt_folder):
    return f"{src_lang}-{tgt_lang}_{src_folder}-{tgt_folder}"


def project_data_dir(path):
    """Per-file data folder of a project ('dane' in projects from older versions)."""
    legacy = Path(path) / "dane"
    return legacy if legacy.is_dir() else Path(path) / "data"


def list_projects():
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(p for p in PROJECTS_DIR.iterdir() if (p / "project.json").exists())


class Project:
    def __init__(self, path):
        self.path = Path(path)
        with open(self.path / "project.json", encoding="utf-8") as f:
            self.meta = json.load(f)
        self._cache = {}
        self._charset = None
        self.dirty = set()

    # meta shortcuts
    @property
    def game_dir(self):
        return self.meta["game_dir"]

    @property
    def src_lang(self):
        return self.meta["source"]["lang"]

    @property
    def tgt_lang(self):
        return self.meta["target"]["lang"]

    @property
    def src_folder(self):
        return self.meta["source"]["folder"]

    @property
    def tgt_folder(self):
        return self.meta["target"]["folder"]

    @property
    def files(self):
        return self.meta["files"]

    @property
    def title(self):
        return (f"{lang_name(self.src_lang)} ({self.src_folder}) → "
                f"{lang_name(self.tgt_lang)} ({self.tgt_folder})")

    def data_path(self, name):
        return project_data_dir(self.path) / f"{name}.json"

    def load_file(self, name):
        if name not in self._cache:
            with open(self.data_path(name), encoding="utf-8") as f:
                entries = json.load(f)["entries"]
            for e in entries:
                refresh_issues(e)
            self._cache[name] = entries
        return self._cache[name]

    def save_file(self, name):
        entries = self._cache.get(name)
        if entries is None:
            return
        clean = [{k: v for k, v in e.items() if not k.startswith("_")} for e in entries]
        tmp = self.data_path(name).with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"file": name, "entries": clean}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.data_path(name))
        self.dirty.discard(name)

    def save_all(self):
        for name in list(self.dirty):
            self.save_file(name)

    def charset(self):
        if self._charset is None:
            try:
                self._charset = (self.path / "charset.txt").read_text(encoding="utf-8")
            except OSError:
                self._charset = ""
        return self._charset

    def file_stats(self, name):
        entries = self.load_file(name)
        return {
            "total": len(entries),
            "edited": sum(1 for e in entries if e["target"] != e["original"]),
            "errors": sum(1 for e in entries if e["_errors"]),
            "flagged": sum(1 for e in entries if e.get("flag")),
            "ai": sum(1 for e in entries if e.get("ai") is not None),
        }


def refresh_issues(entry):
    entry["_errors"], entry["_warnings"] = check_entry(entry["source"], entry["target"])
    if entry.get("ai") is not None:
        entry["_ai_errors"], _ = check_entry(entry["source"], entry["ai"])
    else:
        entry["_ai_errors"] = []


def export_project(game_dir, src, tgt, files, progress=None):
    """Export two languages into a comparison project.

    src/tgt are scan results ({folder, lang}). Existing projects keep their
    edits, flags, notes and AI suggestions.
    """
    game = Path(game_dir)
    pdir = PROJECTS_DIR / project_dir_name(src["lang"], src["folder"], tgt["lang"], tgt["folder"])
    data_dir = project_data_dir(pdir)
    data_dir.mkdir(parents=True, exist_ok=True)
    meta_path = pdir / "project.json"
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
            meta = json.load(f)
    else:
        meta = {"version": 1, "files": []}
    meta.update({
        "game_dir": str(game),
        "source": {"folder": src["folder"], "lang": src["lang"]},
        "target": {"folder": tgt["folder"], "lang": tgt["lang"]},
    })

    src_entries = list_paloc_entries(game / src["folder"])
    tgt_entries = list_paloc_entries(game / tgt["folder"])
    charset = set((pdir / "charset.txt").read_text(encoding="utf-8")) if (pdir / "charset.txt").exists() else set()

    for i, name in enumerate(files):
        if progress:
            progress(i, len(files), name)
        if name not in src_entries or name not in tgt_entries:
            continue
        src_texts = read_texts(src_entries[name])
        tgt_raw, _ = read_paloc(tgt_entries[name])
        tgt_records, _ = parse_paloc(tgt_raw)

        existing = {}
        data_path = data_dir / f"{name}.json"
        if data_path.exists():
            with open(data_path, encoding="utf-8") as f:
                existing = {e["key"]: e for e in json.load(f)["entries"]}

        entries = []
        for _, key, text in tgt_records:
            old = existing.get(key)
            e = {"key": key, "source": src_texts.get(key, "")}
            if old:
                e["target"] = old["target"]
                e["original"] = old["original"]
                for extra in ("flag", "note", "ai", "ai_reason"):
                    if extra in old:
                        e[extra] = old[extra]
            else:
                e["target"] = text
                e["original"] = text
            charset.update(e["original"])
            entries.append(e)

        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"file": name, "entries": entries}, f, ensure_ascii=False, indent=1)
        if name not in meta["files"]:
            meta["files"].append(name)

    meta["files"].sort()
    charset.discard("\n")
    (pdir / "charset.txt").write_text("".join(sorted(charset)), encoding="utf-8")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    if progress:
        progress(len(files), len(files), "")
    return Project(pdir)


# ---------------------------------------------------------------- AI tasks

def build_glossary(project):
    """Collect names from {StaticInfo:ID#name} tokens: ID -> source/target names."""
    pair_re = re.compile(r"\{([^{}#]+)#([^{}]*)\}")
    src_names = {}
    tgt_names = collections.defaultdict(collections.Counter)
    for name in project.files:
        for e in project.load_file(name):
            if "#" not in e["source"]:
                continue
            for ident, disp in pair_re.findall(e["source"]):
                src_names.setdefault(ident.lower(), disp)
            for ident, disp in pair_re.findall(e["target"]):
                tgt_names[ident.lower()][disp] += 1
    rows = []
    for ident, sname in sorted(src_names.items()):
        variants = tgt_names.get(ident)
        if not variants:
            continue
        best = variants.most_common(1)[0][0]
        others = [v for v in variants if v != best]
        rows.append((ident, sname, best, " | ".join(others)))
    return rows


def generate_ai_task(project, files, mode, chunk_size=200):
    """Write an AI task package. mode: 'all', 'errors', 'problems', 'flagged', 'edited'.

    The package is always written in English, so any AI agent can work on any language pair.
    """
    L = TASK_LAYOUTS[0]
    src, tgt = project.src_lang, project.tgt_lang
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out = AI_TASKS_DIR / f"{stamp}_{src}-{tgt}"
    (out / L["data"]).mkdir(parents=True)
    (out / L["results"]).mkdir()

    selected = []
    for name in files:
        for e in project.load_file(name):
            if not e["source"].strip() and not e["target"].strip():
                continue
            if mode == "errors" and not e["_errors"]:
                continue
            if mode == "problems" and not (e["_errors"] or e["_warnings"]):
                continue
            if mode == "flagged" and not e.get("flag"):
                continue
            if mode == "edited" and e["target"] == e["original"]:
                continue
            rec = {"id": f"{name}|{e['key']}", src: e["source"], tgt: e["target"]}
            issues = sum(check_entry(e["source"], e["target"], "en"), [])
            if issues:
                rec[L["issues"]] = issues
            if e.get("note"):
                rec[L["note"]] = e["note"]
            selected.append(rec)

    parts = []
    for i in range(0, len(selected), chunk_size):
        part = f"{L['part']}_{i // chunk_size + 1:03d}.jsonl"
        with open(out / L["data"] / part, "w", encoding="utf-8") as f:
            for rec in selected[i:i + chunk_size]:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        parts.append(part)

    glossary = build_glossary(project)
    with open(out / L["glossary"], "w", encoding="utf-8") as f:
        f.write(f"identifier\t{src}\t{tgt}\tother_variants_{tgt}\n")
        for row in glossary:
            f.write("\t".join(x.replace("\t", " ") for x in row) + "\n")

    (out / L["charset"]).write_text(project.charset(), encoding="utf-8")
    shutil.copy(BASE_DIR / "ai_check.py", out / L["check"])

    task = {
        "source_lang": src,
        "target_lang": tgt,
        "source_name": lang_name(src, "en"),
        "target_name": lang_name(tgt, "en"),
        "project": str(project.path),
        "mode": mode,
        "files": files,
        "parts": parts,
        "count": len(selected),
    }
    with open(out / L["meta"], "w", encoding="utf-8") as f:
        json.dump(task, f, ensure_ascii=False, indent=2)

    (out / L["instructions"]).write_text(_instructions(task, files, len(glossary)), encoding="utf-8")
    return out, len(selected)


def _instructions(task, files, glossary_size):
    s, t = task["source_lang"], task["target_lang"]
    sn, tn = task["source_name"], task["target_name"]
    file_lines = "\n".join(
        f"- `{n}` – {file_hint(n, 'en') or tr('file_hint.default', 'en')}" for n in files)
    mode_text = {
        "all": "all entries of the selected files",
        "errors": "only entries with automatically detected errors (missing tags, empty texts – see the `issues` field)",
        "problems": "only entries where the automatic check found a problem or a suspicion (`issues` field)",
        "flagged": "only entries the user flagged for review",
        "edited": "only entries already changed by the user (for verification)",
    }[task["mode"]]
    first = task["parts"][0] if task["parts"] else "part_001.jsonl"
    return f"""# Task: proofread the {tn} translation of Crimson Desert ({sn} → {tn})

You are an experienced translator and video game localization proofreader.
Crimson Desert is an open-world dark fantasy action game (the continent of
Pywel, mercenaries, a war between factions). Your task is to **compare the
original text ({sn}, `{s}`) with the current translation ({tn}, `{t}`) and
fix the translation where needed.** Write all corrected texts in {tn}.

This folder is a self-contained package. Work only inside it.

## Scope

- Number of entries: **{task['count']}**, split into {len(task['parts'])} parts in the `data/` folder.
- Selection: {mode_text}.
- Game files the texts come from (the part after `|` in the `id` field is the text key):
{file_lines}

## Files in the package

| File | Description |
|---|---|
| `data/part_NNN.jsonl` | Input data – **read only, do not modify**. One line = one JSON object. |
| `results/` | Save your corrections here (format below). |
| `glossary.tsv` | Glossary of proper names ({glossary_size} items) collected from the current translation: identifier, `{s}` name, most common `{t}` name, other variants found. |
| `allowed_chars.txt` | Every character used in the current translation. The game font may not contain any others – **do not use characters outside this list**. |
| `check.py` | Check script: `python3 check.py` |

Line format in `data/`:

```json
{{"id": "ui.paloc|4294967698", "{s}": "original text", "{t}": "current translation", "issues": ["optional: detected problems"], "note": "optional: user's note"}}
```

## What to fix

1. **Meaning errors** – the translation says something different from the original, a part is missing, or content was added.
2. **Untranslated text**, leftovers in another language, or placeholder text.
3. **Language errors** – spelling, punctuation, grammar, inflection, gender agreement.
4. **Inconsistent terminology** – the same name (item, skill, character, place) translated in different ways. Follow `glossary.tsv` unless an entry there is clearly wrong.
5. **Unnatural style** – calques, stiff word order. Dialogue must sound like the character's living speech and keep its tone (formal, rough, playful). Keep the form of addressing the player that dominates the current translation.
6. **Length** – interface texts (`ui.paloc`, item names, prompts) must not be much longer than the current ones, or they may not fit on screen. Spoken lines should roughly match the length of the original, so subtitles fit the voice-over.

If a translation is correct – **do not change it and do not list it in the results**. Do not force changes that are only a matter of taste.

## Tags – absolute rules

- `{{Key:...}}` – a button symbol. Copy it **exactly unchanged**.
- `{{StaticInfo:Category:Identifier#display text}}` – a link to a game object.
  Copy the part **before** `#` exactly unchanged; translate **only** the text after `#`
  (it may be inflected to fit the sentence). If the original has no `#`, do not add one.
- Keep the letter case of the tag name (`StaticInfo` / `Staticinfo`) as it is in the current translation.
- `<br/>` – a line break. Keep it; you may move it to a natural place in the sentence.
- Every tag from the original must appear in the translation the same number of times; do not add new ones.
- Keep markers in square brackets (e.g. `[Level 1]`) and numbers.
- If `issues` mentions missing tags – restore them in the translation.

## Result format

For each part `data/part_NNN.jsonl`, create a file with **the same name** in `results/`
that contains **only the corrected entries**, one JSON object per line (UTF-8):

```json
{{"id": "ui.paloc|4294967698", "{t}": "corrected translation", "reason": "briefly: what was fixed and why"}}
```

- Copy the `id` field unchanged.
- The `{t}` field is the **full** new text of the entry (not a fragment, not a diff).
- If nothing in a part needs fixing, create an empty result file.

## How to work

1. Read `glossary.tsv` and look through `allowed_chars.txt`.
2. Process the parts in order, starting with `data/{first}`. Read the whole part to know the context of neighbouring lines.
3. After each part, run `python3 check.py` and fix every reported error.
4. At the end, create `REPORT.md`: number of corrections, most common kinds of errors, entries you are unsure about (with `id`), and suggested glossary changes.

Do not modify the files in `data/`, `task.json` or `check.py`. The results will be imported
into the comparison program as suggestions that the user approves manually.
"""


def task_prompt(task_dir):
    """Prompt telling an AI agent to start a freshly generated task."""
    L = task_layout(task_dir) or TASK_LAYOUTS[0]
    return (f"Carry out the task described in the file:\n{task_dir}/{L['instructions']}\n\n"
            f"Work only in the folder {task_dir}. Save corrections in the {L['results']}/ subfolder "
            f"and after each part run: python3 {L['check']}")


def import_ai_results(project, task_dir):
    """Load the task's results/*.jsonl as AI suggestions. Returns (applied, problems)."""
    task_dir = Path(task_dir)
    L = task_layout(task_dir)
    with open(task_dir / L["meta"], encoding="utf-8") as f:
        task = json.load(f)
    tgt = task["target_lang"]
    if tgt != project.tgt_lang or task["source_lang"] != project.src_lang:
        raise ValueError(tr("core.task_lang_mismatch"))

    applied = 0
    problems = []
    by_file = collections.defaultdict(dict)
    for path in sorted((task_dir / L["results"]).glob("*.jsonl")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                file_name, key = rec["id"].split("|", 1)
                text = rec[tgt]
            except (ValueError, KeyError, AttributeError):
                problems.append(tr("core.bad_line", where=f"{path.name}:{n}"))
                continue
            by_file[file_name][key] = (text, rec.get(L["reason"], ""))

    for file_name, suggestions in by_file.items():
        if file_name not in project.files:
            problems.append(tr("core.file_not_in_project", name=file_name))
            continue
        entries = project.load_file(file_name)
        index = {e["key"]: e for e in entries}
        for key, (text, reason) in suggestions.items():
            e = index.get(key)
            if e is None:
                problems.append(tr("core.no_key", id=f"{file_name}|{key}"))
                continue
            if text == e["target"]:
                continue
            e["ai"] = text
            e["ai_reason"] = reason
            refresh_issues(e)
            applied += 1
        project.dirty.add(file_name)
    project.save_all()
    return applied, problems


def odd_chars(text, charset):
    """Characters the game font may not have (not used in the original translation)."""
    return sorted(set(text) - set(charset) - {"\n"}) if charset else []


def accept_ai(entry):
    entry["target"] = entry.pop("ai")
    entry.pop("ai_reason", None)
    refresh_issues(entry)


def reject_ai(entry):
    entry.pop("ai", None)
    entry.pop("ai_reason", None)
    refresh_issues(entry)


# ---------------------------------------------------------------- install

def _backup(game, folder, dest):
    (dest / folder).mkdir(parents=True, exist_ok=True)
    for f in (game / folder).iterdir():
        if f.is_file():
            shutil.copy2(f, dest / folder / f.name)
    (dest / "meta").mkdir(exist_ok=True)
    shutil.copy2(game / "meta" / "0.papgt", dest / "meta" / "0.papgt")


def _original_backup_dir(folder):
    """Backup of the untouched game texts ('_oryginal_' in older versions)."""
    legacy = BACKUPS_DIR / f"_oryginal_{folder}"
    return legacy if legacy.exists() else BACKUPS_DIR / f"_original_{folder}"


def _meta_patch(game, folder):
    result = subprocess.run(
        [sys.executable, str(TOOLS_DIR / "Meta_Patch.py"), str(game / folder)],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(tr("core.meta_failed", output=result.stdout + result.stderr))
    return "\n".join(line for line in result.stdout.splitlines() if not line.startswith("[=]"))


def pending_changes(project):
    """Return [(file name, new wrapped bytes)] that differ from the game."""
    game = Path(project.game_dir)
    entries = list_paloc_entries(game / project.tgt_folder)
    changes = []
    for name in project.files:
        if name not in entries:
            continue
        cur_raw, wrapped = read_paloc(entries[name])
        records, tail = parse_paloc(cur_raw)
        targets = {e["key"]: e["target"] for e in project.load_file(name)}
        new_raw = build_paloc([(m, k, targets.get(k, v)) for m, k, v in records], tail)
        if new_raw != cur_raw:
            changes.append((name, wrap_paloc(new_raw) if wrapped else new_raw))
    return changes


def install(project, log=print):
    """Write all edited translations into the game files."""
    game = Path(project.game_dir)
    folder = project.tgt_folder
    project.save_all()
    changes = pending_changes(project)
    if not changes:
        log(tr("core.nothing_to_install"))
        return 0

    original = _original_backup_dir(folder)
    if not original.exists():
        _backup(game, folder, original)
        log(tr("core.original_backup", path=original))
    stamp = BACKUPS_DIR / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    _backup(game, folder, stamp)
    log(tr("core.backup", path=stamp))

    pamt = game / folder / "0.pamt"
    with tempfile.TemporaryDirectory() as tmp:
        for name, data in changes:
            tmp_file = Path(tmp) / name
            tmp_file.write_bytes(data)
            # Offsets shift after every repack, so re-read the index each time
            entry = list_paloc_entries(game / folder)[name]
            repack_entry(str(tmp_file), entry, pamt_path=str(pamt))
            log(tr("core.installed", name=name))
    log(_meta_patch(game, folder).strip())
    return len(changes)


def restore_original(project, log=print):
    game = Path(project.game_dir)
    folder = project.tgt_folder
    original = _original_backup_dir(folder)
    if not original.exists():
        raise RuntimeError(tr("core.no_original"))
    for f in (original / folder).iterdir():
        shutil.copy2(f, game / folder / f.name)
    log(tr("core.restored", folder=folder, path=original))
    # Recompute checksums instead of restoring 0.papgt, so other mods stay valid
    log(_meta_patch(game, folder).strip())


# ---------------------------------------------------------------- continuing tasks

def list_ai_tasks(project=None):
    """Existing AI task folders (newest first) with their progress.

    With a project given, only tasks for the same language pair are returned.
    """
    if not AI_TASKS_DIR.is_dir():
        return []
    tasks = []
    for folder in AI_TASKS_DIR.iterdir():
        L = task_layout(folder)
        if not L:
            continue
        try:
            with open(folder / L["meta"], encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if project and (meta.get("source_lang") != project.src_lang
                        or meta.get("target_lang") != project.tgt_lang):
            continue
        done_parts = 0
        fixes = 0
        for part in (folder / L["results"]).glob("*.jsonl"):
            done_parts += 1
            with open(part, encoding="utf-8") as f:
                fixes += sum(1 for line in f if line.strip())
        tasks.append({
            "path": folder,
            "count": meta.get("count", 0),
            "parts": len(meta.get("parts", [])),
            "done_parts": done_parts,
            "fixes": fixes,
            "has_report": (folder / L["report"]).is_file(),
            "mtime": folder.stat().st_mtime,
        })
    tasks.sort(key=lambda t: t["mtime"], reverse=True)
    return tasks


def continue_prompt(task_dir):
    """Prompt telling a new AI agent to pick up an existing task where it stopped."""
    task_dir = Path(task_dir)
    L = task_layout(task_dir) or TASK_LAYOUTS[0]
    report = (f"then {L['report']} – it describes what has been done and what is left."
              if (task_dir / L["report"]).is_file()
              else f"then check which parts already have a file in {L['results']}/.")
    return (
        f"Continue the task in the folder:\n{task_dir}\n\n"
        f"First read {L['instructions']}, {report}\n"
        f"Leave the corrections already saved in {L['results']}/ unchanged and append new ones to the same files "
        f"(one line per entry, do not repeat the same id).\n"
        f"After each part run: python3 {L['check']}\n"
        f"At the end update {L['report']}: add what was done in this session and what is still left."
    )
