"""Clock for Orca -- periodic time announcements with chime sounds.

Orca 51 user extension. Install to $XDG_DATA_HOME/orca/extensions/ and
approve with ``orca --approve-extension clock``; Orca re-checks the hash
of every file in the package on each start, so re-approve after editing.
"""

__version__ = "2.0.0"

import logging

from orca import gsettings_registry
from orca.extension import Extension

# Import submodules as `from .module import name`, never as
# `from . import module`. The loader imports us as
# orca_user_extension.<name> but never creates `orca_user_extension`
# itself, and `from . import module` goes through __import__ with the
# full dotted path, which insists on importing that absent grandparent.
from .clock import open_settings as _open_settings
from .clock import settings_changed as _settings_changed
from .clock import start as _start_clock
from .clock import stop as _stop_clock
from .config import Config, migrate_legacy_settings

_log = logging.getLogger("orca-clock")

# Where Orca keeps per-extension settings: schema "extensions", one
# relocatable path per extension namespace, all under the single key
# "settings". The namespace is the extension's source id -- the name of
# this package -- which is the last component of __name__ once the
# loader has imported us as orca_user_extension.<namespace>.
_SETTINGS_SCHEMA = "extensions"
_SETTINGS_KEY = "settings"
_NAMESPACE = gsettings_registry.GSettingsRegistry.sanitize_gsettings_path(
    __name__.rsplit(".", 1)[-1]
)


class Clock(Extension):
    """Announces the time at a chosen interval, with optional chimes."""

    GROUP_LABEL = "Clock"
    DESCRIPTION = "Announces the time at a chosen interval, optionally with a chime, and can stay quiet during configured hours."
    VERSION = "2.0.0"  # keep in sync with __version__; read by AST, must be a literal
    AUTHOR = "Toby"
    WEBSITE = "https://github.com/heath-toby/Clock"

    def __init__(self) -> None:
        super().__init__()
        self._config: Config | None = None
        # Kept alive deliberately: the change signal stops firing once
        # the Gio.Settings object is garbage-collected.
        self._watch = None

    def _get_commands(self) -> list:
        """Returns this extension's keyboard commands."""

        from orca import command_manager, keybindings

        keybinding = keybindings.KeyBinding("c", keybindings.ORCA_CTRL_MODIFIER_MASK)
        return [
            command_manager.KeyboardCommand(
                name="clockSettings",
                function=_open_settings,
                group_label=self.GROUP_LABEL,
                description="Open Clock settings",
                desktop_keybinding=keybinding,
                laptop_keybinding=keybinding,
            )
        ]

    # get_preferences() is deliberately not overridden. Orca's generated
    # preferences dialog can only render the declarative kinds -- there
    # is no way to give it Clock's sound Preview and Test buttons, and it
    # stages values in memory, so the extension is not even told which
    # chime you are auditioning. Declaring preferences would therefore
    # produce a second, worse settings dialog alongside the real one. By
    # declaring none, the Settings button in Orca Preferences -> User
    # Extensions stays inactive and Orca+Ctrl+C remains the single place
    # Clock is configured.

    # --- Lifecycle ------------------------------------------------------

    def on_ready(self) -> None:
        """Imports legacy settings if needed, then starts announcing."""

        self._start()

    def on_enabled(self) -> None:
        """Starts announcing after a reload."""

        self._start()

    def on_disabled(self) -> None:
        """Stops announcing and drops the settings watch."""

        self._stop()

    def on_shutdown(self) -> None:
        """Deliberately does nothing.

        Orca runs shutdown hooks in a daemon thread with a timeout, but
        tearing this extension down means GLib source removal, GStreamer
        state changes and un-monkey-patching, all of which are main-thread
        work. Doing it off-thread risks a warning, an assertion or a hung
        exit -- and buys nothing, because the process is exiting anyway.
        on_disabled, which Orca does call on the main thread, still does
        the real teardown when the extension is disabled or reloaded.
        """

    def _start(self) -> None:
        if self._config is not None:
            return
        migrate_legacy_settings(self.settings)
        self._config = Config.load(self.settings)
        self._watch_settings()
        _start_clock(self._config)

    def _stop(self) -> None:
        _stop_clock()
        self._watch = None
        self._config = None

    # --- Reacting to settings changes -----------------------------------

    def _watch_settings(self) -> None:
        """Reschedule when the settings store changes.

        Orca's generated preferences dialog writes settings directly and
        does not call back into the extension, so without this a change
        made there would not take effect until the next reload.
        """
        if self._watch is not None:
            return
        try:
            registry = gsettings_registry.get_registry()
            gs = registry.get_settings(
                _SETTINGS_SCHEMA,
                registry.get_active_profile(),
                f"extensions/{_NAMESPACE}",
            )
            if gs is None:
                _log.warning("Clock: no settings object to watch; changes need a reload.")
                return
            gs.connect(f"changed::{_SETTINGS_KEY}", self._on_settings_key_changed)
            self._watch = gs
        except Exception as error:  # pylint: disable=broad-exception-caught
            _log.warning("Clock: could not watch settings (%s); changes need a reload.", error)

    def _on_settings_key_changed(self, _settings, _key) -> None:
        _settings_changed()
