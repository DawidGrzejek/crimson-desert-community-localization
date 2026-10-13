#!/bin/bash
# Installs a translation patch on Linux / Steam Deck: ./apply_patch.sh my-fix.cdpatch.jsonl
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv && ./.venv/bin/pip install -q lz4 cryptography || exit 1
fi
exec ./.venv/bin/python apply_patch.py "$@"
