# Third-party helper scripts

These scripts read, decrypt and repack the game's PAZ archives. They were **not written by this
project**.

- Source: the *Crimson Desert Localization Tool* on Nexus Mods –
  https://www.nexusmods.com/crimsondesert/mods/103?tab=files&file_id=259
  (the mod page has been hidden since September 2026 and no license was stated, so these files are
  redistributed here only to keep this tool working, with full credit)
- `paz_parse.py`, `paz_crypto.py` and `paz_repack.py` are based on
  [lazorr410/crimson-desert-unpacker](https://github.com/lazorr410/crimson-desert-unpacker) (MIT License).
  `paz_parse.py` is modified to treat `.paloc` files as encrypted.
- `Meta_Patch.py` recomputes the archive checksums in `meta/0.papgt` after repacking.

All credit goes to their original authors. If you are an author and want these files removed or
credited differently, please open an issue.
