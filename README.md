# Crimson Desert – Community Localization

A community project to fix the translations in **Crimson Desert**. Many subtitles in
the localized versions don't match what the characters actually say in the English
voice-over. Some lines are paraphrased into a different meaning, swapped between
speakers or given the wrong names, and some are left in English.

This repository contains a desktop tool that compares any two languages of the game
side by side, helps you review and fix the translation (by hand or with an AI agent),
and installs the corrected texts back into your own copy of the game, always with
backups.

**Polish** is the first language being worked on. Every other language the game ships with
can be handled the same way, and contributors are welcome.

> This is an unofficial, non-commercial fan project. It is not affiliated with or endorsed by
> Pearl Abyss. Crimson Desert and all game content belong to Pearl Abyss. This repository
> contains **no game files and no game text**, only the tool's own source code.

## Progress

| Language | Status | Corrected lines | Notes |
|---|---|---|---|
| 🇵🇱 Polish | 🔄 In progress | **34 495** | all voiced lines reviewed; waiting for in-game testing |

Polish details (corrections per game file, as of 2026-10-14):

| File | What it contains | Corrections | Review |
|---|---|---|---|
| `sequencer.paloc` | cutscene subtitles | 7 862 | ✅ done |
| `questdialog.paloc` | quest dialogue | 13 856 | ✅ done |
| `aidialog.paloc` | NPC lines in the open world | 12 404 | ✅ done |
| `ui.paloc` and others | interface, knowledge, quests | 373 | ✅ critical errors (broken tags, missing buttons) fixed |

Kinds of problems fixed in Polish so far:
- subtitles that say something different from the voice-over, or belong to another line;
- wrong or inconsistent names of characters, places and factions (aligned with the in-game glossary);
- leftover system names in dialogue, random capital letters, wrong grammatical gender;
- broken tags, so missing button icons and links now show again;
- subtitle length brought roughly in line with the spoken line.

## Features

**Subtitles that match the voice-over** – the core of this project:
- 🔊 **Listen to the original voice** of every dialogue line right next to its subtitle
  (about 56 000 voiced lines), with the speaker and the length of the line.
- 🎙 **Speech recognition** (Whisper, runs locally) writes down what the actor really says and flags
  lines whose original subtitle differs from the voice. The AI then translates what is actually spoken.
- ⏱ **Length and reading-speed check**: a translation much longer than the original, or too fast to
  read in the time the line is spoken, is flagged automatically.

**Reviewing and fixing**
- Compares any two languages entry by entry: search, filters, notes, flags and a "reviewed" state.
- Shows the neighbouring lines of the scene, so every line is read in context.
- Automatic checks for missing or extra tags (`{Key:…}`, `{StaticInfo:…}`, `<br/>`), empty or
  untranslated texts, and characters the game font may not support.
- **Name consistency report**: characters, places, items and skills translated in different ways,
  and lines that mention a name the translation leaves out.

**AI help**
- **Agent workflow**: a self-contained task folder (instructions, glossary, allowed characters,
  check script, voice transcripts and line lengths) for an AI agent such as Claude Code.
- **Direct API**: or let the program send the task to Claude, any OpenAI-compatible service, or a
  local model in Ollama. Every answer is checked before you see it.
- All AI corrections come back as suggestions that you accept, reject or edit.

**Installing and sharing**
- **Install into the game** with one click: backups before every change, the written files are
  verified, and the program rolls back automatically if something fails. Original texts can be
  restored at any time.
- **Patches**: export only your corrected lines into a small, git-friendly file. Players install it
  with one command (`apply_patch`), and other translators import it as suggestions to review.
- **Game updates**: the program notices when the game was updated, re-reads the texts, keeps your
  corrections and marks those whose original line changed (⟳).
- Linux / Steam Deck and Windows; ready-made builds on the Releases page. Interface in English and
  Polish (adding a language is one JSON file).

## Requirements

- Crimson Desert (Steam), on Linux / Steam Deck or Windows.
- Python 3.10+ when running from source (the builds on the Releases page include everything).
- For the voice-over: **vgmstream-cli** to decode the game's Wwise audio. The program can download
  the official build for you (tab 5, *Download vgmstream*), or get it from [vgmstream.org](https://vgmstream.org).
- Optional, for speech recognition: the `faster-whisper` package (in `requirements-translator.txt`).
  Not included in the builds.
- The **PAZ helper scripts** in `tools/` (`paz_parse.py`, `paz_crypto.py`, `paz_repack.py`,
  `Meta_Patch.py`) read and repack the game's archives. They are third-party scripts, included
  with credit: they come from the
  [Crimson Desert Localization Tool on Nexus Mods](https://www.nexusmods.com/crimsondesert/mods/103?tab=files&file_id=259),
  and the archive scripts are based on
  [lazorr410/crimson-desert-unpacker](https://github.com/lazorr410/crimson-desert-unpacker) (MIT).
  See [tools/README.md](tools/README.md).

## Installation

**Ready-made build:** download the zip for your system from the
[Releases](https://github.com/DawidGrzejek/crimson-desert-community-localization/releases) page, unpack it
and start `CrimsonDesertTranslator`.

**From source (Linux / Steam Deck):**

```bash
git clone https://github.com/DawidGrzejek/crimson-desert-community-localization.git
cd crimson-desert-community-localization
python3 -m venv .venv
./.venv/bin/pip install -r requirements-translator.txt
./start_translator.sh
./install_desktop_entry.sh   # optional: add it to the application menu
```

On Windows: `python -m venv .venv`, `.venv\Scripts\pip install -r requirements-translator.txt`,
then `.venv\Scripts\python cd_translator.py`.

## How to use

1. **Game and languages.** Pick the game folder (usually found automatically) and click *Scan languages*.
   Choose the original language (e.g. English) and the language to fix, then click *Export and compare*.
2. **Compare.** Browse the files and entries, filter by errors, voice mismatches (🔊), length (⏱) or
   game updates (⟳), edit the translation, press *▶ Voice* to listen, flag entries and mark them as
   reviewed. Save with `Ctrl+S`.
3. **AI agent** (optional). Choose the files and which entries to include, then click *Generate task*.
   Paste the prompt into your AI agent, or click *Run via API*. When it has finished, click
   *Load results from task folder…* and review the suggestions (blue rows).
4. **Install into game.** **Close the game first**, then click *Install changes into game*.
   Here you can also export a patch, install someone else's patch, or import it as suggestions.
5. **Voice and quality.** Build the voice index once (about half a minute), run speech recognition
   for the lines you are working on, check name consistency, and refresh the texts after a game update.

### Installing a patch (for players)

Close the game, put the patch file next to the program and run:

```bash
./apply_patch.sh crimson-desert-en-pl.cdpatch.jsonl          # Linux / Steam Deck
apply_patch.bat crimson-desert-en-pl.cdpatch.jsonl           # Windows (or drag the file onto it)
./apply_patch.sh crimson-desert-en-pl.cdpatch.jsonl --check  # only show what would change
./apply_patch.sh crimson-desert-en-pl.cdpatch.jsonl --restore  # put the original texts back
```

Projects, AI tasks, backups and the voice cache are stored next to the program, in `projects/`,
`ai_tasks/`, `backups/` and `cache/`. They contain game content, so the `.gitignore` keeps them out
of the repository.

## Contributing

- **Fix a language.** Use the tool on your own copy of the game. Please open an issue first to
  coordinate, so two people don't work on the same files.
- **Translate the interface.** Copy `lang/en.json` to `lang/<code>.json` (e.g. `de.json`) and
  translate the values. The new language appears in the selector automatically.
- **Share corrections** as patches (tab 4). They are sorted text files, so they diff and merge well.
- **Code.** Bug reports and pull requests are welcome. Run the checks with
  `python -m unittest discover -s tests -t tests` and `ruff check .`.

Please don't commit game files or extracted game texts to this repository.

## Legal

This project follows the Pearl Abyss
[Fan Content Guidelines](https://crimsondesert.pearlabyss.com/en-US/Policy?_policyNo=130):
it is free and non-commercial, and it doesn't use Pearl Abyss logos or trademarks. Corrected
translations are not distributed here. Sharing them is on hold until it is clarified with
Pearl Abyss.

The tool's own source code is released under the [MIT License](LICENSE). The scripts in `tools/`
belong to their respective authors (see [tools/README.md](tools/README.md)).

---

## 🇵🇱 Po polsku

Społecznościowy projekt poprawiania tłumaczeń gry **Crimson Desert**. Zaczynamy od języka
polskiego: w wielu miejscach napisy nie zgadzają się z tym, co postacie mówią w angielskim
dubbingu. Program porównuje dwa języki gry obok siebie i pozwala poprawiać tłumaczenie ręcznie
albo z pomocą agenta AI (np. Claude Code). Poprawki wgrywa do gry, zawsze z kopią zapasową.

**Postęp polskiej wersji:** poprawiono **34 495** kwestii.
- Napisy do scenek (`sequencer.paloc`): przejrzane w całości.
- Dialogi zadań (`questdialog.paloc`): przejrzane w całości.
- Kwestie NPC (`aidialog.paloc`): przejrzane w całości.

Najważniejsze funkcje: odsłuch oryginalnego głosu przy każdej kwestii, rozpoznawanie mowy, które
wskazuje napisy niezgodne z nagraniem, kontrola długości i tempa czytania, raport spójności nazw,
praca z AI (agent albo bezpośrednio przez API), łatki do udostępniania poprawek oraz obsługa
aktualizacji gry.

Interfejs programu jest dostępny po polsku. Język wybiera się w zakładce „1. Gra i języki”.

To nieoficjalny projekt fanowski, niezwiązany z Pearl Abyss. Repozytorium nie zawiera plików gry
ani jej tekstów.
