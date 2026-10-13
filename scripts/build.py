"""Build standalone programs with PyInstaller (used by the release workflow).

    python scripts/build.py

Creates dist/CrimsonDesertTranslator/ (the window program) and
dist/apply_patch(.exe) (installs a patch for players), then zips them.
Speech recognition (faster-whisper) is not bundled – it is too large.
"""

import os
import platform
import shutil
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parent.parent
SEP = os.pathsep


def data(src, dest):
    return ["--add-data", f"{ROOT / src}{SEP}{dest}"]


def main():
    os.chdir(ROOT)
    common = ["--noconfirm", "--clean", "--paths", str(ROOT / "tools"),
              *data("lang", "lang"), *data("tools", "tools"), *data("ai_check.py", ".")]
    PyInstaller.__main__.run([str(ROOT / "cd_translator.py"), "--name", "CrimsonDesertTranslator", "--windowed",
                              "--exclude-module", "faster_whisper", *common])
    PyInstaller.__main__.run([str(ROOT / "apply_patch.py"), "--onefile", "--console",
                              "--exclude-module", "PyQt6", "--exclude-module", "numpy", *common])
    out = ROOT / "dist"
    exe = "apply_patch.exe" if os.name == "nt" else "apply_patch"
    shutil.copy(out / exe, out / "CrimsonDesertTranslator" / exe)
    for name in ("README.md", "LICENSE"):
        shutil.copy(ROOT / name, out / "CrimsonDesertTranslator" / name)
    system = {"Windows": "windows", "Linux": "linux", "Darwin": "macos"}[platform.system()]
    archive = shutil.make_archive(str(out / f"CrimsonDesertTranslator-{system}-x64"), "zip",
                                  out, "CrimsonDesertTranslator")
    print("Created", archive)


if __name__ == "__main__":
    main()
