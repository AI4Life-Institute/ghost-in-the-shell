#!/bin/bash
# After cargo build, re-sign binary and relaunch
BINARY="$(dirname "$0")/../src-tauri/target/debug/ghost"
ENTITLEMENTS="$(dirname "$0")/../src-tauri/entitlements.plist"
codesign --force --deep --sign - --entitlements "$ENTITLEMENTS" "$BINARY" 2>/dev/null
exec "$BINARY" "$@"
