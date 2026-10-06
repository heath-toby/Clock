"""Where every piece of the announcement lands, in one run.

Run against the installed extension, after ./install.sh:

    python3 tests-timing.py

The clock and the GLib timer are both stubbed, so this measures what
_schedule_next actually decides rather than re-deriving it. Every row must
stay green: the whole point of the design is that the chime's significant
instant and the voice are placed by one constant and cannot drift apart.
"""
import datetime
import os
import sys

import orca.extension_loader  # noqa: F401
sys.path.insert(0, os.path.expanduser("~/.local/share/orca/extensions"))
from gi.repository import GLib  # noqa: E402

from clock import clock as C  # noqa: E402
from clock.config import Config  # noqa: E402

cfg = Config()
cfg.interval = 30
cfg.chime_sound = "clock_cuckoo7.wav"
cfg.intermediate_sound = "clock_chime3.wav"
cfg.intermediate_enabled = True
cfg.quiet_hours_enabled = False
# _schedule_next refuses to arm a timer unless this is the live config -- that
# is what keeps a finished chime from restarting a stopped clock -- so the
# module has to believe it has been started.
C._config = cfg

# Measured from the files themselves; see _BBC_PIPS_FINAL_ONSET.
PIPS_ONSET, CHIME3_DUR = 5.220, 1.578
BIAS = C._ANNOUNCEMENT_BIAS_SECONDS

# (delay_ms, boundary, is_hourly) for each timer the code arms.
captured: list[tuple] = []
C.GLib.timeout_add = lambda ms, _cb, *a: (captured.append((ms,) + a), len(captured))[1]
C.GLib.source_remove = lambda sid: True
assert GLib is not None  # imported for the real module's sake


class FrozenNow(datetime.datetime):
    """A datetime whose now() is wherever we put it."""

    frozen: "FrozenNow"

    @classmethod
    def now(cls, tz=None):
        return cls.frozen


_real_datetime = C.datetime.datetime


def schedule(style, at):
    """Return how many seconds _schedule_next would wait, from `at`."""
    cfg.chime_style = style
    captured.clear()
    C._last_announced_boundary = None
    FrozenNow.frozen = FrozenNow(2026, 10, 6, *at)
    C.datetime.datetime = FrozenNow
    try:
        C._schedule_next(cfg)
    finally:
        C.datetime.datetime = _real_datetime
    return captured[0][0] / 1000.0


rows, fails = [], 0


def check(name, got, want, tol=0.02):
    global fails
    ok = abs(got - want) <= tol
    if not ok:
        fails += 1
    rows.append((name, want, got, "ok" if ok else "FAIL"))


# 16:50:00, interval 30 -> the 17:00 boundary, 600 s off, hourly: the pips.
# `d - 600` is where the sound starts relative to the boundary.
d = schedule("sound-speech", (16, 50, 0))
check("pips start before the boundary", 600 - d, PIPS_ONSET - BIAS)
check("pip 6 lands with the voice", (d - 600) + PIPS_ONSET, BIAS)

# 17:20:00 -> the 17:30 boundary, intermediate: the short chime, which has
# no instant of its own, so its last note is the instant.
d = schedule("sound-speech", (17, 20, 0))
check("chime3 starts before the boundary", 600 - d, CHIME3_DUR - BIAS)
check("chime3 ends with the voice", (d - 600) + CHIME3_DUR, BIAS)

# Sound only: no voice to meet, so the instant is the boundary itself.
d = schedule("sound", (16, 50, 0))
check("pip 6 on the boundary, sound only", (d - 600) + PIPS_ONSET, 0.0)
d = schedule("sound", (17, 20, 0))
check("chime3 starts on the boundary, sound only", 600 - d, 0.0)

# Speech only: the timer lands just past the boundary and the wait in
# _speak_time_at_boundary does the placing.
d = schedule("speech", (16, 50, 0))
check("speech-only timer fires after the boundary", 600 - d, -0.1)

# The drift tolerance is taken from the chime for *this* boundary. It used to
# measure the hourly chime at an intermediate boundary, which bought a short
# chime a tolerance it had not earned.
cfg.chime_style = "sound-speech"
check("hourly tolerance from the pips", C._chime_moment(cfg, True)[1], PIPS_ONSET)
check("intermediate tolerance from chime3", C._chime_moment(cfg, False)[1], CHIME3_DUR)

# The announced value must come from the boundary, not from the clock: this
# is what lets the voice be placed by ear rather than by fear of "16:59:59".
fmt = C._orca_time_format()
check("Orca's time format is readable", 1.0 if fmt else 0.0, 1.0)
boundary = datetime.datetime(2026, 10, 6, 17, 0, 0)
spoken = []
from orca import presentation_manager  # noqa: E402

presentation_manager.get_manager().present_message = lambda t, *a, **k: spoken.append(t)
C._speak_time(boundary)
check("the boundary is announced, not the clock",
      1.0 if spoken == [boundary.strftime(fmt)] else 0.0, 1.0)

# The boundary handed to the timer must be the boundary it is for. Worked out
# again inside _on_timer, "the next boundary strictly after now" was the
# following one whenever the timer fired past the boundary -- which speech-only
# does by design -- so that announcement was recorded against a boundary half
# an hour off and the real one was skipped as already done.
schedule("speech", (16, 50, 0))
_ms, _cfg, armed_boundary, armed_hourly = captured[0]
check("speech-only arms the timer for the right boundary",
      1.0 if armed_boundary == datetime.datetime(2026, 10, 6, 17, 0) else 0.0, 1.0)
check("...and knows it is the hourly one", 1.0 if armed_hourly else 0.0, 1.0)

schedule("sound-speech", (17, 20, 0))
_ms, _cfg, armed_boundary, armed_hourly = captured[0]
check("sound+speech arms the timer for the right boundary",
      1.0 if armed_boundary == datetime.datetime(2026, 10, 6, 17, 30) else 0.0, 1.0)
check("...and knows it is an intermediate one",
      0.0 if armed_hourly else 1.0, 1.0)

# Too late to start this boundary's chime: the boundary must move with the
# delay, or the timer announces a boundary it was not armed for.
schedule("sound-speech", (16, 59, 58))
_ms, _cfg, armed_boundary, _h = captured[0]
check("no runway left -> next boundary, boundary moved too",
      1.0 if armed_boundary == datetime.datetime(2026, 10, 6, 17, 30) else 0.0, 1.0)

# An already-announced boundary is skipped, and again the boundary moves.
C._last_announced_boundary = datetime.datetime(2026, 10, 6, 17, 0)
cfg.chime_style = "sound-speech"
captured.clear()
FrozenNow.frozen = FrozenNow(2026, 10, 6, 16, 50, 0)
C.datetime.datetime = FrozenNow
try:
    C._schedule_next(cfg)
finally:
    C.datetime.datetime = _real_datetime
_ms, _cfg, armed_boundary, _h = captured[0]
check("an announced boundary is skipped, boundary moved too",
      1.0 if armed_boundary == datetime.datetime(2026, 10, 6, 17, 30) else 0.0, 1.0)
C._last_announced_boundary = None

# A chime's worker thread reschedules when playback finishes, seconds after
# _on_timer returned. If the extension was disabled in the meantime, arming a
# timer off the config it captured would restart a clock that had been stopped.
cfg.chime_style = "sound-speech"
FrozenNow.frozen = FrozenNow(2026, 10, 6, 16, 50, 0)
C.datetime.datetime = FrozenNow
try:
    captured.clear()
    C._config = None
    C._schedule_next(cfg)
    check("a stopped clock is not rescheduled", float(len(captured)), 0.0)

    spoken.clear()
    C._speak_time_at_boundary(boundary)
    check("a stopped clock does not speak", float(len(spoken)), 0.0)

    captured.clear()
    C._config = Config()
    C._schedule_next(cfg)
    check("a config that is no longer live is not rescheduled",
          float(len(captured)), 0.0)
finally:
    C.datetime.datetime = _real_datetime
    C._config = cfg

w = max(len(r[0]) for r in rows)
for name, want, got, status in rows:
    print(f"  {status:4s} {name:<{w}}  want={want:+.3f}  got={got:+.3f}")
print(f"\n{len(rows) - fails}/{len(rows)} rows green")
sys.exit(1 if fails else 0)
