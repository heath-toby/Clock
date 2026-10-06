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

from gi.repository import GLib

from orca import script_manager, system_information_presenter

from .config import Config

_log = logging.getLogger("orca-clock")


# BBC pips (clock_cuckoo7.wav): 5 short pips then a 6th long pip marking the hour.
# The final long pip starts at 5.22s into the 5.91s file.
# Speech should fire WITH the 6th pip, not after the file ends.
_BBC_PIPS_FILE = "clock_cuckoo7.wav"
_BBC_PIPS_FINAL_ONSET = 5.22  # seconds into file where the long pip starts

# How late the announcement lands, as a whole: the chime's significant
# instant -- the sixth pip, or the last note of a chime that is timed to
# finish on the boundary -- and the speech that goes with it, together.
#
# It used to be a bias on the speech alone, and for a reason: Orca's
# present_time reads the clock when it speaks, and its default "%X" format
# includes seconds in most locales, so firing one millisecond early made it
# announce 16:59:59 instead of 17:00:00. Plenty jitters by more than a
# millisecond -- the audio graph's quantum, pw-play start-up, and over a
# timer armed half an hour ago, NTP slewing the wall clock against the
# monotonic clock GLib counts on. So the speech was held back a quarter of a
# second and the chime was not, which left the sixth pip a quarter second
# ahead of the voice: the pip marked the hour and the voice arrived after it.
#
# The speech no longer reads the clock -- it announces the boundary it was
# scheduled for, whenever it happens to fire -- so this is no longer holding
# anything together. It is now purely where the announcement sits, and the
# chime and the speech both read it, so they cannot drift apart. Sound-only
# mode has no voice to meet and does not use it: there the pip marks the
# hour and nothing else is competing for the moment.
_ANNOUNCEMENT_BIAS_SECONDS = 0.25

# How far past its boundary a timer may wake and still announce. Generous on
# purpose: the announcement names the boundary it was scheduled for, so a late
# one is still true, and saying "17:00" five seconds late beats saying nothing.
# Beyond this the information is stale and we start again.
_LATE_TOLERANCE_SECONDS = 30.0

# Most we will wait in one hop, and how many hops before giving up and
# just speaking. Guards against a bad boundary parking the announcement.
_SPEECH_WAIT_CAP_SECONDS = 10.0
_SPEECH_MAX_ATTEMPTS = 5


def _chime_moment(config: Config, is_hourly: bool) -> tuple[str, float]:
    """Return the chime file for a boundary, and where its moment falls in it.

    The offset is how far into the file the instant that marks the boundary
    sits. For the BBC pips that is the onset of the sixth, long pip, which is
    the convention: the pip starts on the hour, it does not end on it. For
    anything else in sound-speech mode it is the whole duration, so the chime
    finishes as the voice begins. Sound-only mode asks for nothing but the
    file and starts it on the boundary, the pips excepted.

    Lives here rather than inline because three callers need the same answer
    -- scheduling, the drift check, and sound-only scheduling -- and when
    they each worked it out for themselves they disagreed: the drift check
    measured the hourly chime's duration even at an intermediate boundary.
    """
    if is_hourly or not config.intermediate_enabled:
        sound_file = config.chime_sound
    else:
        sound_file = config.intermediate_sound

    if sound_file == _BBC_PIPS_FILE:
        return sound_file, _BBC_PIPS_FINAL_ONSET
    return sound_file, config.get_chime_duration(sound_file)


# Module state
_config: Config | None = None
_timer_source_id: int | None = None
# A speech still waiting out the last fraction of a second before its
# boundary. Held separately from _timer_source_id because _schedule_next
# cancels that one every time it runs, and in sound-then-speech mode it runs
# while the voice is still waiting: cancelling both there would swallow the
# announcement the chime was just played for.
_speech_source_id: int | None = None
_last_announced_boundary: datetime.datetime | None = None
_lock = threading.Lock()


def _boundary_datetime(now: datetime.datetime, interval: int) -> datetime.datetime:
    """Return the datetime of the next interval boundary strictly after `now`."""
    secs_into_hour = now.minute * 60 + now.second + now.microsecond / 1_000_000
    interval_secs = interval * 60
    # How many full intervals into the hour are we?
    n = math.ceil(secs_into_hour / interval_secs)
    # Target total minutes in the hour, possibly overflowing to the next hour
    target_total_mins = n * interval
    boundary = now.replace(minute=0, second=0, microsecond=0)
    boundary += datetime.timedelta(minutes=target_total_mins)
    # Guard against floating-point edge: if "now" is exactly on a boundary
    # (n*interval_secs == secs_into_hour), math.ceil returns n, giving us
    # the current moment — push forward one interval.
    if boundary <= now:
        boundary += datetime.timedelta(minutes=interval)
    return boundary


def _orca_time_format() -> str | None:
    """The strftime format Orca is configured to announce the time in.

    Orca's public ``get_time_format`` returns the name of the format rather
    than the format itself ("twentyfour_hms"), so the name is looked up in
    Orca's own enum to get the string. Returns None if either half of that
    is not where it used to be, which is the caller's signal to fall back to
    letting Orca speak the time itself.
    """
    try:
        from orca.system_information_presenter import TimeFormat
        presenter = system_information_presenter.get_presenter()
        name = presenter.get_time_format()
        for fmt in TimeFormat:
            if fmt.string_name == name:
                return fmt.value
    except Exception as e:  # pylint: disable=broad-exception-caught
        _log.debug("Clock: could not read Orca's time format: %s", e)
    return None


def _speak_time(moment: datetime.datetime | None = None) -> None:
    """Announce a time, in whatever format Orca is configured for.

    With a ``moment``, that moment is announced rather than the current one.
    This is the whole reason the announcement can be placed by ear instead of
    by necessity: Orca's own ``present_time`` reads the clock at the instant
    it speaks, so firing a millisecond before the hour made it say 16:59:59,
    and the speech had to be held back far enough to be sure of the second.
    Announcing the boundary we were scheduled for cannot be wrong early or
    late, so where the voice sits became a question of what sounds right --
    which is how the chime came to be the thing that moves.
    """
    time_format = _orca_time_format() if moment is not None else None
    if time_format is not None:
        from orca import presentation_manager
        presentation_manager.get_manager().present_message(
            moment.strftime(time_format)
        )
        return

    # No moment asked for, or Orca's format is not where we expect it: let
    # Orca read the clock itself, exactly as it does for its own command.
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


def _cancel_pending_speech() -> None:
    """Cancel a speech still waiting for its boundary. Main thread only.

    Only stop() calls this -- see the note on _speech_source_id.
    """
    global _speech_source_id
    with _lock:
        if _speech_source_id is not None:
            GLib.source_remove(_speech_source_id)
            _speech_source_id = None


def _announcement_delay(
    config: Config, boundary: datetime.datetime, now: datetime.datetime,
    is_hourly: bool,
) -> float:
    """Seconds from ``now`` until the timer should fire for ``boundary``.

    One function, so the delay and the boundary it is for are always derived
    from each other. They used to be worked out separately, and when the
    delay was pushed on by an interval the boundary was not, which is how a
    timer came to be armed for one boundary while the code believed it was
    for another.
    """
    secs = (boundary - now).total_seconds()

    if config.uses_precision_timing:
        # Sound and speech together. Start the chime early enough that its
        # significant instant -- the sixth pip, or the final note -- lands
        # where the voice does, which is the boundary plus the bias. Both
        # read the one constant, so moving the announcement moves the pair.
        _sound_file, moment_offset = _chime_moment(config, is_hourly)
        return secs - moment_offset + _ANNOUNCEMENT_BIAS_SECONDS

    if config.uses_sound:
        # Sound only, so there is no voice to meet: the chime's significant
        # instant lands on the boundary itself. For the pips that means
        # starting a sixth-pip's-worth early; for everything else the sound
        # simply begins on the hour, since nothing about it marks the moment.
        sound_file, moment_offset = _chime_moment(config, is_hourly)
        if sound_file == _BBC_PIPS_FILE:
            return secs - moment_offset
        return secs

    # Speech only: land just past the boundary and let the wait in
    # _speak_time_at_boundary place the voice.
    return secs + 0.1


def _schedule_next(config: Config) -> None:
    """Schedule the next announcement. Must be called from main thread."""
    global _timer_source_id

    # A chime's worker thread reschedules when playback finishes, several
    # seconds after _on_timer returned. By then the extension may have been
    # disabled, or the settings dialog may have handed over a new config. The
    # captured one is stale either way, and arming a timer from it would
    # restart a clock that had been stopped.
    if _config is None:
        _log.info("Clock: not rescheduling; the clock is stopped.")
        return
    if config is not _config:
        _log.info("Clock: ignoring a reschedule for a config that is no longer live.")
        return

    _cancel_timer()

    if not config.is_active or config.interval <= 0:
        return

    now = datetime.datetime.now()
    boundary = _boundary_datetime(now, config.interval)
    if _last_announced_boundary == boundary:
        boundary += datetime.timedelta(minutes=config.interval)

    delay = _announcement_delay(config, boundary, now, boundary.minute == 0)
    if delay < 0.5:
        # Not enough runway left to start this boundary's chime, so take the
        # next one -- and move the boundary with it, because that is what the
        # timer will be announcing.
        boundary += datetime.timedelta(minutes=config.interval)
        delay = _announcement_delay(config, boundary, now, boundary.minute == 0)

    is_hourly = boundary.minute == 0
    delay_ms = max(int(delay * 1000), 100)

    with _lock:
        _timer_source_id = GLib.timeout_add(
            delay_ms, _on_timer, config, boundary, is_hourly
        )

    _log.info(
        "Clock: next %s announcement for %s, timer in %.1fs (%s)",
        config.chime_style, boundary.strftime("%H:%M:%S"), delay,
        "hourly" if is_hourly else "intermediate",
    )


def _on_timer(
    config: Config, boundary_time: datetime.datetime, is_hourly: bool,
) -> bool:
    """GLib timeout callback. Runs on the main thread.

    ``boundary_time`` is the boundary this timer was armed for, handed over
    rather than worked out again here. Re-deriving it was wrong whenever the
    timer fired *after* the boundary, which speech-only mode does by design:
    "the next boundary strictly after now" was then the following one, so the
    announcement was recorded against a boundary half an hour away and that
    one was skipped as already done.
    """
    global _timer_source_id
    with _lock:
        _timer_source_id = None

    if config.interval <= 0:
        return False

    now = datetime.datetime.now()
    secs_to_boundary = (boundary_time - now).total_seconds()

    # The timer is armed deliberately early, by the offset of the chime's
    # significant instant. Much earlier than that, or well past the boundary,
    # means the clock moved under us -- a suspend, or NTP stepping it -- so
    # start again. The late tolerance is generous because a late announcement
    # still says the right thing: it announces its boundary, not the clock.
    if config.uses_sound:
        _sound_file, moment_offset = _chime_moment(config, is_hourly)
        too_early = moment_offset + 3
    else:
        too_early = 3
    if secs_to_boundary > too_early or secs_to_boundary < -_LATE_TOLERANCE_SECONDS:
        _log.info(
            "Clock: timer woke %+.1fs from its boundary %s, rescheduling",
            secs_to_boundary, boundary_time.strftime("%H:%M:%S"),
        )
        _schedule_next(config)
        return False

    # Refuse to announce for a boundary we've already announced
    global _last_announced_boundary
    if _last_announced_boundary == boundary_time:
        _log.info("Clock: boundary %s already announced, skipping", boundary_time)
        _schedule_next(config)
        return False

    # Skip announcement if the boundary falls in quiet hours
    if config.is_in_quiet_hours(boundary_time):
        _log.info("Clock: quiet hours active, skipping announcement")
        _schedule_next(config)
        return False

    _last_announced_boundary = boundary_time

    # Dispatch based on chime style
    if config.chime_style == "speech":
        _speak_time_at_boundary(boundary_time)
        _schedule_next(config)
    elif config.chime_style == "sound":
        _play_sound_async(config, speak_after=False, is_hourly=is_hourly, boundary_time=boundary_time)
    elif config.chime_style == "sound-speech":
        _play_sound_async(config, speak_after=True, is_hourly=is_hourly, boundary_time=boundary_time)

    return False  # one-shot


def _play_sound_async(
    config: Config,
    speak_after: bool,
    is_hourly: bool = True,
    boundary_time: datetime.datetime | None = None,
) -> None:
    """Play the chime in a background thread, optionally speak after."""
    chime_path = config.get_chime_path(is_hourly=is_hourly)
    if not os.path.isfile(chime_path):
        _log.warning("Clock: chime file not found: %s, falling back to speech", chime_path)
        _speak_time_at_boundary(boundary_time)
        _schedule_next(config)
        return

    sound_file = os.path.basename(chime_path)
    is_bbc_pips = sound_file == _BBC_PIPS_FILE

    def _worker():
        try:
            if is_bbc_pips and speak_after:
                # BBC pips: speak WITH the final long pip, not after the file
                # ends. The sound was started so the final pip lands on the
                # boundary. The speech is handed straight to the main loop,
                # which waits out the remaining wall-clock time itself --
                # previously this thread slept for the pip onset and spoke on
                # waking, which pinned the announcement to when playback
                # happened to start rather than to the boundary.
                proc = subprocess.Popen(["pw-play", "--volume", str(config.chime_volume), chime_path])
                GLib.idle_add(_speak_time_at_boundary, boundary_time)
                try:
                    # Bounded, unlike the bare wait this used to be: nothing
                    # reschedules until playback ends, so a wedged pw-play
                    # stopped the clock permanently rather than for one chime.
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    _log.warning("Clock: chime playback did not finish; killing it.")
                    proc.kill()
                GLib.idle_add(_schedule_next, config)
            else:
                subprocess.run(["pw-play", "--volume", str(config.chime_volume), chime_path], timeout=15)
                if speak_after:
                    GLib.idle_add(_speak_and_reschedule, config, boundary_time)
                else:
                    GLib.idle_add(_schedule_next, config)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
            _log.warning("Clock: chime playback failed: %s", e)
            if speak_after:
                GLib.idle_add(_speak_and_reschedule, config, boundary_time)
            else:
                GLib.idle_add(_schedule_next, config)

    threading.Thread(target=_worker, daemon=True).start()


def _speak_time_at_boundary(
    boundary_time: datetime.datetime | None,
    attempts: int = 0,
) -> bool:
    """Speak the time, never before the boundary it is meant to announce.

    Which second gets announced no longer depends on when this runs -- the
    boundary is announced, not the clock -- so the wait is about where the
    voice sits against the chime, not about being right. It still waits
    rather than trusting whatever woke it (a worker thread's sleep, an idle
    callback, a timer armed half an hour ago), because the chime was started
    on the strength of the same boundary and the two have to meet. Called on
    the main thread.
    """
    global _speech_source_id
    with _lock:
        _speech_source_id = None

    if _config is None:
        # Disabled while the chime was still playing, or while this was
        # waiting. Nothing to announce for a clock that has been stopped.
        return False

    if boundary_time is not None and attempts < _SPEECH_MAX_ATTEMPTS:
        remaining = (
            boundary_time - datetime.datetime.now()
        ).total_seconds() + _ANNOUNCEMENT_BIAS_SECONDS
        if remaining > 0:
            wait_ms = int(min(remaining, _SPEECH_WAIT_CAP_SECONDS) * 1000) + 5
            with _lock:
                _speech_source_id = GLib.timeout_add(
                    wait_ms, _speak_time_at_boundary, boundary_time, attempts + 1
                )
            return False
    if boundary_time is not None:
        # Logged so a mis-announced second can be confirmed from the log
        # rather than inferred: a positive offset is what we want.
        offset = (datetime.datetime.now() - boundary_time).total_seconds()
        _log.info(
            "Clock: announcing %s at %+.3fs from the boundary (attempt %d)",
            boundary_time.strftime("%H:%M:%S"), offset, attempts,
        )
    _speak_time(boundary_time)
    return False


def _speak_and_reschedule(config: Config, boundary_time: datetime.datetime | None = None) -> bool:
    """Speak the time (at the boundary), then reschedule. Called on main thread."""
    _speak_time_at_boundary(boundary_time)
    _schedule_next(config)
    return False


def open_settings() -> bool:
    """Command handler for Orca+Ctrl+C. Opens the hand-written dialog."""
    GLib.idle_add(_show_settings_ui)
    return True


def _show_settings_ui() -> bool:
    """Open the settings dialog on the main thread."""
    if _config is None:
        _log.error("Clock: settings requested before the extension was started.")
        return False
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
    """Callback when settings are saved from the hand-written dialog."""
    global _config
    _config = config
    _schedule_next(config)


# --- Lifecycle, driven by the Extension in __init__.py ---

def start(config: Config) -> None:
    """Start announcing on `config`'s schedule."""
    global _config
    _config = config
    _schedule_next(config)
    _log.info("Clock: started (interval=%d, style=%s)", config.interval, config.chime_style)


def stop() -> None:
    """Cancel any pending announcement and forget the config."""
    global _config, _last_announced_boundary
    _cancel_timer()
    _cancel_pending_speech()
    _config = None
    _last_announced_boundary = None
    _log.info("Clock: stopped.")


def settings_changed() -> None:
    """Re-read the settings and reschedule.

    Called when anything writes the store -- Orca's own generated
    preferences dialog does so directly, without telling the extension,
    so this is driven off a GSettings change signal rather than off the
    dialog.
    """
    if _config is None:
        return
    _config.reload()
    _log.info(
        "Clock: settings changed (interval=%d, style=%s); rescheduling.",
        _config.interval, _config.chime_style,
    )
    _schedule_next(_config)
