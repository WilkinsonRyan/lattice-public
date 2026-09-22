#!/bin/zsh
# Build ~/Applications/Lattice Digest.app from main.m.
# Builds to a staging bundle first and only swaps it in once the compile and the
# signature both succeed — a failed build must never leave the app missing.
# The signing identifier MUST match CFBundleIdentifier or usernoted cannot tie
# the posting process to the bundle and the notification is misattributed.
set -e
SRC="${0:A:h}"
APP="$HOME/Applications/Lattice Digest.app"
STAGE="$(mktemp -d)/Lattice Digest.app"

mkdir -p "$STAGE/Contents/MacOS" "$STAGE/Contents/Resources"
clang -fobjc-arc -O2 -o "$STAGE/Contents/MacOS/LatticeDigest" "$SRC/main.m" \
      -framework Cocoa -framework UserNotifications
[[ -f "$SRC/applet.icns" ]] && cp "$SRC/applet.icns" "$STAGE/Contents/Resources/applet.icns"

cat > "$STAGE/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Lattice Digest</string>
  <key>CFBundleDisplayName</key><string>Lattice Digest</string>
  <key>CFBundleIdentifier</key><string>com.lattice.digest</string>
  <key>CFBundleExecutable</key><string>LatticeDigest</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleInfoDictionaryVersion</key><string>6.0</string>
  <key>CFBundleShortVersionString</key><string>2.0</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>CFBundleIconFile</key><string>applet</string>
  <key>NSPrincipalClass</key><string>NSApplication</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

codesign --force --sign - --identifier com.lattice.digest "$STAGE"
codesign --verify --strict "$STAGE"

rm -rf "$APP"
ditto "$STAGE" "$APP"
rm -rf "${STAGE:h}"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP"
codesign -dvvv "$APP" 2>&1 | grep -E "^Identifier|Info.plist"
echo "built $APP"
