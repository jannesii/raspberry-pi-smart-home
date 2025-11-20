#!/usr/bin/env bash
set -euo pipefail

# Compress web/src assets into gzipped files under web/dist for LittleFS upload
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/web/src"
DIST="$ROOT/web/dist"

mkdir -p "$DIST"
rm -f "$DIST"/*.gz

shopt -s nullglob
to_pack=("$SRC"/*.html "$SRC"/static/*)
if (( ${#to_pack[@]} == 0 )); then
  echo "No assets found in $SRC" >&2
  exit 1
fi

for file in "${to_pack[@]}"; do
  base="$(basename "$file")"
  gzip -c -9 "$file" > "$DIST/$base.gz"
done

echo "Built ${#to_pack[@]} assets into $DIST"
