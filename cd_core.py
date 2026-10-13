"""Core logic for the Crimson Desert translation comparer (no GUI).

Reads .paloc text files straight from the game's PAZ archives, pairs two
languages by key, stores editable comparison projects as JSON, prepares
task packages for AI agents, and writes edited translations back into
the game.
"""

import collections
import contextlib
import datetime
import io
import json
import os
import re
import runpy
import shutil
import struct
import sys
import tempfile
from pathlib import Path

from i18n import tr

# Program files (code, lang/, tools/) and user data (projects, tasks, backups, settings).
# They are the same folder, except in a packaged build, where data lives next to the executable.
RESOURCE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else RESOURCE_DIR
TOOLS_DIR = RESOURCE_DIR / "tools"
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
        Path("C:/Program Files (x86)/Steam/steamapps/libraryfolders.vdf"),
        Path("C:/Program Files/Steam/steamapps/libraryfolders.vdf"),
    ]
    libs = []
    for vdf in vdfs:
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        libs += [p.replace("\\\\", "/") for p in re.findall(r'"path"\s+"([^"]+)"', text)]
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

def game_stamp(game_dir, folder):
    """Size and modification time of a language package index; it changes when the game is updated."""
    try:
        st = (Path(game_dir) / folder / "0.pamt").stat()
        return [st.st_size, int(st.st_mtime)]
    except OSError:
        return None


def game_updated(project):
    """True if the original-language package changed since the project was exported."""
    stamp = project.meta.get("game_stamp")
    return stamp is not None and game_stamp(project.game_dir, project.src_folder) != stamp


def refresh_project(project, progress=None):
    """Re-read the game texts after a game update, keeping all edits (changed originals become 'outdated')."""
    project.save_all()
    src = {"folder": project.src_folder, "lang": project.src_lang}
    tgt = {"folder": project.tgt_folder, "lang": project.tgt_lang}
    return export_project(project.game_dir, src, tgt, project.files, progress)


def resolve_outdated(entry):
    """Mark an outdated correction as checked against the new original."""
    entry.pop("outdated", None)
    entry.pop("old_source", None)
    refresh_issues(entry)


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
        # Optional voice data (set with attach_voice): a cd_audio.VoiceLibrary and the transcripts
        self.voice = None
        self.transcripts = {}

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
                self._annotate(name, e)
                refresh_issues(e)
            self._cache[name] = entries
        return self._cache[name]

    def _annotate(self, name, e):
        e["_file"] = name
        e["_dur"] = self.voice.duration(name, e["key"]) if self.voice else None
        e["_heard"] = self.transcripts.get(f"{name}|{e['key']}")

    def attach_voice(self, library, transcripts):
        """Use voice durations and transcripts in the checks of all loaded entries."""
        self.voice = library
        self.transcripts = transcripts
        for name, entries in self._cache.items():
            for e in entries:
                self._annotate(name, e)
                refresh_issues(e)

    def neighbours(self, name, key, before=2, after=2):
        """Entries around a key in the order of the game file (the context of a dialogue line)."""
        entries = self.load_file(name)
        index = next((i for i, e in enumerate(entries) if e["key"] == key), None)
        if index is None:
            return [], []
        return entries[max(0, index - before):index], entries[index + 1:index + 1 + after]

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
            "voice": sum(1 for e in entries if e["_voice_issue"]),
            "timing": sum(1 for e in entries if e["_timing_issue"]),
            "outdated": sum(1 for e in entries if e.get("outdated")),
            "reviewed": sum(1 for e in entries if e.get("reviewed")),
        }


# Subtitle limits: translation much longer than the original, or too fast to read while the line is spoken
LONG_RATIO = 1.5
LONG_MIN_CHARS = 15
MAX_READING_SPEED = 20  # characters per second
# Fast speech is fine if the original is just as fast: only flag translations clearly longer than it
SPEED_RELATIVE = 1.25
VOICE_MISMATCH_RATIO = 0.6


def visible_text(text):
    """Text as shown on screen: tags removed, link text after # kept, <br/> as a space."""
    return re.sub(r"\s+", " ", TOKEN_RE.sub(lambda m: " " if m.group(3) is not None else (m.group(2) or "#")[1:], text)).strip()


def timing_warnings(entry, text, lang=None):
    """Length and reading-speed warnings for a subtitle (needs the voice duration for the speed)."""
    warnings = []
    src_len, tgt_len = len(visible_text(entry["source"])), len(visible_text(text))
    if src_len >= LONG_MIN_CHARS and tgt_len > src_len * LONG_RATIO:
        warnings.append(tr("check.longer", lang, pct=round(100 * (tgt_len - src_len) / src_len)))
    duration = entry.get("_dur")
    if (duration and duration > 0.5 and tgt_len / duration > MAX_READING_SPEED
            and tgt_len > src_len * SPEED_RELATIVE):
        warnings.append(tr("check.speed", lang, cps=round(tgt_len / duration, 1), max=MAX_READING_SPEED,
                           sec=duration))
    return warnings


def voice_warning(entry, lang=None):
    """Warning when the original subtitle differs from what the voice actually says."""
    heard = entry.get("_heard")
    if heard and heard.get("ratio", 1) < VOICE_MISMATCH_RATIO:
        return tr("check.voice", lang, text=heard["text"])
    return None


def refresh_issues(entry):
    entry["_errors"], entry["_warnings"] = check_entry(entry["source"], entry["target"])
    timing = timing_warnings(entry, entry["target"])
    voice = voice_warning(entry)
    entry["_timing_issue"] = bool(timing)
    entry["_voice_issue"] = voice is not None
    entry["_warnings"] += timing + ([voice] if voice else [])
    if entry.get("outdated"):
        entry["_warnings"].append(tr("check.outdated", old=entry.get("old_source", "")))
    if entry.get("ai") is not None:
        entry["_ai_errors"], _ = check_entry(entry["source"], entry["ai"])
    else:
        entry["_ai_errors"] = []


def merge_entry(old, key, source, game_text):
    """Project entry for a key after (re-)reading the game; old is the entry from the previous export."""
    e = {"key": key, "source": source}
    if not old:
        e["target"] = e["original"] = game_text
        return e
    # The game holds either its own text or our installed edit; anything else is an official update
    e["original"] = old["original"] if game_text in (old["target"], old["original"]) else game_text
    edited = old["target"] != old["original"]
    e["target"] = old["target"] if edited else e["original"]
    for extra in ("flag", "note", "ai", "ai_reason", "reviewed", "outdated", "old_source"):
        if extra in old:
            e[extra] = old[extra]
    if edited and old["source"] and old["source"] != source:
        # The original line changed in a game update after we corrected the translation
        e["outdated"] = True
        e.setdefault("old_source", old["source"])
    return e


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
            e = merge_entry(existing.get(key), key, src_texts.get(key, ""), text)
            charset.update(e["original"])
            entries.append(e)

        with open(data_path, "w", encoding="utf-8") as f:
            json.dump({"file": name, "entries": entries}, f, ensure_ascii=False, indent=1)
        if name not in meta["files"]:
            meta["files"].append(name)

    meta["files"].sort()
    meta["game_stamp"] = game_stamp(game, src["folder"])
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


def _stem(word):
    """Rough stem that survives inflection (Polish, German, …): the first ~70% of the word."""
    word = word.lower()
    return word if len(word) <= 4 else word[:max(4, round(len(word) * 0.7))]


def _same_name(a, b):
    """True if two translations of a name are just different inflected forms."""
    wa, wb = a.split(), b.split()
    return len(wa) == len(wb) and all(_stem(x) == _stem(y) or x.lower().startswith(_stem(y))
                                      or y.lower().startswith(_stem(x)) for x, y in zip(wa, wb))


def terminology_report(project, files=None, progress=None):
    """Inconsistent names: [{"name", "expected", "variants", "entries": [(file, key)]}], worst first.

    Names come from the game's own links ({StaticInfo:…#name}). Reported are names translated in
    clearly different ways, and entries whose original mentions a name in plain text while the
    translation uses none of its known forms.
    """
    glossary = build_glossary(project)
    names = {}
    for _ident, sname, best, others in glossary:
        if len(sname) < 4 or not sname[:1].isupper():
            continue
        variants = [best] + [v for v in others.split(" | ") if v]
        info = names.setdefault(sname, {"name": sname, "expected": best, "variants": set(), "entries": [],
                                        "forms": set()})
        info["forms"].update(v for v in variants if _same_name(v, best))
        info["variants"].update(v for v in variants if not _same_name(v, best))
    if not names:
        return []
    pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)) + r")\b")
    files = files or project.files
    for i, name in enumerate(files):
        if progress:
            progress(i, len(files), name)
        for e in project.load_file(name):
            src = visible_text(e["source"])
            if not src:
                continue
            tgt = visible_text(e["target"]).lower()
            for found in set(pattern.findall(src)):
                info = names[found]
                stems = [[_stem(w) for w in form.split()] for form in info["forms"]]
                if not any(all(st in tgt for st in form) for form in stems):
                    info["entries"].append((name, e["key"]))
    if progress:
        progress(len(files), len(files), "")
    report = []
    for info in names.values():
        if len(info["variants"]) > 0 or info["entries"]:
            report.append({"name": info["name"], "expected": info["expected"],
                           "variants": sorted(info["variants"]), "entries": info["entries"]})
    report.sort(key=lambda r: (-(len(r["variants"]) > 0), -len(r["entries"]), r["name"]))
    return report


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
            if mode == "voice" and not (e["_voice_issue"] or e["_timing_issue"] or e.get("outdated")):
                continue
            rec = {"id": f"{name}|{e['key']}", src: e["source"], tgt: e["target"]}
            voice = voice_warning(e, "en")
            issues = sum(check_entry(e["source"], e["target"], "en"), []) + timing_warnings(e, e["target"], "en")
            if voice:
                issues.append(voice)
                rec["voice"] = e["_heard"]["text"]
            if e.get("_dur"):
                rec["seconds"] = e["_dur"]
            if e.get("outdated"):
                issues.append(tr("check.outdated", "en", old=e.get("old_source", "")))
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
    shutil.copy(RESOURCE_DIR / "ai_check.py", out / L["check"])

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
        "voice": "only entries whose subtitle does not fit the voice-over (different wording, too long, "
                 "too fast to read) or whose original changed in a game update",
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
{{"id": "ui.paloc|4294967698", "{s}": "original text", "{t}": "current translation", "issues": ["optional: detected problems"], "note": "optional: user's note", "voice": "optional", "seconds": 4.2}}
```

- `voice` – what the voice actor actually says (speech recognition of the {sn} voice-over). It is present
  only when it differs from the `{s}` subtitle. **The translation must match what is spoken**, so translate
  the meaning of `voice` (speech recognition may misspell names – take names from `{s}` and the glossary).
- `seconds` – length of the spoken line. A subtitle must be readable in that time: keep it at most about
  {MAX_READING_SPEED} characters per second and roughly as long as the original.

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
    """Recompute the package checksums in meta/0.papgt (runs tools/Meta_Patch.py in this process)."""
    out = io.StringIO()
    argv = sys.argv
    sys.argv = [str(TOOLS_DIR / "Meta_Patch.py"), str(game / folder)]
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(sys.argv[0], run_name="__main__")
    except Exception as e:
        raise RuntimeError(tr("core.meta_failed", output=f"{out.getvalue()}{e}")) from e
    finally:
        sys.argv = argv
    return "\n".join(line for line in out.getvalue().splitlines() if not line.startswith("[=]"))


def compute_changes(game_dir, folder, texts):
    """[(file name, bytes to write, expected raw text data)] for {file: {key: new text}} that differ from the game."""
    entries = list_paloc_entries(Path(game_dir) / folder)
    changes = []
    for name, new_texts in texts.items():
        if name not in entries:
            continue
        cur_raw, wrapped = read_paloc(entries[name])
        records, tail = parse_paloc(cur_raw)
        new_raw = build_paloc([(m, k, new_texts.get(k, v)) for m, k, v in records], tail)
        if new_raw != cur_raw:
            changes.append((name, wrap_paloc(new_raw) if wrapped else new_raw, new_raw))
    return changes


def pending_changes(project):
    """Changes needed so the game contains the project's translation."""
    texts = {name: {e["key"]: e["target"] for e in project.load_file(name)} for name in project.files}
    return compute_changes(project.game_dir, project.tgt_folder, texts)


def _restore_folder(game, folder, backup):
    for f in (backup / folder).iterdir():
        shutil.copy2(f, game / folder / f.name)


def write_changes(game_dir, folder, changes, log=print):
    """Back up, repack the changed text files, verify them and update checksums.

    If anything fails, the language folder is rolled back to the backup taken just before.
    """
    game = Path(game_dir)
    original = _original_backup_dir(folder)
    if not original.exists():
        _backup(game, folder, original)
        log(tr("core.original_backup", path=original))
    stamp = BACKUPS_DIR / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    _backup(game, folder, stamp)
    log(tr("core.backup", path=stamp))

    pamt = game / folder / "0.pamt"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for name, data, _ in changes:
                tmp_file = Path(tmp) / name
                tmp_file.write_bytes(data)
                # Offsets shift after every repack, so re-read the index each time
                entry = list_paloc_entries(game / folder)[name]
                repack_entry(str(tmp_file), entry, pamt_path=str(pamt))
                log(tr("core.installed", name=name))
        entries = list_paloc_entries(game / folder)
        for name, _, expected in changes:
            if read_paloc(entries[name])[0] != expected:
                raise RuntimeError(tr("core.verify_failed", name=name))
        log(tr("core.verified", n=len(changes)))
        log(_meta_patch(game, folder).strip())
    except Exception as e:
        _restore_folder(game, folder, stamp)
        _meta_patch(game, folder)
        raise RuntimeError(tr("core.rolled_back", error=e, path=stamp)) from e
    return len(changes)


def install(project, log=print):
    """Write all edited translations into the game files."""
    project.save_all()
    changes = pending_changes(project)
    if not changes:
        log(tr("core.nothing_to_install"))
        return 0
    return write_changes(project.game_dir, project.tgt_folder, changes, log)


def restore_original(project, log=print):
    game = Path(project.game_dir)
    folder = project.tgt_folder
    original = _original_backup_dir(folder)
    if not original.exists():
        raise RuntimeError(tr("core.no_original"))
    _restore_folder(game, folder, original)
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
