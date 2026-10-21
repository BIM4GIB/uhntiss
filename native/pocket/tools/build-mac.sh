#!/bin/sh
set -eu
# MAX_SDK_PATH must point to Cycling74/max-sdk-base at the revision in README.
: "${MAX_SDK_PATH:?Set MAX_SDK_PATH to a max-sdk-base checkout}"
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
DEST=${1:-"$ROOT/m4l/pocket/build"}
mkdir -p "$DEST/uhntiss.pocket02~.mxo/Contents/MacOS"
clang++ -std=c++17 -O3 -DNDEBUG -arch arm64 -arch x86_64 -mmacosx-version-min=11.0 \
  -bundle -Wl,-undefined,dynamic_lookup \
  -I "$ROOT/native/pocket/include" -I "$MAX_SDK_PATH/c74support/max-includes" -I "$MAX_SDK_PATH/c74support/msp-includes" \
  "$ROOT/native/pocket/src/engine.cpp" "$ROOT/native/pocket/max/external.cpp" \
  -F "$MAX_SDK_PATH/c74support/msp-includes" -framework MaxAudioAPI \
  -o "$DEST/uhntiss.pocket02~.mxo/Contents/MacOS/uhntiss.pocket02~"
cp "$ROOT/native/pocket/max/Info.plist.in" "$DEST/uhntiss.pocket02~.mxo/Contents/Info.plist"
codesign --force --sign - "$DEST/uhntiss.pocket02~.mxo"
node "$ROOT/m4l/pocket/build.mjs" "$DEST"
