"""Accessible GTK3 settings dialog for Clock for Orca.

Follows the same AT-SPI event suspension pattern as Polyglot's config_ui.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from typing import Callable

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gtk, Gdk, GLib

from .config import Config

_log = logging.getLogger("orca-clock")

_resume_timer_id: int | None = None

_EVENTS_TO_SUSPEND = [
    "object:state-changed:focused",
    "object:state-changed:showing",
    "object:children-changed:",
    "object:property-change:accessible-name",
]

_INTERVALS = [
    (0, "Off"),
    (15, "Every 15 minutes"),
    (30, "Every 30 minutes"),
    (60, "Every hour"),
]

_STYLES = [
    ("off", "Off"),
    ("speech", "Speech"),
    ("sound", "Sound"),
    ("sound-speech", "Sound then speech"),
]


def _suspend_events():
    global _resume_timer_id
    # Cancel any pending resume so we don't resume prematurely
    if _resume_timer_id is not None:
        GLib.source_remove(_resume_timer_id)
        _resume_timer_id = None
    try:
        from orca import event_manager
        manager = event_manager.get_manager()
        for event in _EVENTS_TO_SUSPEND:
            manager.deregister_listener(event)
    except Exception:
        pass


def _schedule_resume():
    """Schedule a deferred resume of AT-SPI events."""
    global _resume_timer_id
    if _resume_timer_id is not None:
        GLib.source_remove(_resume_timer_id)
    _resume_timer_id = GLib.timeout_add(500, _resume_events)


def _resume_events():
    global _resume_timer_id
    _resume_timer_id = None
    try:
        from orca import event_manager
        manager = event_manager.get_manager()
        for event in _EVENTS_TO_SUSPEND:
            manager.register_listener(event)
    except Exception:
        pass
    return False


def _friendly_sound_name(filename: str) -> str:
    """Convert 'clock_chime1.wav' to 'Clock chime 1'."""
    name = os.path.splitext(filename)[0]
    name = name.replace("_", " ")
    # Insert space before trailing digits if not already spaced
    result = []
    for i, ch in enumerate(name):
        if ch.isdigit() and i > 0 and not name[i - 1].isdigit() and name[i - 1] != " ":
            result.append(" ")
        result.append(ch)
    return "".join(result).strip().capitalize()


class ClockSettingsWindow(Gtk.Window):
    def __init__(self, config: Config, on_save: Callable[[Config], None] | None = None):
        super().__init__(title="Clock Settings")
        self._config = config
        self._on_save = on_save
        self._preview_proc: subprocess.Popen | None = None
        self._preview_lock = threading.Lock()

        self.set_default_size(400, -1)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(False)

        atk_obj = self.get_accessible()
        if atk_obj:
            atk_obj.set_name("Clock Settings")

        _suspend_events()
        self._build_ui()
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

    def _build_ui(self):
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(18)
        vbox.set_margin_bottom(18)
        vbox.set_margin_start(18)
        vbox.set_margin_end(18)

        # --- Interval ---
        interval_label = Gtk.Label(label="Announcement interval:", xalign=0)
        self._interval_combo = Gtk.ComboBoxText()
        self._interval_combo.get_accessible().set_name("Announcement interval")
        active_idx = 0
        for i, (val, label) in enumerate(_INTERVALS):
            self._interval_combo.append(str(val), label)
            if val == self._config.interval:
                active_idx = i
        self._interval_combo.set_active(active_idx)
        vbox.pack_start(interval_label, False, False, 0)
        vbox.pack_start(self._interval_combo, False, False, 0)

        # --- Chime style ---
        style_label = Gtk.Label(label="Chime style:", xalign=0)
        self._style_combo = Gtk.ComboBoxText()
        self._style_combo.get_accessible().set_name("Chime style")
        active_idx = 0
        for i, (val, label) in enumerate(_STYLES):
            self._style_combo.append(val, label)
            if val == self._config.chime_style:
                active_idx = i
        self._style_combo.set_active(active_idx)
        self._style_combo.connect("changed", self._on_style_changed)
        vbox.pack_start(style_label, False, False, 0)
        vbox.pack_start(self._style_combo, False, False, 0)

        # --- Chime volume ---
        vol_label = Gtk.Label(label="Chime volume:", xalign=0)
        self._vol_label = vol_label
        self._vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.0, 1.0, 0.05)
        self._vol_scale.set_value(self._config.chime_volume)
        self._vol_scale.set_draw_value(False)
        self._vol_scale.get_accessible().set_name("Chime volume")
        vbox.pack_start(vol_label, False, False, 0)
        vbox.pack_start(self._vol_scale, False, False, 0)

        # --- Hourly chime sound ---
        sound_label = Gtk.Label(label="Hourly chime sound:", xalign=0)
        self._sound_label = sound_label

        sounds = self._config.list_sounds()

        sound_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._sound_combo = Gtk.ComboBoxText()
        self._sound_combo.get_accessible().set_name("Hourly chime sound")
        active_idx = 0
        for i, fname in enumerate(sounds):
            self._sound_combo.append(fname, _friendly_sound_name(fname))
            if fname == self._config.chime_sound:
                active_idx = i
        if sounds:
            self._sound_combo.set_active(active_idx)

        self._preview_btn = Gtk.Button(label="Preview")
        self._preview_btn.get_accessible().set_name("Preview hourly chime sound")
        self._preview_btn.connect("clicked", self._on_preview)

        sound_box.pack_start(self._sound_combo, True, True, 0)
        sound_box.pack_start(self._preview_btn, False, False, 0)

        vbox.pack_start(sound_label, False, False, 0)
        vbox.pack_start(sound_box, False, False, 0)

        # --- Intermediate chime toggle + sound ---
        self._int_check = Gtk.CheckButton(label="Use different sound for intermediate chimes")
        self._int_check.get_accessible().set_name("Use different sound for intermediate chimes")
        self._int_check.set_active(self._config.intermediate_enabled)
        self._int_check.connect("toggled", self._on_int_toggled)
        vbox.pack_start(self._int_check, False, False, 0)

        self._int_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._int_combo = Gtk.ComboBoxText()
        self._int_combo.get_accessible().set_name("Intermediate chime sound")
        active_idx = 0
        for i, fname in enumerate(sounds):
            self._int_combo.append(fname, _friendly_sound_name(fname))
            if fname == self._config.intermediate_sound:
                active_idx = i
        if sounds:
            self._int_combo.set_active(active_idx)

        self._int_preview_btn = Gtk.Button(label="Preview")
        self._int_preview_btn.get_accessible().set_name("Preview intermediate chime sound")
        self._int_preview_btn.connect("clicked", self._on_preview_intermediate)

        self._int_box.pack_start(self._int_combo, True, True, 0)
        self._int_box.pack_start(self._int_preview_btn, False, False, 0)

        vbox.pack_start(self._int_box, False, False, 0)

        # --- Buttons ---
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(12)

        # Temporary test button — plays chime then speaks time
        test_btn = Gtk.Button(label="Test")
        test_btn.get_accessible().set_name("Test sound and speech")
        test_btn.connect("clicked", self._on_test)

        save_btn = Gtk.Button(label="Save")
        save_btn.get_accessible().set_name("Save settings")
        save_btn.connect("clicked", self._on_save_clicked)

        close_btn = Gtk.Button(label="Close")
        close_btn.get_accessible().set_name("Close without saving")
        close_btn.connect("clicked", self._on_close_clicked)

        btn_box.pack_start(test_btn, False, False, 0)
        btn_box.pack_start(close_btn, False, False, 0)
        btn_box.pack_start(save_btn, False, False, 0)

        vbox.pack_start(btn_box, False, False, 0)

        self.add(vbox)

        # Set initial sensitivity of sound widgets
        self._update_sound_sensitivity()

    def _update_sound_sensitivity(self):
        style = self._style_combo.get_active_id() or "off"
        has_sound = style in ("sound", "sound-speech")
        self._vol_label.set_sensitive(has_sound)
        self._vol_scale.set_sensitive(has_sound)
        self._sound_combo.set_sensitive(has_sound)
        self._preview_btn.set_sensitive(has_sound)
        self._sound_label.set_sensitive(has_sound)
        self._int_check.set_sensitive(has_sound)
        int_visible = has_sound and self._int_check.get_active()
        self._int_box.set_visible(int_visible)

    def _on_style_changed(self, combo):
        self._update_sound_sensitivity()

    def _on_int_toggled(self, check):
        self._update_sound_sensitivity()

    def _on_test(self, button):
        """Play the selected chime, then speak the time — for verification."""
        from .clock import _BBC_PIPS_FILE, _BBC_PIPS_FINAL_ONSET
        self._stop_preview()
        fname = self._sound_combo.get_active_id()
        path = os.path.join(self._config.sounds_dir, fname) if fname else None
        is_bbc_pips = fname == _BBC_PIPS_FILE

        def _worker():
            if path and os.path.isfile(path):
                try:
                    proc = subprocess.Popen(["pw-play", "--volume", str(self._config.chime_volume), path])
                    with self._preview_lock:
                        self._preview_proc = proc
                    if is_bbc_pips:
                        # Speak with the final long pip, not after
                        import time as _time
                        _time.sleep(_BBC_PIPS_FINAL_ONSET)
                        GLib.idle_add(self._speak_test_time)
                        proc.wait()
                    else:
                        proc.wait()
                        GLib.idle_add(self._speak_test_time)
                except (FileNotFoundError, OSError):
                    GLib.idle_add(self._speak_test_time)
                with self._preview_lock:
                    self._preview_proc = None
            else:
                GLib.idle_add(self._speak_test_time)

        threading.Thread(target=_worker, daemon=True).start()

    def _speak_test_time(self):
        try:
            from .clock import _speak_time
            _speak_time()
        except Exception as e:
            _log.error("Clock: test speech failed: %s", e)
        return False

    def _on_preview(self, button):
        self._play_preview(self._sound_combo.get_active_id())

    def _on_preview_intermediate(self, button):
        self._play_preview(self._int_combo.get_active_id())

    def _play_preview(self, fname: str | None):
        if not fname:
            return
        path = os.path.join(self._config.sounds_dir, fname)
        if not os.path.isfile(path):
            return
        self._stop_preview()

        def _play():
            try:
                proc = subprocess.Popen(["pw-play", "--volume", str(self._config.chime_volume), path])
                with self._preview_lock:
                    self._preview_proc = proc
                proc.wait()
            except (FileNotFoundError, OSError):
                pass
            with self._preview_lock:
                self._preview_proc = None

        threading.Thread(target=_play, daemon=True).start()

    def _stop_preview(self):
        with self._preview_lock:
            proc = self._preview_proc
        if proc is not None:
            try:
                proc.terminate()
            except OSError:
                pass

    def _on_save_clicked(self, button):
        self._save_config()
        _suspend_events()
        self._stop_preview()
        self.destroy()
        _schedule_resume()

    def _on_close_clicked(self, button):
        _suspend_events()
        self._stop_preview()
        self.destroy()
        _schedule_resume()

    def _on_delete(self, window, event):
        _suspend_events()
        self._stop_preview()
        _schedule_resume()
        return False

    def _on_key_press(self, window, event):
        if event.keyval == Gdk.KEY_Escape:
            _suspend_events()
            self._stop_preview()
            self.destroy()
            _schedule_resume()
            return True
        return False

    def _save_config(self):
        interval_id = self._interval_combo.get_active_id()
        self._config.interval = int(interval_id) if interval_id else 0

        style_id = self._style_combo.get_active_id()
        self._config.chime_style = style_id or "off"

        self._config.chime_volume = round(self._vol_scale.get_value(), 2)

        sound_id = self._sound_combo.get_active_id()
        if sound_id:
            self._config.chime_sound = sound_id

        self._config.intermediate_enabled = self._int_check.get_active()
        int_id = self._int_combo.get_active_id()
        if int_id:
            self._config.intermediate_sound = int_id

        self._config.save()

        if self._on_save:
            self._on_save(self._config)

    def focus_first(self):
        self._interval_combo.grab_focus()
        _schedule_resume()


def show_settings_dialog(config: Config, on_save: Callable[[Config], None] | None = None):
    """Show the settings window. Must be called from the GTK main thread."""
    window = ClockSettingsWindow(config, on_save)
    window.show_all()
    window.focus_first()
    return window
