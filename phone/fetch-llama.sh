#!/usr/bin/env bash
# Put llama.cpp's Apple xcframework where the Xcode project expects it (phone/Frameworks/, ignored
# by git: it is 870 MB). Build it from a llama.cpp checkout first, with that checkout's own
# ./build-xcframework.sh, ideally at the commit the box's llama-server runs, since parity with
# llama-server is what benchmarks/phone-render/ measured.
#
#   LLAMA_CPP=~/src/llama.cpp phone/fetch-llama.sh
set -euo pipefail
: "${LLAMA_CPP:?set LLAMA_CPP to a llama.cpp checkout that has run ./build-xcframework.sh}"
SRC="$LLAMA_CPP/build-apple/llama.xcframework"
[ -d "$SRC" ] || { echo "no $SRC: run ./build-xcframework.sh in $LLAMA_CPP first" >&2; exit 1; }
DEST="$(cd "$(dirname "$0")" && pwd)/Frameworks"
mkdir -p "$DEST"
rm -rf "$DEST/llama.xcframework"
cp -R "$SRC" "$DEST/"
git -C "$LLAMA_CPP" describe --tags --always > "$DEST/llama.version" 2>/dev/null || echo unknown > "$DEST/llama.version"
echo "llama.xcframework from $(cat "$DEST/llama.version") is in $DEST"
