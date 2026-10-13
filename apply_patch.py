#!/usr/bin/env python3
"""Install a translation patch into Crimson Desert – for players, no window needed.

    python apply_patch.py my-fix.cdpatch.jsonl            # finds the game in Steam automatically
    python apply_patch.py my-fix.cdpatch.jsonl --game "D:/SteamLibrary/steamapps/common/Crimson Desert"
    python apply_patch.py my-fix.cdpatch.jsonl --check    # only show what would change
    python apply_patch.py my-fix.cdpatch.jsonl --restore  # put the original texts back

Close the game first. A backup is made before every change.
"""

import argparse
import sys
from pathlib import Path

import cd_core as core
import cd_patch as patch
import i18n


def main():
    i18n.set_language(core.load_config().get("ui_language") or "en")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("patch", help="patch file (*.cdpatch.jsonl)")
    parser.add_argument("--game", help="Crimson Desert folder (default: found in Steam libraries)")
    parser.add_argument("--restore", action="store_true", help="restore the original texts of the patch's language")
    parser.add_argument("--check", action="store_true", help="only show what would be installed, change nothing")
    args = parser.parse_args()

    game = args.game
    if not game:
        found = core.find_game_dirs()
        if not found:
            sys.exit("Crimson Desert was not found. Use --game to give its folder.")
        game = found[0]
    print(f"Game: {game}")
    try:
        if args.restore:
            header, _ = patch.read_patch(args.patch)
            folder = header.get("target_folder")
            original = core._original_backup_dir(folder)
            if not original.exists():
                sys.exit(core.tr("core.no_original"))
            core._restore_folder(Path(game), folder, original)
            print(core._meta_patch(Path(game), folder).strip())
            print(core.tr("restore.done"))
        elif args.check:
            patch.plan_patch(game, args.patch)
        else:
            n = patch.apply_patch(game, args.patch)
            print(core.tr("install.done", n=n))
    except Exception as e:
        sys.exit(core.tr("install.error", error=e))


if __name__ == "__main__":
    main()
