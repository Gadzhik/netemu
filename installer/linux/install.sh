#!/bin/sh
# Install NetEmu for the current user (no root needed for the program itself):
#   - copies the program to ~/.local/opt/netemu
#   - adds "NetEmu" to the applications menu and opens .netemu files with it
#   - checks the system libraries Qt needs and offers to install the missing ones
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/../opt/netemu"
DEST="$(mkdir -p "$DEST" && cd "$DEST" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
MIME="${XDG_DATA_HOME:-$HOME/.local/share}/mime/packages"
BIN="$HOME/.local/bin"

if [ "$1" = "--uninstall" ]; then
    rm -rf "$DEST" "$APPS/netemu.desktop" "$ICONS/netemu.png" "$MIME/netemu.xml" "$BIN/netemu"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
    command -v update-mime-database >/dev/null 2>&1 && update-mime-database "$(dirname "$MIME")" 2>/dev/null || true
    echo "NetEmu removed."
    exit 0
fi

echo "Installing NetEmu to $DEST"
rm -rf "$DEST"
mkdir -p "$DEST" "$APPS" "$ICONS" "$MIME" "$BIN"
cp -a "$HERE/." "$DEST/"
chmod +x "$DEST/NetEmu" "$DEST/netemu.sh" "$DEST/install.sh"
ln -sf "$DEST/netemu.sh" "$BIN/netemu"
[ -f "$DEST/netemu.png" ] && cp "$DEST/netemu.png" "$ICONS/netemu.png"

cat > "$APPS/netemu.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=NetEmu
Comment=Network simulator with multi-vendor CLI
Exec="$DEST/netemu.sh" %f
Icon=netemu
Terminal=false
Categories=Education;Network;
MimeType=application/x-netemu;
EOF

cat > "$MIME/netemu.xml" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">
  <mime-type type="application/x-netemu">
    <comment>NetEmu project</comment>
    <glob pattern="*.netemu"/>
  </mime-type>
</mime-info>
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS" 2>/dev/null || true
command -v update-mime-database >/dev/null 2>&1 && update-mime-database "$(dirname "$MIME")" 2>/dev/null || true

echo "Checking the system libraries NetEmu needs..."
"$DEST/netemu.sh" --check-deps || true
echo "Done. Start NetEmu from the applications menu or with: $DEST/netemu.sh"
