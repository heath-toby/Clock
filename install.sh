#!/usr/bin/env bash
# Clock for Orca -- Installer
#
# Installs Clock as an Orca 51 user extension, into
# ~/.local/share/orca/extensions/, and approves it so Orca will load it.
#
# Orca 51 replaced the orca-customizations.py loader block with a
# first-class extension system, and Clock now stores its settings in
# Orca's own per-extension store rather than in a private GSettings
# schema. Settings from the old org.gnome.Orca.Clock schema are imported
# automatically the first time the extension runs, so this script leaves
# that schema in place -- see uninstall.sh to remove it.
#
# Re-running is safe. Because approval is by content hash, the script
# re-approves on every run -- which is what you want after editing.

set -euo pipefail

ADDON_NAME="clock"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
EXTENSIONS_DIR="$ORCA_DIR/extensions"
ADDON_DIR="$EXTENSIONS_DIR/$ADDON_NAME"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/$ADDON_NAME"
SOUNDS_SRC="$SCRIPT_DIR/sounds"

BEGIN_MARKER="# --- clock begin ---"
END_MARKER="# --- clock end ---"

info()  { echo "  [+] $*"; }
warn()  { echo "  [!] $*"; }
error() { echo "  [ERROR] $*" >&2; exit 1; }

echo ""
echo "=== Clock for Orca -- Installer ==="
echo ""

# Probe via extension_loader, not orca.extension: importing orca.extension
# first hits a circular import inside Orca itself (live_region_presenter
# imports it mid-initialisation). extension_loader pulls in command_manager
# ahead of it, so this import order is the one that works.
if ! python3 -c "import orca.extension_loader" 2>/dev/null; then
    error "Orca 51 or later with extension support not found."
fi
info "Orca with extension support found."

[ -d "$SOURCE_DIR" ] || error "Source directory '$SOURCE_DIR' not found."

rm -rf "$SOURCE_DIR/__pycache__"

mkdir -p "$ADDON_DIR"
# Remove files that no longer exist in the source, so the installed
# package -- and therefore its approval hash -- matches the source.
find "$ADDON_DIR" -maxdepth 1 -name '*.py' -delete
rm -rf "$ADDON_DIR/__pycache__"
cp "$SOURCE_DIR"/*.py "$ADDON_DIR/"
info "Installed extension package to $ADDON_DIR"

if [ -d "$SOUNDS_SRC" ]; then
    mkdir -p "$ADDON_DIR/sounds"
    # Prune first, for the same reason as the .py files above: a sound
    # dropped from the source would otherwise stay installed and keep
    # appearing in the settings dialog's chime list.
    find "$ADDON_DIR/sounds" -maxdepth 1 -name '*.wav' -delete
    cp "$SOUNDS_SRC"/*.wav "$ADDON_DIR/sounds/"
    info "Installed $(ls -1 "$ADDON_DIR/sounds/"*.wav 2>/dev/null | wc -l) chime sounds."
else
    warn "No sounds directory at $SOUNDS_SRC; chime styles will have nothing to play."
fi

# Approval covers every file in the package, sounds included, so it has
# to happen after the sounds are in place.
if orca --approve-extension "$ADDON_NAME" >/dev/null 2>&1; then
    info "Approved '$ADDON_NAME' with Orca."
else
    error "Could not approve the extension. Run: orca --approve-extension $ADDON_NAME"
fi

# Clean up after the pre-extension installer, if its block is still there.
if [ -f "$CUSTOMIZATIONS" ] && grep -qF "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed the obsolete Clock block from orca-customizations.py."
fi

echo ""
echo "=== Installation complete ==="
echo ""
echo "  Restart Orca to activate:"
echo "    orca --replace &"
echo ""
echo "  Settings: Orca+Ctrl+C, or Orca Preferences -> User Extensions -> Clock."
echo "  Settings from the old org.gnome.Orca.Clock schema are imported on"
echo "  first run; nothing to do by hand."
echo ""
