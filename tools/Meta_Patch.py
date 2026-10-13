import sys
import struct
from pathlib import Path


PA_MAGIC = 0x2145E233


def rol(x, k):
    return ((x << k) & 0xFFFFFFFF) | (x >> (32 - k))


def ror(x, k):
    return (x >> k) | ((x << (32 - k)) & 0xFFFFFFFF)


def pa_checksum(data: bytes):
    length = len(data)
    if length == 0:
        return 0

    a = b = c = (length - PA_MAGIC) & 0xFFFFFFFF
    offset = 0
    remaining = length

    while remaining > 12:
        a = (a + struct.unpack_from("<I", data, offset)[0]) & 0xFFFFFFFF
        b = (b + struct.unpack_from("<I", data, offset + 4)[0]) & 0xFFFFFFFF
        c = (c + struct.unpack_from("<I", data, offset + 8)[0]) & 0xFFFFFFFF

        a = (a - c) & 0xFFFFFFFF; a ^= rol(c, 4); c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= rol(a, 6); a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= rol(b, 8); b = (b + a) & 0xFFFFFFFF
        a = (a - c) & 0xFFFFFFFF; a ^= rol(c,16); c = (c + b) & 0xFFFFFFFF
        b = (b - a) & 0xFFFFFFFF; b ^= rol(a,19); a = (a + c) & 0xFFFFFFFF
        c = (c - b) & 0xFFFFFFFF; c ^= rol(b, 4); b = (b + a) & 0xFFFFFFFF

        offset += 12
        remaining -= 12

    tail = data[offset:]

    for i in range(remaining):
        byte = tail[i]
        if i < 4:
            a = (a + (byte << (8 * i))) & 0xFFFFFFFF
        elif i < 8:
            b = (b + (byte << (8 * (i - 4)))) & 0xFFFFFFFF
        else:
            c = (c + (byte << (8 * (i - 8)))) & 0xFFFFFFFF

    v82 = ((b ^ c) - rol(b, 14)) & 0xFFFFFFFF
    v83 = ((a ^ v82) - rol(v82, 11)) & 0xFFFFFFFF
    v84 = ((v83 ^ b) - ror(v83, 7)) & 0xFFFFFFFF
    v85 = ((v84 ^ v82) - rol(v84, 16)) & 0xFFFFFFFF
    v86 = rol(v85, 4)
    t = ((v83 ^ v85) - v86) & 0xFFFFFFFF
    v87 = ((t ^ v84) - rol(t, 14)) & 0xFFFFFFFF

    return ((v87 ^ v85) - ror(v87, 8)) & 0xFFFFFFFF



if len(sys.argv) > 1:
    ROOT = Path(sys.argv[1]).resolve().parent
else:
    ROOT = Path.cwd()
PAPGT_PATH = ROOT / "meta" / "0.papgt"

with open(PAPGT_PATH, "rb") as f:
    papgt = bytearray(f.read())

folders = sorted([
    f for f in ROOT.iterdir()
    if f.is_dir() and f.name.isdigit() and f.name != "meta"
])

changed = False

for entry_index, folder in enumerate(folders):

    pamt_path = folder / "0.pamt"
    if not pamt_path.exists():
        continue

    with open(pamt_path, "rb") as f:
        pamt = f.read()

    real_crc = pa_checksum(pamt[12:])

    crc_offset = 12 + (entry_index * 12) + 8

    old_crc = struct.unpack_from("<I", papgt, crc_offset)[0]

    if old_crc == real_crc:
        print(f"[=] Skip {folder.name}")
        continue

    struct.pack_into("<I", papgt, crc_offset, real_crc)

    print(f"[+] Patch {folder.name}: {old_crc:08X} -> {real_crc:08X}")
    changed = True


if changed:
    papgt_hash = pa_checksum(papgt[12:])
    struct.pack_into("<I", papgt, 4, papgt_hash)

    with open(PAPGT_PATH, "wb") as f:
        f.write(papgt)

    print(f"[+] 0.papgt updated CRC = 0x{papgt_hash:08X}")
else:
    print("[+] No changes")