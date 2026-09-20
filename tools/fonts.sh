#!/bin/sh
# Copies SF Mono, the Mac Terminal's font, out of Terminal.app so the dashboard can serve it at
# /fonts/. Apple's licence does not allow redistributing the files, so data/fonts/ is gitignored
# and this runs on the demo laptop. Without the files the stack falls back to Menlo.
src=/System/Applications/Utilities/Terminal.app/Contents/Resources/Fonts
dst="$(cd "$(dirname "$0")/.." && pwd)/data/fonts"
[ -d "$src" ] || { echo "fonts: not a Mac, keeping Menlo"; exit 0; }
mkdir -p "$dst"
for w in Regular Medium Bold; do cp "$src/SF-Mono-$w.otf" "$dst/"; done
echo "fonts: SF Mono in $dst"
