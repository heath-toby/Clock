# Clock for Orca

Periodic time announcements with chime sounds for the [Orca screen reader](https://wiki.gnome.org/Projects/Orca) on Linux. Inspired by the [NVDA Clock add-on](https://addons.nvda-project.org/addons/clock.en.html).

## Features

- **Configurable intervals**: Off, every 15 minutes, every 30 minutes, or every hour
- **Chime styles**:
  - **Speech** — Orca announces the time at each interval
  - **Sound** — a chime plays at each interval
  - **Sound then speech** — the chime is precision-timed to finish exactly on the boundary, then Orca announces the time
- **BBC pips support**: When using `clock_cuckoo7.wav` (the Greenwich Time Signal), the five short pips play leading up to the hour and Orca announces the time simultaneously with the sixth long pip — just like radio
- **Separate hourly and intermediate chimes**: Optionally use a different sound for quarter/half-hour intervals
- **Adjustable chime volume**
- **17 bundled chime sounds**: Bells, clock chimes, cuckoo clocks, time signals, and more
- **Respects Orca's time format**: Uses Orca's own time presentation, so your configured format is honoured
- **Accessible settings dialog**: Press **Orca+Ctrl+C** to configure everything

## Requirements

- [Orca](https://wiki.gnome.org/Projects/Orca) screen reader
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

The installer copies files to `~/.local/share/orca/clock/`, installs the GSettings schema, and adds a loader block to `orca-customizations.py`.

## Usage

Press **Orca+Ctrl+C** to open the settings dialog, where you can configure:

- **Announcement interval** — how often to announce
- **Chime style** — speech, sound, or both
- **Chime volume** — adjust the sound level
- **Hourly chime sound** — select from 17 bundled sounds
- **Intermediate chime sound** — optionally use a different sound for non-hourly intervals

## Uninstallation

```bash
cd Clock
./uninstall.sh
orca --replace &
```

## How it works

Clock runs as an Orca extension loaded via `orca-customizations.py`. It uses `GLib.timeout_add()` to schedule single-shot timers that fire at each interval boundary. Sound playback runs in a background thread via `pw-play` so it never blocks Orca. Time announcements are delegated to Orca's own `SystemInformationPresenter.present_time()`, ensuring consistency with your configured time format.

For precision timing (sound-then-speech mode), the chime duration is measured via `soxi -D` and the sound is started early enough that it finishes exactly on the interval boundary.

## License

MIT
