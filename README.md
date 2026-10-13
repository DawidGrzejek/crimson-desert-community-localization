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
| 🇵🇱 Polish | 🔄 In progress | **25 382** | NPC lines being matched to the English voice-over |

Polish details (corrections per game file, as of 2026-10-13):

| File | What it contains | Corrections | Review |
|---|---|---|---|
| `sequencer.paloc` | cutscene subtitles | 7 862 | ✅ done |
| `questdialog.paloc` | quest dialogue | 10 380 | 🔄 46 / 71 batches |
| `aidialog.paloc` | NPC lines in the open world | 6 767 | ⏳ first pass done, full review pending |
| `ui.paloc` and others | interface, knowledge, quests | 373 | ✅ critical errors (broken tags, missing buttons) fixed |

Kinds of problems fixed in Polish so far:
- subtitles that say something different from the voice-over, or belong to another line;
- wrong or inconsistent names of characters, places and factions (aligned with the in-game glossary);
- leftover system names in dialogue, random capital letters, wrong grammatical gender;
- broken tags, so missing button icons and links now show again;
- subtitle length brought roughly in line with the spoken line.

## Features

- Finds the game in all Steam libraries and detects every language folder automatically.
- Compares any two languages entry by entry: search, filters, notes and "needs review" flags.
- Automatic checks for missing or extra tags (`{Key:…}`, `{StaticInfo:…}`, `<br/>`), empty or
  untranslated texts, and characters the game font may not support.
- **AI agent workflow**: generates a self-contained task folder (instructions, glossary of names,
  allowed characters, check script) that an AI agent such as Claude Code can work through. Its
  corrections come back as suggestions that you accept, reject or edit.
- **Install into the game** with one click. Before the first change it backs up the original files,
  and before every later change it backs up the current state. You can restore the original texts
  at any time.
- Interface in English and Polish. Adding a language is one JSON file.

## Requirements

- Crimson Desert (Steam), on Linux / Steam Deck or Windows.
- Python 3.10+.
- The **PAZ helper scripts** in a `tools/` folder next to the program: `paz_parse.py`,
  `paz_crypto.py`, `paz_repack.py` and `Meta_Patch.py`. They read and repack the game's archives
  and are not part of this repository. They come with the *Crimson Desert Localization Tool*, and
  the archive scripts are based on
  [lazorr410/crimson-desert-unpacker](https://github.com/lazorr410/crimson-desert-unpacker) (MIT).
  The `paz_parse.py` you use must recognise encrypted `.paloc` files.

## Installation (Linux / Steam Deck)

```bash
# 1. Put the files of this repository into the folder that contains tools/
git clone https://github.com/DawidGrzejek/crimson-desert-community-localization.git
cd crimson-desert-community-localization
# (copy or link the tools/ folder here)

# 2. Create a virtual environment and install the dependencies
python3 -m venv .venv
./.venv/bin/pip install -r requirements-translator.txt

# 3. Start
./start_translator.sh
```

On Windows: `python -m venv .venv`, `.venv\Scripts\pip install -r requirements-translator.txt`,
then `.venv\Scripts\python cd_translator.py`.

## How to use

1. **Game and languages.** Pick the game folder (usually found automatically) and click *Scan languages*.
   Choose the original language (e.g. English) and the language to fix, then click *Export and compare*.
2. **Compare.** Browse the files and entries, filter by errors, edit the translation directly,
   flag entries and add notes. Save with `Ctrl+S`.
3. **AI agent** (optional). Choose the files and which entries to include, then click *Generate task*
   and paste the prompt into your AI agent. When it has finished, click *Load results from task folder…*
   and review the suggestions (blue rows), or accept all suggestions that have no errors.
   *Continue work* prepares a prompt so a new agent session picks up where the last one stopped.
4. **Install into game.** **Close the game first**, then click *Install changes into game*.
   A game update may overwrite the texts; if it does, just install them again.

Projects, AI tasks and backups are stored next to the program, in `projects/`, `ai_tasks/` and
`backups/`. They contain game text, so the `.gitignore` keeps them out of the repository.

## Contributing

- **Fix a language.** Use the tool on your own copy of the game. Please open an issue first to
  coordinate, so two people don't work on the same files.
- **Translate the interface.** Copy `lang/en.json` to `lang/<code>.json` (e.g. `de.json`) and
  translate the values. The new language appears in the selector automatically.
- **Code.** Bug reports and pull requests are welcome.

Please don't commit game files or extracted game texts to this repository.

## Legal

This project follows the Pearl Abyss
[Fan Content Guidelines](https://crimsondesert.pearlabyss.com/en-US/Policy?_policyNo=130):
it is free and non-commercial, and it doesn't use Pearl Abyss logos or trademarks. Corrected
translations are not distributed here. Sharing them is on hold until it is clarified with
Pearl Abyss.

The tool's source code is released under the [MIT License](LICENSE).

---

## 🇵🇱 Po polsku

Społecznościowy projekt poprawiania tłumaczeń gry **Crimson Desert**. Zaczynamy od języka
polskiego: w wielu miejscach napisy nie zgadzają się z tym, co postacie mówią w angielskim
dubbingu. Program porównuje dwa języki gry obok siebie i pozwala poprawiać tłumaczenie ręcznie
albo z pomocą agenta AI (np. Claude Code). Poprawki wgrywa do gry, zawsze z kopią zapasową.

**Postęp polskiej wersji:** poprawiono **25 382** kwestie.
- Napisy do scenek (`sequencer.paloc`): przejrzane w całości.
- Dialogi zadań (`questdialog.paloc`): przejrzane 46 z 71 paczek.
- Kwestie NPC (`aidialog.paloc`): czekają na pełny przegląd.

Interfejs programu jest dostępny po polsku. Język wybiera się w zakładce „1. Gra i języki”.

To nieoficjalny projekt fanowski, niezwiązany z Pearl Abyss. Repozytorium nie zawiera plików gry
ani jej tekstów.
