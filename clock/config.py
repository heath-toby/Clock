"""GSettings-backed configuration for Clock for Orca."""

from __future__ import annotations

import logging
import os
import subprocess

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_log = logging.getLogger("orca-clock")

SCHEMA_ID = "org.gnome.Orca.Clock"

_VALID_INTERVALS = (0, 15, 30, 60)
_VALID_STYLES = ("off", "speech", "sound", "sound-speech")


def _get_schema_source():
    """Get a GSettings schema source that includes the user schema dir."""
    user_schema_dir = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "glib-2.0", "schemas",
    )
    default_source = Gio.SettingsSchemaSource.get_default()
    try:
        return Gio.SettingsSchemaSource.new_from_directory(
            user_schema_dir, default_source, False,
        )
    except GLib.Error:
        return default_source


class Config:
    """Clock configuration backed by GSettings."""

    def __init__(self):
        self.interval: int = 0
        self.chime_style: str = "off"
        self.chime_sound: str = "clock_chime1.wav"
        self.chime_volume: float = 0.5
        self.intermediate_enabled: bool = False
        self.intermediate_sound: str = "clock_chime3.wav"
        self._settings: Gio.Settings | None = None
        self._duration_cache: dict[str, float] = {}

    @classmethod
    def load(cls) -> Config:
        cfg = cls()
        cfg._init_gsettings()
        return cfg

    def _init_gsettings(self):
        source = _get_schema_source()
        schema = source.lookup(SCHEMA_ID, True)
        if schema is None:
            _log.warning("Clock: GSettings schema %s not found, using defaults", SCHEMA_ID)
            return
        self._settings = Gio.Settings.new_full(schema, None, None)
        self.interval = self._settings.get_int("interval")
        self.chime_style = self._settings.get_string("chime-style")
        self.chime_sound = self._settings.get_string("chime-sound")
        self.chime_volume = self._settings.get_double("chime-volume")
        self.intermediate_enabled = self._settings.get_boolean("intermediate-enabled")
        self.intermediate_sound = self._settings.get_string("intermediate-sound")
        # Validate
        if self.interval not in _VALID_INTERVALS:
            self.interval = 0
        if self.chime_style not in _VALID_STYLES:
            self.chime_style = "off"

    def save(self):
        if self._settings is None:
            _log.error("Clock: cannot save, GSettings not available")
            return
        self._settings.set_int("interval", self.interval)
        self._settings.set_string("chime-style", self.chime_style)
        self._settings.set_string("chime-sound", self.chime_sound)
        self._settings.set_double("chime-volume", self.chime_volume)
        self._settings.set_boolean("intermediate-enabled", self.intermediate_enabled)
        self._settings.set_string("intermediate-sound", self.intermediate_sound)

    @property
    def sounds_dir(self) -> str:
        orca_dir = os.path.join(
            os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
            "orca", "clock", "sounds",
        )
        return orca_dir

    def list_sounds(self) -> list[str]:
        d = self.sounds_dir
        if not os.path.isdir(d):
            return []
        return sorted(f for f in os.listdir(d) if f.lower().endswith(".wav"))

    def get_chime_path(self, is_hourly: bool = True) -> str:
        if is_hourly or not self.intermediate_enabled:
            sound = self.chime_sound
        else:
            sound = self.intermediate_sound
        return os.path.join(self.sounds_dir, sound)

    def get_chime_duration(self, filename: str | None = None) -> float:
        fname = filename or self.chime_sound
        if fname in self._duration_cache:
            return self._duration_cache[fname]
        path = os.path.join(self.sounds_dir, fname)
        try:
            result = subprocess.run(
                ["soxi", "-D", path],
                capture_output=True, text=True, timeout=5,
            )
            duration = float(result.stdout.strip())
        except (subprocess.TimeoutExpired, ValueError, OSError) as e:
            _log.warning("Clock: could not get duration for %s: %s", fname, e)
            duration = 3.0  # safe fallback
        self._duration_cache[fname] = duration
        return duration

    @property
    def is_active(self) -> bool:
        return self.interval > 0 and self.chime_style != "off"

    @property
    def uses_sound(self) -> bool:
        return self.chime_style in ("sound", "sound-speech")

    @property
    def uses_speech(self) -> bool:
        return self.chime_style in ("speech", "sound-speech")

    @property
    def uses_precision_timing(self) -> bool:
        return self.chime_style == "sound-speech"
