# Changelog

All notable changes to Clock for Orca.

## [2.0.0] - 2026-10-06

Clock is now a first-class **Orca 51 extension**. This is a breaking change in
how it is installed and where it keeps its settings, hence the major version;
nothing you have configured is lost.

### Changed — Orca's extension system instead of `orca-customizations.py`

- **Clock is an Orca 51 extension.** Orca discovers it in
  `~/.local/share/orca/extensions/clock/`, checks it against an approved content
  hash, and drives it through `on_ready` / `on_enabled` / `on_disabled` /
  `on_shutdown`. Version 1.x appended a loader block to
  `orca-customizations.py` and imported itself on startup; that block is removed
  by the installer if it is still there.
- **Clock appears in Orca Preferences → User Extensions**, where it can be
  enabled, disabled or removed like any other extension, and it stops and
  restarts cleanly when you do — no Orca restart needed.
- **Settings moved into Orca's own per-extension store**, under
  `/org/gnome/orca/<profile>/extensions/clock/`, in place of Clock's private
  `org.gnome.Orca.Clock` GSettings schema. Every value is imported from the old
  schema automatically on first run and the import is marked done, so it never
  runs twice — including after a deliberate reset to defaults. The old schema is
  left in place; `./uninstall.sh --purge` removes it.
  Quiet-hours days are stored as seven booleans rather than a list of weekday
  numbers, because the extension store holds only booleans, numbers, strings and
  lists of strings.
- **Settings are watched, not just read.** Anything that writes the store —
  `dconf`, another profile — reschedules the next announcement immediately.
- **Orca+Ctrl+C remains the only place Clock is configured.** Clock declares no
  preferences to Orca, so the Settings button in User Extensions is inactive on
  purpose: the generated dialog cannot host the Preview or Test buttons, and it
  stages values in memory, so the extension is never told which chime you are
  auditioning. The README explains the trade in full.
- Install approval covers the bundled sounds as well as the code, so
  `install.sh` copies the sounds before approving, and re-approves on every run
  — which is what you want after editing anything under `clock/`.

### Changed — the chime moves, not the voice

- **The sound is now what gets placed late, not the speech.** Every chime has one
  instant that marks the boundary: for the BBC pips (`clock_cuckoo7.wav`) the
  onset of the sixth, long pip, which is the convention — the pip *starts* on the
  hour, it does not end on it; for any other chime in sound-then-speech mode, its
  last note. That instant and the spoken time now land **together**, both placed
  by one constant, so they cannot drift apart. Previously the voice alone was
  held back and the pip was not, which left the pip a quarter of a second ahead
  of the announcement.
- **The voice announces the boundary it was scheduled for, not whatever the clock
  says when it gets round to speaking.** This is what freed the timing: Orca's
  default `%X` format includes seconds in most locales, so a speech call a
  millisecond early announced `16:59:59` instead of `17:00:00` — and the audio
  graph's quantum, `pw-play` start-up and NTP slewing the wall clock against the
  monotonic clock all jitter by more than a millisecond. Clock reads your
  configured format from Orca and applies it to the scheduled boundary, so the
  announced value is right however early or late it fires.
- **Sound-only mode puts the chime's instant on the boundary itself.** There is no
  voice for it to meet, so nothing competes for the moment.

### Fixed

- **Speech-only mode lost every other announcement.** The timer callback worked out
  which boundary it had woken for by taking "the next boundary strictly after
  now" — right when the timer fires early, wrong whenever it fires *after* the
  boundary, which speech-only mode does by design. It recorded the announcement
  against the *following* boundary, which was then skipped as already done. The
  boundary is now handed to the timer rather than re-derived.
- **A timer could be armed for one boundary while the code believed it was for
  another.** When there was no runway left to start a chime, or the boundary had
  already been announced, the delay was pushed on by one interval but the
  boundary was not. One function now turns a boundary into a delay, so the two
  are always derived from each other.
- **The drift tolerance measured the wrong chime.** It used the hourly chime's
  length even at an intermediate boundary, giving a short chime a tolerance it
  had not earned.
- **A finished chime could restart a stopped clock.** Playback runs in a worker
  thread that reschedules when it ends, seconds after the timer callback
  returned. If the extension was disabled in the meantime, the config it had
  captured re-armed a timer anyway. Rescheduling and speaking are now both
  refused unless the clock is still running on that config.
- **A wedged `pw-play` stopped the clock permanently.** The BBC-pips path waited on
  playback without a timeout, and nothing reschedules until playback ends. The
  wait is now bounded and the process killed if it overruns.
- **A pending announcement could speak after the extension was disabled.** The
  speech's wait was an untracked GLib source, so nothing could cancel it.
- **The intermediate chime row appeared even with its checkbox clear.** The
  window's `show_all()` undid the initial hide.
- **Preview and Test played at the saved volume, not the slider's.** Auditioning a
  level after moving the slider played the old one — the one thing Preview is
  for.
- A sound removed from the source stayed installed, and kept appearing in the
  settings dialog's chime list.

### Added

- `tests-timing.py` — 20 rows, run against the installed extension. The clock and
  the GLib timer are stubbed, so it measures where the scheduler actually puts
  each piece rather than re-deriving it, and asserts that the boundary handed to
  the timer is the boundary it is for.

## 1.x

Never tagged; `__version__` stayed at 1.0.0 throughout. Loaded itself from
`orca-customizations.py` and so works on Orca 50 and earlier.

- **2026-04-22** — Quiet hours: stay silent between two times, on the days you
  choose. Boundary timing edge cases fixed.
- **2026-03-29** — Initial release: configurable intervals, four chime styles,
  separate hourly and intermediate chime sounds, adjustable volume, 17 bundled
  sounds, and an accessible GTK3 settings dialog on Orca+Ctrl+C.
