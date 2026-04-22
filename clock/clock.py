"""Clock for Orca -- core timer, sound playback, and speech logic.

Provides periodic time announcements at configurable intervals with
optional chime sounds. Chime styles:
  - off:          No announcements
  - speech:       Orca speaks the time at each interval boundary
  - sound:        A chime plays at each interval boundary
  - sound-speech: Chime is timed to finish at the boundary, then Orca speaks
"""

from __future__ import annotations

import datetime
import logging
import math
import os
import subprocess
import threading

import gi
gi.require_version("Gio", "2.0")
from gi.repository import GLib

from orca import command_manager, keybindings, script_manager, system_information_presenter

from .config import Config

_log = logging.getLogger("orca-clock")


# BBC pips (clock_cuckoo7.wav): 5 short pips then a 6th long pip marking the hour.
# The final long pip starts at 5.22s into the 5.91s file.
# Speech should fire WITH the 6th pip, not after the file ends.
_BBC_PIPS_FILE = "clock_cuckoo7.wav"
_BBC_PIPS_FINAL_ONSET = 5.22  # seconds into file where the long pip starts

# Module state
_config: Config | None = None
_timer_source_id: int | None = None
_lock = threading.Lock()


def _speak_time() -> None:
    """Use Orca's own present_time, respecting the user's time format setting."""
    presenter = system_information_presenter.get_presenter()
    script = script_manager.get_manager().get_active_script()
    presenter.present_time(script)


def _cancel_timer() -> None:
    """Cancel any pending timer. Must be called from main thread."""
    global _timer_source_id
    with _lock:
        if _timer_source_id is not None:
            GLib.source_remove(_timer_source_id)
            _timer_source_id = None


def _next_boundary(config: Config) -> tuple[float, bool]:
    """Return (seconds until next boundary, whether that boundary is on the hour)."""
    now = datetime.datetime.now()
    minute = now.minute
    second = now.second + now.microsecond / 1_000_000

    past = minute % config.interval
    minutes_until = config.interval - past
    secs_until = (minutes_until * 60) - second

    # Which minute will the boundary land on?
    target_minute = (minute + minutes_until) % 60
    is_hourly = target_minute == 0

    return secs_until, is_hourly


def _schedule_next(config: Config) -> None:
    """Schedule the next announcement. Must be called from main thread."""
    global _timer_source_id
    _cancel_timer()

    if not config.is_active or config.interval <= 0:
        return

    secs_until_boundary, is_hourly = _next_boundary(config)

    # For sound-speech mode, start the chime early so it finishes on the boundary
    # BBC pips special case: align the final long pip with the boundary
    if config.uses_precision_timing:
        if is_hourly or not config.intermediate_enabled:
            sound_file = config.chime_sound
        else:
            sound_file = config.intermediate_sound
        if sound_file == _BBC_PIPS_FILE:
            # Start so the final pip lands on the boundary
            delay = secs_until_boundary - _BBC_PIPS_FINAL_ONSET
        else:
            chime_dur = config.get_chime_duration(sound_file)
            delay = secs_until_boundary - chime_dur
        if delay < 0.5:
            delay += config.interval * 60
    elif config.uses_sound:
        # Sound-only: most sounds play at the boundary, but BBC pips
        # need to start early so the 6th pip lands on the boundary.
        if is_hourly or not config.intermediate_enabled:
            sound_file = config.chime_sound
        else:
            sound_file = config.intermediate_sound
        if sound_file == _BBC_PIPS_FILE:
            delay = secs_until_boundary - _BBC_PIPS_FINAL_ONSET
        else:
            delay = secs_until_boundary
        if delay < 0.5:
            delay += config.interval * 60
    else:
        # Speech-only: add a small buffer so we fire just after :00, never before
        delay = secs_until_boundary + 0.1
        if delay < 0.5:
            delay += config.interval * 60

    delay_ms = max(int(delay * 1000), 100)

    with _lock:
        _timer_source_id = GLib.timeout_add(delay_ms, _on_timer, config)

    now = datetime.datetime.now()
    next_time = now + datetime.timedelta(seconds=delay)
    _log.info(
        "Clock: next %s announcement at ~%s (%.1fs, %s)",
        config.chime_style, next_time.strftime("%H:%M:%S"), delay,
        "hourly" if is_hourly else "intermediate",
    )


def _on_timer(config: Config) -> bool:
    """GLib timeout callback. Runs on the main thread."""
    global _timer_source_id
    with _lock:
        _timer_source_id = None

    if config.interval <= 0:
        return False

    # Verify we haven't drifted too far (e.g. system woke from suspend)
    now = datetime.datetime.now()
    minute = now.minute
    second = now.second + now.microsecond / 1_000_000
    past = minute % config.interval
    secs_to_boundary = (config.interval - past) * 60 - second
    # Normalize: if past == 0 and second < 2, we're right on time
    if past == 0 and second < 15:
        secs_to_boundary = -second  # we're just past the boundary

    if config.uses_precision_timing:
        max_drift = config.get_chime_duration() + 3
    elif config.uses_sound and config.chime_sound == _BBC_PIPS_FILE:
        max_drift = _BBC_PIPS_FINAL_ONSET + 3
    else:
        max_drift = 5
    if abs(secs_to_boundary) > max_drift and secs_to_boundary < config.interval * 60 - max_drift:
        _log.info("Clock: timer drifted (%.1fs to boundary), rescheduling", secs_to_boundary)
        _schedule_next(config)
        return False

    # Determine if the upcoming boundary is on the hour.
    # We can't just check now.minute — in precision mode the timer fires
    # seconds before the boundary, so we calculate the next boundary directly.
    now = datetime.datetime.now()
    secs_into_hour = now.minute * 60 + now.second + now.microsecond / 1_000_000
    interval_secs = config.interval * 60
    target_minute = math.ceil(secs_into_hour / interval_secs) * config.interval % 60
    is_hourly = target_minute == 0

    # Skip announcement if the boundary falls in quiet hours.
    # Check against the boundary time (not fire time), since we may fire early.
    boundary_time = now + datetime.timedelta(seconds=max(secs_to_boundary, 0))
    if config.is_in_quiet_hours(boundary_time):
        _log.info("Clock: quiet hours active, skipping announcement")
        _schedule_next(config)
        return False

    # Dispatch based on chime style
    if config.chime_style == "speech":
        _speak_time()
        _schedule_next(config)
    elif config.chime_style == "sound":
        _play_sound_async(config, speak_after=False, is_hourly=is_hourly)
    elif config.chime_style == "sound-speech":
        _play_sound_async(config, speak_after=True, is_hourly=is_hourly)

    return False  # one-shot


def _play_sound_async(config: Config, speak_after: bool, is_hourly: bool = True) -> None:
    """Play the chime in a background thread, optionally speak after."""
    chime_path = config.get_chime_path(is_hourly=is_hourly)
    if not os.path.isfile(chime_path):
        _log.warning("Clock: chime file not found: %s, falling back to speech", chime_path)
        _speak_time()
        _schedule_next(config)
        return

    sound_file = os.path.basename(chime_path)
    is_bbc_pips = sound_file == _BBC_PIPS_FILE

    def _worker():
        try:
            if is_bbc_pips and speak_after:
                # BBC pips: speak WITH the final long pip, not after the file ends.
                # The sound was started so the final pip lands on the boundary.
                # Wait until the final pip onset, then fire speech while sound continues.
                proc = subprocess.Popen(["pw-play", "--volume", str(config.chime_volume), chime_path])
                # Sleep until the final pip begins
                import time as _time
                _time.sleep(_BBC_PIPS_FINAL_ONSET)
                GLib.idle_add(_speak_time)
                proc.wait()  # let the long pip finish playing
                GLib.idle_add(_schedule_next, config)
            else:
                subprocess.run(["pw-play", "--volume", str(config.chime_volume), chime_path], timeout=15)
                if speak_after:
                    GLib.idle_add(_speak_and_reschedule, config)
                else:
                    GLib.idle_add(_schedule_next, config)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            _log.warning("Clock: chime playback failed: %s", e)
            if speak_after:
                GLib.idle_add(_speak_and_reschedule, config)
            else:
                GLib.idle_add(_schedule_next, config)

    threading.Thread(target=_worker, daemon=True).start()


def _speak_and_reschedule(config: Config) -> bool:
    """Speak the time, then reschedule. Called on main thread via GLib.idle_add."""
    _speak_time()
    _schedule_next(config)
    return False


def _open_settings(script, event=None):
    """Keybinding handler for Orca+Ctrl+C."""
    GLib.idle_add(_show_settings_ui)
    return True


def _show_settings_ui() -> bool:
    """Open the settings dialog on the main thread."""
    global _config
    try:
        from .config_ui import show_settings_dialog
        show_settings_dialog(_config, on_save=_on_settings_saved)
    except Exception as e:
        _log.error("Clock: could not open settings: %s", e)
        try:
            from orca import presentation_manager
            presentation_manager.get_manager().present_message(f"Error opening clock settings: {e}")
        except Exception:
            pass
    return False


def _on_settings_saved(config: Config) -> None:
    """Callback when settings are saved from the UI."""
    global _config
    _config = config
    _schedule_next(config)


# --- Keybinding registration ---

_keybinding_registered = False


def _register_keybinding() -> bool:
    """Register Orca+Ctrl+C for the settings dialog. Called via GLib.idle_add."""
    global _keybinding_registered
    if _keybinding_registered:
        return False
    try:
        manager = command_manager.get_manager()
        kb = keybindings.KeyBinding("c", keybindings.ORCA_CTRL_MODIFIER_MASK)
        manager.add_command(
            command_manager.KeyboardCommand(
                name="clockSettings",
                function=_open_settings,
                group_label="Clock",
                description="Open Clock settings",
                desktop_keybinding=kb,
                laptop_keybinding=kb,
            )
        )
        _keybinding_registered = True
        _log.info("Clock: keybinding Orca+Ctrl+C registered")
    except Exception as e:
        _log.error("Clock: failed to register keybinding: %s", e)
    return False


# --- Public entry point ---

def register() -> None:
    """Register the Clock add-on with Orca. Called from orca-customizations.py."""
    global _config
    _config = Config.load()
    _schedule_next(_config)
    GLib.idle_add(_register_keybinding)
    _log.info("Clock: registered (interval=%d, style=%s)", _config.interval, _config.chime_style)
