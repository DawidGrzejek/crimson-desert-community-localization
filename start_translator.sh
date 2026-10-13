#!/bin/bash
cd "$(dirname "$(readlink -f "$0")")"
exec ./.venv/bin/python cd_translator.py
