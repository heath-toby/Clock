"""Configuration for Clock for Orca.

Values live in Orca's own per-extension settings store (dconf, under
``/org/gnome/orca/<profile>/extensions/clock/settings``), which is what
the Orca 51 extension system reads and writes. Clock used to keep them
in a private GSettings schema, ``org.gnome.Orca.Clock``; settings from
that schema are imported once, automatically, on first run -- see
``migrate_legacy_settings``.

The public attributes of ``Config`` are unchanged from the pre-extension
versions, so the hand-written settings dialog in ``config_ui`` works
against either backend without modification.
"""

from __future__ import annotations

import datetime
import logging
import os
import subprocess

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

_log = logging.getLogger("orca-clock")

# The private schema Clock used before the Orca 51 extension system.
LEGACY_SCHEMA_ID = "org.gnome.Orca.Clock"

# Bumped when the shape of the stored settings changes. Its presence also
# marks the legacy import as done, so it never runs twice -- including
# when the user has deliberately reset everything back to defaults.
SETTINGS_VERSION = 2
_VERSION_KEY = "settings-version"

VALID_INTERVALS = (0, 15, 30, 60)
VALID_STYLES = ("off", "speech", "sound", "sound-speech")

# 0=Monday .. 6=Sunday, matching datetime.weekday() and the ints the
# legacy "quiet-hours-days" key held.
DAY_KEYS = (
    "quiet-hours-monday",
    "quiet-hours-tuesday",
    "quiet-hours-wednesday",
    "quiet-hours-thursday",
    "quiet-hours-friday",
    "quiet-hours-saturday",
    "quiet-hours-sunday",
)

DEFAULT_INTERVAL = 0
DEFAULT_CHIME_STYLE = "off"
DEFAULT_CHIME_SOUND = "clock_chime1.wav"
DEFAULT_CHIME_VOLUME = 0.5
DEFAULT_INTERMEDIATE_ENABLED = False
DEFAULT_INTERMEDIATE_SOUND = "clock_chime3.wav"
DEFAULT_QUIET_HOURS_ENABLED = False
DEFAULT_QUIET_HOURS_START = "22:00"
DEFAULT_QUIET_HOURS_END = "07:00"
DEFAULT_QUIET_HOURS_DAYS = [0, 1, 2, 3, 4, 5, 6]


def sounds_dir() -> str:
    """Return the directory holding the bundled chime sounds.

    Resolved relative to this file so the package can be renamed or moved
    (extensions/, clock_v51/, ...) without breaking.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")


def list_sounds() -> list[str]:
    """Return the available chime filenames, sorted."""
    directory = sounds_dir()
    if not os.path.isdir(directory):
        return []
    return sorted(f for f in os.listdir(directory) if f.lower().endswith(".wav"))


def _legacy_gsettings() -> Gio.Settings | None:
    """Return the old private-schema Gio.Settings, or None if not installed."""
    user_schema_dir = os.path.join(
        os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
        "glib-2.0", "schemas",
    )
    default_source = Gio.SettingsSchemaSource.get_default()
    try:
        source = Gio.SettingsSchemaSource.new_from_directory(
            user_schema_dir, default_source, False,
        )
    except GLib.Error:
        source = default_source
    if source is None:
        return None
    schema = source.lookup(LEGACY_SCHEMA_ID, True)
    if schema is None:
        return None
    return Gio.Settings.new_full(schema, None, None)


def migrate_legacy_settings(settings) -> bool:
    """Import settings from the pre-extension GSettings schema, once.

    ``settings`` is the extension's ExtensionSettings. Returns True if
    values were actually imported. Writes the version marker either way,
    so a missing or already-migrated legacy schema costs one lookup at
    most once in the life of the install.
    """
    if settings.get(_VERSION_KEY) is not None:
        return False

    legacy = _legacy_gsettings()
    if legacy is None:
        _log.info("Clock: no legacy %s schema to import from; using defaults.", LEGACY_SCHEMA_ID)
        settings.set(_VERSION_KEY, SETTINGS_VERSION)
        return False

    try:
        settings.set("interval", legacy.get_int("interval"))
        settings.set("chime-style", legacy.get_string("chime-style"))
        settings.set("chime-sound", legacy.get_string("chime-sound"))
        settings.set("chime-volume", legacy.get_double("chime-volume"))
        settings.set("intermediate-enabled", legacy.get_boolean("intermediate-enabled"))
        settings.set("intermediate-sound", legacy.get_string("intermediate-sound"))
        settings.set("quiet-hours-enabled", legacy.get_boolean("quiet-hours-enabled"))
        settings.set("quiet-hours-start", legacy.get_string("quiet-hours-start"))
        settings.set("quiet-hours-end", legacy.get_string("quiet-hours-end"))
        days = set(legacy.get_value("quiet-hours-days").unpack())
        for index, key in enumerate(DAY_KEYS):
            settings.set(key, index in days)
    except (GLib.Error, TypeError, ValueError) as error:
        # Leave the version marker unset so a later run can try again
        # rather than silently stranding the user on defaults.
        _log.error("Clock: importing legacy settings failed: %s", error)
        return False

    settings.set(_VERSION_KEY, SETTINGS_VERSION)
    _log.info("Clock: imported settings from the legacy %s schema.", LEGACY_SCHEMA_ID)
    return True


class Config:
    """Clock configuration, backed by Orca's per-extension settings."""

    def __init__(self, settings=None):
        self._settings = settings
        self.interval: int = DEFAULT_INTERVAL
        self.chime_style: str = DEFAULT_CHIME_STYLE
        self.chime_sound: str = DEFAULT_CHIME_SOUND
        self.chime_volume: float = DEFAULT_CHIME_VOLUME
        self.intermediate_enabled: bool = DEFAULT_INTERMEDIATE_ENABLED
        self.intermediate_sound: str = DEFAULT_INTERMEDIATE_SOUND
        self.quiet_hours_enabled: bool = DEFAULT_QUIET_HOURS_ENABLED
        self.quiet_hours_start: str = DEFAULT_QUIET_HOURS_START
        self.quiet_hours_end: str = DEFAULT_QUIET_HOURS_END
        self.quiet_hours_days: list[int] = list(DEFAULT_QUIET_HOURS_DAYS)
        self._duration_cache: dict[str, float] = {}

    @classmethod
    def load(cls, settings=None) -> Config:
        """Read the current settings into a new Config."""
        cfg = cls(settings)
        cfg.reload()
        return cfg

    def reload(self) -> None:
        """Re-read every value from the settings store."""
        settings = self._settings
        if settings is None:
            _log.warning("Clock: no settings store; using defaults.")
            return

        self.interval = int(settings.get("interval", DEFAULT_INTERVAL))
        self.chime_style = str(settings.get("chime-style", DEFAULT_CHIME_STYLE))
        self.chime_sound = str(settings.get("chime-sound", DEFAULT_CHIME_SOUND))
        self.chime_volume = float(settings.get("chime-volume", DEFAULT_CHIME_VOLUME))
        self.intermediate_enabled = bool(
            settings.get("intermediate-enabled", DEFAULT_INTERMEDIATE_ENABLED)
        )
        self.intermediate_sound = str(
            settings.get("intermediate-sound", DEFAULT_INTERMEDIATE_SOUND)
        )
        self.quiet_hours_enabled = bool(
            settings.get("quiet-hours-enabled", DEFAULT_QUIET_HOURS_ENABLED)
        )
        self.quiet_hours_start = str(settings.get("quiet-hours-start", DEFAULT_QUIET_HOURS_START))
        self.quiet_hours_end = str(settings.get("quiet-hours-end", DEFAULT_QUIET_HOURS_END))
        self.quiet_hours_days = [
            index
            for index, key in enumerate(DAY_KEYS)
            if bool(settings.get(key, index in DEFAULT_QUIET_HOURS_DAYS))
        ]

        # A bad value here would mean a silent clock or a crash in the
        # scheduler, so fall back rather than trust the store.
        if self.interval not in VALID_INTERVALS:
            _log.warning("Clock: ignoring invalid interval %r", self.interval)
            self.interval = DEFAULT_INTERVAL
        if self.chime_style not in VALID_STYLES:
            _log.warning("Clock: ignoring invalid chime style %r", self.chime_style)
            self.chime_style = DEFAULT_CHIME_STYLE
        self.chime_volume = min(max(self.chime_volume, 0.0), 1.0)

    def save(self) -> None:
        """Write every value back to the settings store."""
        settings = self._settings
        if settings is None:
            _log.error("Clock: cannot save, no settings store.")
            return

        settings.set("interval", int(self.interval))
        settings.set("chime-style", str(self.chime_style))
        settings.set("chime-sound", str(self.chime_sound))
        settings.set("chime-volume", float(self.chime_volume))
        settings.set("intermediate-enabled", bool(self.intermediate_enabled))
        settings.set("intermediate-sound", str(self.intermediate_sound))
        settings.set("quiet-hours-enabled", bool(self.quiet_hours_enabled))
        settings.set("quiet-hours-start", str(self.quiet_hours_start))
        settings.set("quiet-hours-end", str(self.quiet_hours_end))
        selected = set(self.quiet_hours_days)
        for index, key in enumerate(DAY_KEYS):
            settings.set(key, index in selected)

    @property
    def sounds_dir(self) -> str:
        """Directory holding the bundled chime sounds."""
        return sounds_dir()

    def list_sounds(self) -> list[str]:
        """Available chime filenames, sorted."""
        return list_sounds()

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

    @staticmethod
    def _parse_hhmm(text: str) -> tuple[int, int] | None:
        try:
            h, m = text.strip().split(":")
            h_i, m_i = int(h), int(m)
            if 0 <= h_i < 24 and 0 <= m_i < 60:
                return h_i, m_i
        except (ValueError, AttributeError):
            pass
        return None

    def is_in_quiet_hours(self, when: datetime.datetime | None = None) -> bool:
        """Return True if `when` (default: now) falls in a configured quiet window."""
        if not self.quiet_hours_enabled:
            return False
        start = self._parse_hhmm(self.quiet_hours_start)
        end = self._parse_hhmm(self.quiet_hours_end)
        if start is None or end is None:
            return False
        if start == end:
            return False
        if not self.quiet_hours_days:
            return False

        now = when or datetime.datetime.now()
        # Each quiet window begins at `start` on one of the selected days.
        # If end <= start, the window crosses midnight and ends the next day.
        # Check windows anchored today and yesterday so that overnight windows catch us.
        for days_ago in (0, 1):
            anchor = (now - datetime.timedelta(days=days_ago)).replace(
                hour=start[0], minute=start[1], second=0, microsecond=0,
            )
            if anchor.weekday() not in self.quiet_hours_days:
                continue
            end_dt = anchor.replace(hour=end[0], minute=end[1])
            if end_dt <= anchor:
                end_dt += datetime.timedelta(days=1)
            if anchor <= now < end_dt:
                return True
        return False
