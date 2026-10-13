"""Voice-over support: find the spoken line for a text key, decode it, measure it.

Voice files live in their own game packages (one per voice language) as
Wwise .wem files named "<speaker>_<text key>.wem", e.g.
"nhm_adult_noble_1_questdialog_main_50831.wem" is text key
"questdialog_main_50831" spoken by "nhm_adult_noble_1".
Decoding .wem needs vgmstream-cli (https://vgmstream.org).
"""

import io
import json
import os
import shutil
import struct
import subprocess
import urllib.request
import wave
import zipfile
from pathlib import Path

import cd_core as core

# Voice packages of the current game version and their language
VOICE_PACKAGES = {"0005": "ko", "0006": "en", "0035": "zh-CN"}
# Text files that have voice-over
VOICED_FILES = ("questdialog.paloc", "aidialog.paloc", "sequencer.paloc",
                "narrationdialog.paloc", "stageclosedialog.paloc")
CACHE_DIR = core.BASE_DIR / "cache" / "voice"
INDEX_NAME = "voice_index.json"
INDEX_VERSION = 1

# Speaker codes at the start of voice file names (n = NPC, h/d/g/t = human/dwarf/giant/troll, m/w = man/woman)
SPEAKER_KINDS = ("nhm", "nhw", "ndm", "ndw", "ngm", "ngw", "ntm", "ntw")


def find_vgmstream():
    """Path to vgmstream-cli, or None."""
    cfg = core.load_config().get("vgmstream")
    candidates = [cfg] if cfg else []
    exe = "vgmstream-cli.exe" if os.name == "nt" else "vgmstream-cli"
    candidates += [shutil.which("vgmstream-cli"), str(core.TOOLS_DIR / "vgmstream" / exe),
                   str(core.BASE_DIR / "tools" / "vgmstream" / exe),
                   str(Path.home() / ".local/share/vgmstream" / exe)]
    return next((c for c in candidates if c and Path(c).is_file()), None)


VGMSTREAM_RELEASES = "https://api.github.com/repos/vgmstream/vgmstream/releases/latest"


def download_vgmstream():
    """Download the official vgmstream-cli build for this system into tools/vgmstream. Returns its path."""
    asset = "vgmstream-win64.zip" if os.name == "nt" else "vgmstream-linux.zip"
    with urllib.request.urlopen(VGMSTREAM_RELEASES, timeout=60) as resp:
        release = json.load(resp)
    url = next(a["browser_download_url"] for a in release["assets"] if a["name"] == asset)
    with urllib.request.urlopen(url, timeout=300) as resp:
        data = resp.read()
    dest = core.BASE_DIR / "tools" / "vgmstream"
    dest.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
    exe = dest / ("vgmstream-cli.exe" if os.name == "nt" else "vgmstream-cli")
    exe.chmod(0o755)
    return exe


def voice_packages(game_dir):
    """{folder: language} of the voice packages present in the game folder."""
    overrides = core.load_config().get("voice_packages") or {}
    packages = {**VOICE_PACKAGES, **overrides}
    return {f: lang for f, lang in packages.items() if (Path(game_dir) / f / "0.pamt").is_file()}


def voice_folder(game_dir, lang):
    return next((f for f, code in voice_packages(game_dir).items() if code == lang), None)


def speaker_label(prefix):
    """'nhm_adult_noble_1' -> 'man adult noble 1' in the UI language; unique speakers keep their name."""
    parts = prefix.split("_")
    if parts and parts[0] in SPEAKER_KINDS:
        words = [core.tr(f"speaker.{parts[0]}")] + [core.tr(f"speaker.{w}") if core.tr(f"speaker.{w}") != f"speaker.{w}"
                                                    else w for w in parts[1:]]
        return " ".join(words)
    return prefix.replace("_", " ")


def _read_entry(entry, limit=None):
    size = entry.comp_size if entry.compressed else entry.orig_size
    with open(entry.paz_file, "rb") as f:
        f.seek(entry.offset)
        data = f.read(size if limit is None or entry.encrypted or entry.compressed else min(size, limit))
    if entry.encrypted:
        data = core.decrypt(data, os.path.basename(entry.path))
    if entry.compressed and entry.compression_type == 2:
        data = core.lz4.block.decompress(data, uncompressed_size=entry.orig_size)
    return data


def wem_duration(header):
    """Duration in seconds from a Wwise RIFF header (sample count is stored in the fmt chunk)."""
    if header[:4] != b"RIFF":
        return None
    i = 12
    while i + 8 <= len(header):
        cid = header[i:i + 4]
        size = struct.unpack_from("<I", header, i + 4)[0]
        if cid == b"fmt ":
            body = header[i + 8:i + 8 + size]
            if len(body) >= 28:
                rate = struct.unpack_from("<I", body, 4)[0]
                samples = struct.unpack_from("<I", body, 24)[0]
                return round(samples / rate, 3) if rate else None
            return None
        i += 8 + size + (size & 1)
    return None


def build_index(project, lang="en", progress=None):
    """Map text keys of a project to voice files of one voice language and store it in the project.

    Returns {"folder", "lang", "lines": {file: {key: [[speaker, path, seconds], ...]}}}.
    """
    folder = voice_folder(project.game_dir, lang)
    if not folder:
        raise RuntimeError(core.tr("voice.no_package", lang=core.lang_name(lang)))
    keys = {}
    for name in VOICED_FILES:
        if name in project.files:
            for e in project.load_file(name):
                keys[e["key"]] = name
    entries = core.parse_pamt(str(Path(project.game_dir) / folder / "0.pamt"),
                              paz_dir=str(Path(project.game_dir) / folder))
    wems = [e for e in entries if e.path.endswith(".wem")]
    lines = {}
    for n, e in enumerate(wems):
        if progress and n % 500 == 0:
            progress(n, len(wems), core.tr("voice.indexing"))
        stem = Path(e.path.replace("\\", "/")).stem
        parts = stem.split("_")
        for i in range(1, len(parts)):
            key = "_".join(parts[i:])
            if key in keys:
                seconds = wem_duration(_read_entry(e, 256))
                lines.setdefault(keys[key], {}).setdefault(key, []).append(["_".join(parts[:i]), e.path, seconds])
                break
    if progress:
        progress(len(wems), len(wems), "")
    index = {"version": INDEX_VERSION, "folder": folder, "lang": lang, "lines": lines}
    with open(project.path / INDEX_NAME, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    return index


def load_index(project):
    try:
        with open(project.path / INDEX_NAME, encoding="utf-8") as f:
            index = json.load(f)
        return index if index.get("version") == INDEX_VERSION else None
    except (OSError, ValueError):
        return None


class VoiceLibrary:
    """Voice lines of one project: lookup, durations and decoded WAV files (cached)."""

    def __init__(self, project, index):
        self.project = project
        self.index = index
        self._entries = None

    def lines(self, file_name, key):
        """[(speaker, path, seconds)] for a text key (empty if it has no voice)."""
        return [tuple(x) for x in self.index["lines"].get(file_name, {}).get(key, [])]

    def duration(self, file_name, key):
        found = self.lines(file_name, key)
        return found[0][2] if found else None

    def count(self, file_name=None):
        files = [file_name] if file_name else list(self.index["lines"])
        return sum(len(self.index["lines"].get(f, {})) for f in files)

    def _entry(self, path):
        if self._entries is None:
            folder = Path(self.project.game_dir) / self.index["folder"]
            self._entries = {e.path: e for e in core.parse_pamt(str(folder / "0.pamt"), paz_dir=str(folder))}
        return self._entries[path]

    def wav(self, path):
        """Decode a voice file to WAV (cached) and return its path."""
        out = CACHE_DIR / self.index["folder"] / (Path(path.replace("\\", "/")).stem + ".wav")
        if out.is_file():
            return out
        tool = find_vgmstream()
        if not tool:
            raise RuntimeError(core.tr("voice.no_vgmstream"))
        out.parent.mkdir(parents=True, exist_ok=True)
        wem = out.with_suffix(".wem")
        wem.write_bytes(_read_entry(self._entry(path)))
        try:
            result = subprocess.run([tool, "-o", str(out), str(wem)], capture_output=True, text=True)
        finally:
            wem.unlink(missing_ok=True)
        if result.returncode != 0 or not out.is_file():
            raise RuntimeError(result.stderr.strip() or result.stdout.strip())
        return out


def load_wav_16k(path):
    """Mono float32 samples at 16 kHz (the input format of Whisper), without extra dependencies."""
    import numpy as np
    with wave.open(str(path)) as w:
        raw = w.readframes(w.getnframes())
        rate, channels, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
    if width != 2:
        raise ValueError(f"unsupported sample width: {width}")
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    audio = audio.reshape(-1, channels).mean(axis=1)
    n = int(len(audio) * 16000 / rate)
    return np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
