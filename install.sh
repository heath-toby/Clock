#!/usr/bin/env bash
# Clock for Orca — Installer
set -euo pipefail

ADDON_NAME="clock"
ORCA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/orca"
ADDON_DIR="$ORCA_DIR/$ADDON_NAME"
CUSTOMIZATIONS="$ORCA_DIR/orca-customizations.py"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="$SCRIPT_DIR/$ADDON_NAME"
SOUNDS_SRC="$SCRIPT_DIR/sounds"
SCHEMA_FILE="org.gnome.Orca.Clock.gschema.xml"
SCHEMA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/glib-2.0/schemas"

BEGIN_MARKER="# --- clock begin ---"
END_MARKER="# --- clock end ---"

info()  { echo "  [+] $*"; }
warn()  { echo "  [!] $*"; }
error() { echo "  [ERROR] $*" >&2; exit 1; }

echo ""
echo "=== Clock for Orca — Installer ==="
echo ""

# Pre-flight
if ! python3 -c "import orca" 2>/dev/null; then
    error "Orca screen reader not found. Please install Orca first."
fi
info "Orca found."

if [ ! -d "$SOURCE_DIR" ]; then
    error "Source directory '$SOURCE_DIR' not found."
fi

# Check dependencies
if ! command -v pw-play >/dev/null 2>&1; then
    warn "pw-play not found. Sound playback requires PipeWire."
fi
if ! command -v soxi >/dev/null 2>&1; then
    warn "soxi not found (install sox). Sound duration detection will use fallbacks."
fi

# Install add-on files
info "Installing add-on files to $ADDON_DIR..."
mkdir -p "$ADDON_DIR"
cp "$SOURCE_DIR"/*.py "$ADDON_DIR/"
info "Python modules installed."

# Install sounds
if [ -d "$SOUNDS_SRC" ]; then
    mkdir -p "$ADDON_DIR/sounds"
    cp "$SOUNDS_SRC"/*.wav "$ADDON_DIR/sounds/"
    SOUND_COUNT=$(ls -1 "$ADDON_DIR/sounds/"*.wav 2>/dev/null | wc -l)
    info "$SOUND_COUNT sound files installed."
else
    warn "No sounds directory found at $SOUNDS_SRC"
fi

# Install GSettings schema
if [ -f "$SOURCE_DIR/$SCHEMA_FILE" ]; then
    info "Installing GSettings schema..."
    mkdir -p "$SCHEMA_DIR"
    cp "$SOURCE_DIR/$SCHEMA_FILE" "$SCHEMA_DIR/"
    if command -v glib-compile-schemas >/dev/null 2>&1; then
        glib-compile-schemas "$SCHEMA_DIR" 2>/dev/null && \
            info "GSettings schema compiled." || \
            warn "Could not compile GSettings schema."
    else
        warn "glib-compile-schemas not found."
    fi
else
    warn "GSettings schema file not found."
fi

# Set up orca-customizations.py
LOADER_BLOCK="${BEGIN_MARKER}
try:
    import sys as _sys, os as _os
    _orca_dir = _os.path.join(
        _os.environ.get(\"XDG_DATA_HOME\", _os.path.expanduser(\"~/.local/share\")),
        \"orca\"
    )
    if _orca_dir not in _sys.path:
        _sys.path.insert(0, _orca_dir)
    from clock.clock import register as _clock_register
    _clock_register()
except Exception as _e:
    import logging as _logging
    _logging.getLogger(\"orca-clock\").error(
        f\"Failed to load Clock: {_e}\", exc_info=True
    )
${END_MARKER}"

# Create customizations file if needed
if [ ! -f "$CUSTOMIZATIONS" ]; then
    touch "$CUSTOMIZATIONS"
    info "Created $CUSTOMIZATIONS"
fi

# Remove any previous clock block
if grep -q "$BEGIN_MARKER" "$CUSTOMIZATIONS" 2>/dev/null; then
    sed -i "/${BEGIN_MARKER//\//\\/}/,/${END_MARKER//\//\\/}/d" "$CUSTOMIZATIONS"
    info "Removed previous Clock loader block."
fi

# Append the loader block
if [ -s "$CUSTOMIZATIONS" ] && grep -q '[^[:space:]]' "$CUSTOMIZATIONS" 2>/dev/null; then
    echo "" >> "$CUSTOMIZATIONS"
    echo "$LOADER_BLOCK" >> "$CUSTOMIZATIONS"
    info "Loader appended to existing orca-customizations.py."
else
    echo "$LOADER_BLOCK" > "$CUSTOMIZATIONS"
    info "Created orca-customizations.py with loader."
fi

echo ""
echo "=== Installation complete! ==="
echo ""
echo "  Restart Orca for changes to take effect:"
echo "    orca --replace &"
echo ""
echo "  Settings: press Orca+Ctrl+C at any time."
echo "  Uninstall: run ./uninstall.sh"
echo ""
