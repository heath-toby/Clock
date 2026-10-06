# Clock for Orca

Periodic time announcements with chime sounds for the [Orca screen reader](https://wiki.gnome.org/Projects/Orca) on Linux. Inspired by the [NVDA Clock add-on](https://addons.nvda-project.org/addons/clock.en.html).

## Features

- **Configurable intervals**: Off, every 15 minutes, every 30 minutes, or every hour
- **Chime styles**:
  - **Speech** — Orca announces the time at each interval
  - **Sound** — a chime plays at each interval
  - **Sound then speech** — the chime is started early enough that the instant marking the boundary (the final note, or the sixth pip) lands with the spoken time, not before it
- **BBC pips support**: When using `clock_cuckoo7.wav` (the Greenwich Time Signal), the five short pips play leading up to the hour and Orca announces the time simultaneously with the sixth long pip — just like radio
- **Separate hourly and intermediate chimes**: Optionally use a different sound for quarter/half-hour intervals
- **Adjustable chime volume**
- **17 bundled chime sounds**: Bells, clock chimes, cuckoo clocks, time signals, and more
- **Respects Orca's time format**: Uses Orca's own time presentation, so your configured format is honoured
- **Quiet hours**: Stay silent between two times, on the days you choose
- **Accessible settings dialog**: Press **Orca+Ctrl+C** to configure everything, with
  sound preview and a test button

## Requirements

- [Orca](https://wiki.gnome.org/Projects/Orca) **51 or later** — Clock is a user
  extension, and the extension system did not exist before 51. (Version 1.x of Clock
  loaded itself from `orca-customizations.py` and works on Orca 50 and earlier.)
- [PipeWire](https://pipewire.org/) (for `pw-play`)
- [SoX](https://sox.sourceforge.net/) (for `soxi` duration detection)
- Python 3.10+
- GLib/GSettings

## Installation

```bash
git clone https://github.com/heath-toby/Clock.git
cd Clock
./install.sh
orca --replace &
```

The installer copies the package and its sounds to
`~/.local/share/orca/extensions/clock/` and approves it with
`orca --approve-extension clock`. It no longer touches
`orca-customizations.py`; if a loader block from version 1.x is still there, it
removes it.

Orca approves extensions **by content hash** and refuses to load one whose files
have changed since approval, so after editing anything under `clock/` re-run
`./install.sh` — it re-approves as part of installing.

### Upgrading from version 1.x

Nothing to do. Clock used to keep its settings in a private GSettings schema,
`org.gnome.Orca.Clock`; it now uses Orca's own per-extension settings store. The
first time the extension runs it imports every value from the old schema — interval,
chime style, both chime sounds, volume, and the whole quiet-hours configuration —
and marks the import done so it never runs twice. The old schema and its values are
left in place; `./uninstall.sh --purge` removes them.

## Usage

Press **Orca+Ctrl+C** to open Clock's own settings dialog, where you can configure:

- **Announcement interval** — how often to announce
- **Chime style** — speech, sound, or both
- **Chime volume** — adjust the sound level
- **Hourly chime sound** — select from 17 bundled sounds, with a **Preview** button
- **Intermediate chime sound** — optionally use a different sound for non-hourly intervals
- **Quiet hours** — a start and end time, and which days they apply on

**Orca+Ctrl+C is the only place Clock is configured.** Orca Preferences → User
Extensions → Clock is where you enable, disable, remove, or read information about the
extension, but its **Settings button is deliberately inactive** — see below.

### Why Clock doesn't use Orca's generated preferences dialog

Orca 51 lets an extension declare its settings and get a dialog generated for free. Clock
declares none, on purpose.

That dialog can only render the declarative preference kinds — booleans, strings, enums,
numbers, lists. There is no way to give it Clock's **Preview** buttons or the **Test**
button, and no hook for an extension to supply its own dialog in place of the generated
one. Worse, the generated dialog *stages* every value in memory and writes them only when
the whole Orca preferences window is saved, so the extension is never told which chime you
are auditioning — auto-previewing on selection is impossible too, for the same reason.

Declaring preferences would therefore have produced a second, poorer settings dialog
sitting alongside the real one, showing the same settings without the ability to hear any
of them. Declaring none leaves the Settings button greyed out and Orca+Ctrl+C as the
single, complete way in.

This is the right trade for any add-on whose settings need more than a form — modal
sub-dialogs, previews, live feedback, anything conditional. Package it as an extension for
the discovery, approval, and lifecycle benefits; keep its own dialog on a keybinding.

## Uninstallation

```bash
cd Clock
./uninstall.sh
orca --replace &
```

## How it works

Clock is an Orca 51 user extension: Orca discovers it in the extensions directory,
checks it against an approved content hash, and drives it through `on_ready` /
`on_enabled` / `on_disabled` / `on_shutdown`. It uses `GLib.timeout_add()` to schedule single-shot timers that fire at each interval boundary. Sound playback runs in a background thread via `pw-play` so it never blocks Orca.

### Where the announcement lands

Every chime has one instant that marks the boundary. For the BBC pips it is the onset of the
sixth, long pip -- the pip starts on the hour, it does not end on it. For any other chime in
sound-then-speech mode it is the last note, so the chime finishes as the voice begins.
Durations are measured once with `soxi -D` and cached.

The chime is started early enough for that instant to land where the voice does, and both
read the same constant, so they cannot drift apart. In sound-only mode there is no voice to
meet, so the instant lands on the boundary itself.

The voice announces **the boundary it was scheduled for**, not whatever the clock says when it
gets round to speaking. That matters more than it sounds: Orca's default `%X` format includes
seconds in most locales, so a speech call a millisecond early would announce 16:59:59 instead
of 17:00:00 -- and the audio graph's quantum, `pw-play` start-up and NTP slewing the wall
clock against the monotonic clock all jitter by more than a millisecond. Reading your
configured format from Orca and applying it to the scheduled boundary makes the announced
value right whenever it fires, which is what frees the timing to be set by ear.

`tests-timing.py` at the repository root checks all of this. It stubs the clock and the
GLib timer, so it measures where the scheduler actually puts each piece rather than
re-deriving it, and it asserts that the boundary handed to the timer is the boundary it
is for. It runs against the *installed* extension, so install first:

```bash
./install.sh && python3 tests-timing.py
```

Settings live in Orca's own per-extension store rather than in a private schema, so
`dconf` keeps them under `/org/gnome/orca/<profile>/extensions/clock/`. Clock watches that
key and reschedules the next announcement whenever anything writes it, so a change takes
effect immediately however it was made.

Quiet-hours days are stored as seven separate booleans rather than a list of weekday
numbers, because the extension settings store accepts only booleans, numbers, strings and
lists of strings — a list of integers cannot go in it.

## License

MIT
