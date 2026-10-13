#!/bin/bash
# Adds the translator to the application menu (Steam Deck desktop mode / any Linux desktop).
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/crimson-desert-translator.desktop <<DESKTOP
[Desktop Entry]
Type=Application
Name=Crimson Desert Translation Comparer
Comment=Compare and fix Crimson Desert translations
Exec="$DIR/start_translator.sh"
Path=$DIR
Icon=accessories-dictionary
Terminal=false
Categories=Utility;Game;
DESKTOP
echo "Added to the application menu: Crimson Desert Translation Comparer"
