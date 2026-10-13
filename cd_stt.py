"""Speech-to-text for voice lines and comparison with the subtitles.

Uses faster-whisper (optional dependency, runs locally on the CPU). The
transcripts are stored per project in transcripts.jsonl, one line per entry,
so a long run can be stopped and continued at any time.
"""

import difflib
import json
import re

import cd_audio as audio

TRANSCRIPTS_NAME = "transcripts.jsonl"
MODELS = ("base.en", "small.en", "medium.en", "base", "small")
DEFAULT_MODEL = "base.en"
# Below this word similarity the subtitle is reported as different from the voice
MISMATCH_RATIO = 0.6

_TAG_RE = re.compile(r"\{[^{}#]*#([^{}]*)\}|\{[^{}]*\}|<[^<>]*>")
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)?")


def available():
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def plain_words(text):
    """Words of a subtitle without tags, punctuation and case (link text after # is kept)."""
    text = _TAG_RE.sub(lambda m: f" {m.group(1) or ''} ", text)
    return _WORD_RE.findall(text.lower())


def similarity(subtitle, heard):
    a, b = plain_words(subtitle), plain_words(heard)
    if not a and not b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def load_transcripts(project):
    """{"file|key": {"text", "model", "ratio"}}."""
    result = {}
    try:
        with open(project.path / TRANSCRIPTS_NAME, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    result[rec["id"]] = rec
    except (OSError, ValueError):
        pass
    return result


def save_transcript(project, rec):
    with open(project.path / TRANSCRIPTS_NAME, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


class Transcriber:
    def __init__(self, model_name=DEFAULT_MODEL, threads=0):
        from faster_whisper import WhisperModel
        self.model_name = model_name
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=threads)

    def transcribe(self, wav_path, hint=""):
        """Text heard in a WAV file. hint: subtitle, whose capitalised names help with spelling."""
        names = sorted({w for w in re.findall(r"\b[A-Z][a-z]{2,}\b", _TAG_RE.sub(" ", hint))})
        prompt = ("Names: " + ", ".join(names) + ".") if names else None
        language = "en" if self.model_name.endswith(".en") else None
        segments, _ = self.model.transcribe(audio.load_wav_16k(wav_path), language=language,
                                            initial_prompt=prompt, beam_size=1, vad_filter=False)
        return " ".join(s.text.strip() for s in segments).strip()


def transcribe_entries(project, library, items, model_name=DEFAULT_MODEL, progress=None, stop=None):
    """Transcribe [(file, entry)] that have voice; returns the number of new transcripts.

    progress(done, total, label) is called after every line; stop() returning True ends the run.
    """
    transcriber = Transcriber(model_name)
    done = 0
    for n, (file_name, e) in enumerate(items):
        if stop and stop():
            break
        lines = library.lines(file_name, e["key"])
        if not lines:
            continue
        heard = transcriber.transcribe(library.wav(lines[0][1]), e["source"])
        rec = {"id": f"{file_name}|{e['key']}", "text": heard, "model": model_name,
               "ratio": round(similarity(e["source"], heard), 3)}
        save_transcript(project, rec)
        project.transcripts[rec["id"]] = rec
        done += 1
        if progress:
            progress(n + 1, len(items), f"{file_name}|{e['key']}")
    return done
