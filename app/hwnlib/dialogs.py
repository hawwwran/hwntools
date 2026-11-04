import os
import sys
import time

from gi.repository import Gtk, Gdk, Gio, Pango, GLib, Vte

from .config import parse_config
from .deps import check_dependencies
from .identity import OUTPUT_APP_ID, program_name
from .state import load_state, update_state


# Script output windows are independent of the main window: they do not block
# it and keep the GTK main loop alive after it closes, so a running script is
# not killed when the user closes hwntools.
_active_output_windows = set()
_main_window_alive = [True]


def _on_main_window_closed():
    _main_window_alive[0] = False
    if not _active_output_windows:
        Gtk.main_quit()


def _register_output_window(win):
    _active_output_windows.add(win)


def _unregister_output_window(win):
    _active_output_windows.discard(win)
    if not _main_window_alive[0] and not _active_output_windows:
        Gtk.main_quit()


def _status_hint_row(status=None, end_widget=None):
    """HBox: optional status label on the left, copy/paste hint on the right.
    end_widget, if given, is packed at the far right (after the hint).
    Overrides the status label's start/bottom margins so the row aligns as one line."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.set_margin_start(8)
    box.set_margin_end(8)
    box.set_margin_bottom(4)
    if status is not None:
        status.set_margin_start(0)
        status.set_margin_bottom(0)
        box.pack_start(status, False, False, 0)
    if end_widget is not None:
        box.pack_end(end_widget, False, False, 0)
    hint = Gtk.Label(label="Select text \u2022 Ctrl+Shift+C copy \u2022 Ctrl+Shift+V paste")
    hint.get_style_context().add_class("dim-label")
    box.pack_end(hint, False, False, 0)
    return box


def _handle_terminal_copy_paste(terminal, event, allow_plain_paste=False):
    """Ctrl+Shift+C copies, Ctrl+Shift+V pastes. Returns True if the event was handled.
    allow_plain_paste: also accept Ctrl+V without Shift (used for the git auth terminal
    so users can paste access tokens quickly)."""
    if not (event.state & Gdk.ModifierType.CONTROL_MASK):
        return False
    shift = event.state & Gdk.ModifierType.SHIFT_MASK
    if shift and event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
        terminal.copy_clipboard_format(Vte.Format.TEXT)
        return True
    if (shift or allow_plain_paste) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
        terminal.paste_clipboard()
        return True
    return False


# Rows from the bottom within which a downward scroll snaps to the bottom and
# re-engages output following. Must stay below the mouse wheel step (3 rows),
# otherwise one wheel notch up from the bottom would be snapped straight back.
_AUTOSCROLL_SNAP_ROWS = 2


def _setup_smart_autoscroll(terminal, scrolled):
    """Follow new output only while the view is scrolled to the bottom.

    With scroll_on_output off, VTE keeps the view pinned to the bottom when it
    is exactly there and leaves it alone once the user scrolls up. The handler
    adds a snap zone so that scrolling back down near the bottom lands exactly
    on it, re-engaging the follow behavior without pixel-perfect aim. Only
    downward movement snaps, so scrolling up from the bottom is never yanked
    back.

    Must be called after the terminal is inside the scrolled window: adding
    the terminal replaces its adjustment with the scrolled window's own.
    """
    terminal.set_scroll_on_output(False)
    adj = scrolled.get_vadjustment()
    last_value = [adj.get_value()]

    def on_value_changed(adjustment):
        value = adjustment.get_value()
        moving_down = value > last_value[0]
        last_value[0] = value
        bottom = adjustment.get_upper() - adjustment.get_page_size()
        if moving_down and 0 < bottom - value <= _AUTOSCROLL_SNAP_ROWS:
            adjustment.set_value(bottom)

    adj.connect("value-changed", on_value_changed)


def _make_git_terminal(parent, title, command, on_success=None, on_failure=None,
                       cleanup_dir=None):
    """Open a terminal window that runs a git command interactively.
    on_success: called and window auto-closes on exit code 0.
    on_failure: called on non-zero exit or dismiss; window stays open for retry.
    cleanup_dir: directory to remove before retry (partial clone leftovers)."""
    win = Gtk.Window(title=title)
    win.set_transient_for(parent)
    win.set_modal(True)
    win.set_default_size(600, 350)
    failed = [False]  # track whether on_failure was already called

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.add(vbox)

    terminal = Vte.Terminal()
    terminal.set_font(Pango.FontDescription("monospace 11"))
    terminal.set_color_background(Gdk.RGBA(0.118, 0.118, 0.118, 1))
    terminal.set_color_foreground(Gdk.RGBA(0.831, 0.831, 0.831, 1))
    terminal.set_scrollback_lines(10000)

    status = Gtk.Label(label="Waiting for authentication\u2026")
    status.set_xalign(0)
    status.get_style_context().add_class("dim-label")
    status.set_margin_start(8)
    status.set_margin_bottom(4)

    btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    btn_box.set_margin_start(8)
    btn_box.set_margin_end(8)
    btn_box.set_margin_bottom(6)
    retry_btn = Gtk.Button(label="Retry")
    close_btn = Gtk.Button(label="Close")
    btn_box.pack_end(close_btn, False, False, 0)
    btn_box.pack_end(retry_btn, False, False, 0)
    retry_btn.set_no_show_all(True)
    close_btn.set_no_show_all(True)

    def _cleanup():
        if cleanup_dir and os.path.isdir(cleanup_dir):
            import shutil
            shutil.rmtree(cleanup_dir, ignore_errors=True)

    def spawn():
        failed[0] = False
        retry_btn.hide()
        close_btn.hide()
        status.set_text("Waiting for authentication\u2026")
        terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            ["/bin/bash", "-c", command + "; exit $?"],
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None,
            -1, None,
            None,
        )

    def _fire_failure():
        if not failed[0] and on_failure:
            failed[0] = True
            on_failure()

    def on_key(widget, event):
        if _handle_terminal_copy_paste(terminal, event, allow_plain_paste=True):
            return True
        if event.keyval == Gdk.KEY_Escape:
            _fire_failure()
            win.destroy()
            return True
        return False

    def on_child_exited(terminal, exit_status):
        code = exit_status >> 8
        if code == 0:
            if on_success:
                on_success()
            win.destroy()
        else:
            status.set_markup(
                '<span color="#cc3333">Authentication failed</span>')
            retry_btn.show()
            close_btn.show()
            _fire_failure()

    def on_retry(button):
        _cleanup()
        terminal.reset(True, True)
        spawn()

    def on_close(button):
        _fire_failure()
        win.destroy()

    terminal.connect("key-press-event", on_key)
    terminal.connect("child-exited", on_child_exited)
    retry_btn.connect("clicked", on_retry)
    close_btn.connect("clicked", on_close)

    scrolled = Gtk.ScrolledWindow()
    scrolled.set_margin_top(6)
    scrolled.set_margin_bottom(6)
    scrolled.set_margin_start(6)
    scrolled.set_margin_end(6)
    scrolled.add(terminal)
    _setup_smart_autoscroll(terminal, scrolled)
    vbox.pack_start(scrolled, True, True, 0)
    vbox.pack_start(_status_hint_row(status), False, False, 0)
    vbox.pack_start(btn_box, False, False, 0)

    win.show_all()
    spawn()
    return win


class InstallDialog(Gtk.Window):
    """Terminal window for installing a dependency."""
    def __init__(self, parent, dep_name):
        super().__init__(title=f"Install {dep_name}")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(500, 350)
        self.dep_name = dep_name

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription("monospace 11"))
        self.terminal.set_color_background(Gdk.RGBA(0.118, 0.118, 0.118, 1))
        self.terminal.set_color_foreground(Gdk.RGBA(0.831, 0.831, 0.831, 1))
        self.terminal.set_scrollback_lines(10000)
        self.terminal.connect("key-press-event", self.on_key)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_margin_top(6)
        scrolled.set_margin_bottom(6)
        scrolled.set_margin_start(6)
        scrolled.set_margin_end(6)
        scrolled.add(self.terminal)
        _setup_smart_autoscroll(self.terminal, scrolled)
        vbox.pack_start(scrolled, True, True, 0)
        vbox.pack_start(_status_hint_row(), False, False, 0)

        self.show_all()

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            ["/bin/bash", "-c", f"sudo apt install {dep_name}; exec bash"],
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None,
            -1, None,
            None,
        )

    def on_key(self, widget, event):
        if _handle_terminal_copy_paste(self.terminal, event):
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False


class DepDialog(Gtk.Window):
    """Shows missing dependencies with install buttons. Rechecks on install window close."""
    def __init__(self, parent, script_path, failures):
        super().__init__(title="Missing Dependencies")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_default_size(400, -1)
        self.parent_win = parent
        self.script_path = script_path
        self.deps = parse_config(script_path).get("deps", [])

        self.connect("key-press-event", self.on_key)

        self.vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.vbox.set_margin_top(12)
        self.vbox.set_margin_bottom(12)
        self.vbox.set_margin_start(16)
        self.vbox.set_margin_end(16)
        self.add(self.vbox)

        self.build_content(failures)

    def build_content(self, failures):
        for child in self.vbox.get_children():
            self.vbox.remove(child)

        header = Gtk.Label()
        header.set_markup(f"<b>Cannot run {os.path.basename(self.script_path)}</b>")
        header.set_xalign(0)
        self.vbox.pack_start(header, False, False, 0)

        for name, problem in failures:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label()
            lbl.set_markup(f"<b>{name}</b> — {problem}")
            lbl.set_xalign(0)
            lbl.set_line_wrap(True)
            row.pack_start(lbl, True, True, 0)

            install_btn = Gtk.Button(label="Install")
            install_btn.connect("clicked", self.on_install, name)
            row.pack_end(install_btn, False, False, 0)

            self.vbox.pack_start(row, False, False, 0)

        self.vbox.show_all()

    def on_install(self, button, dep_name):
        install_win = InstallDialog(self, dep_name)
        install_win.connect("destroy", self.on_install_closed)

    def on_install_closed(self, widget):
        failures = check_dependencies(self.deps)
        if failures:
            self.build_content(failures)
        else:
            self.destroy()

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False


class OutputDialog(Gtk.Window):
    # Responses of the "script is still running" save-log question.
    _RESP_SAVE_NOW = 1
    _RESP_SAVE_ON_EXIT = 2

    def __init__(self, parent, title, script_path):
        super().__init__(title=title)
        # Own taskbar entry and icon, apart from the launcher: X11 reads this
        # WM_CLASS, Wayland the program name do_map sets.
        self.set_wmclass(OUTPUT_APP_ID, OUTPUT_APP_ID)
        self.set_icon_name(OUTPUT_APP_ID)
        self.parent_win = parent
        self.script_path = script_path
        self._exited = False
        self._exit_msg = ""
        # When set, output is being logged live to this path. A periodic flush
        # rewrites the file from the terminal buffer whenever it changed.
        self._pending_log_path = None
        self._log_flush_id = None
        self._log_dirty = False
        _register_output_window(self)
        self.connect("destroy", self.on_destroy)

        state = load_state()
        w = min(state.get("output_width", 550), 1024)
        h = min(state.get("output_height", 420), 800)
        self.set_default_size(w, h)
        if "output_x" in state and "output_y" in state:
            self.move(state["output_x"], state["output_y"])

        self._save_pending_id = None
        self._pending_geo = None
        self.connect("configure-event", self.on_configure)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        header = Gtk.Label(label=f"  {title}")
        header.set_xalign(0)
        header.get_style_context().add_class("terminal-header")
        header.set_margin_top(4)
        header.set_margin_start(6)
        vbox.pack_start(header, False, False, 2)

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription("monospace 11"))
        self.terminal.set_color_background(Gdk.RGBA(0.118, 0.118, 0.118, 1))
        self.terminal.set_color_foreground(Gdk.RGBA(0.831, 0.831, 0.831, 1))
        # Unlimited scrollback: "Save log" promises the full history.
        self.terminal.set_scrollback_lines(-1)
        self.terminal.connect("key-press-event", self.on_key)
        self.terminal.connect("child-exited", self.on_child_exited)
        self.terminal.connect("contents-changed", self._on_contents_changed)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_margin_top(2)
        scrolled.set_margin_bottom(6)
        scrolled.set_margin_start(6)
        scrolled.set_margin_end(6)
        scrolled.add(self.terminal)
        _setup_smart_autoscroll(self.terminal, scrolled)
        vbox.pack_start(scrolled, True, True, 0)

        self.status = Gtk.Label(label="Running...")
        self.status.set_xalign(0)
        self.status.get_style_context().add_class("terminal-header")
        self.save_btn = Gtk.Button(label="Save log")
        self.save_btn.connect("clicked", self.on_save_log)
        vbox.pack_start(_status_hint_row(self.status, end_widget=self.save_btn),
                        False, False, 0)

        self.show_all()

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.path.dirname(script_path),
            [script_path],
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None,
            -1, None,
            None,
        )

    def do_map(self):
        with program_name(OUTPUT_APP_ID):
            Gtk.Window.do_map(self)

    def on_child_exited(self, terminal, status):
        self._exited = True
        code = status >> 8
        self._exit_msg = "Done." if code == 0 else f"Exited with code {code}."
        extra = None
        if self._pending_log_path:
            path = self._disarm_continuous_log()
            if os.path.exists(path):
                extra = (f"Log saved to {os.path.basename(path)}."
                         if self._write_log_file(path)
                         else "Saving log failed.")
            else:
                extra = "Log file was removed; not saved."
        self._set_exit_status(extra)

    def _set_logging_mode(self, logging):
        """While a continuous log is armed the save button reads "Stop logging";
        otherwise "Save log"."""
        self.save_btn.set_label("Stop logging" if logging else "Save log")

    def _arm_continuous_log(self, path):
        """Start logging live to path. Writes the current buffer immediately,
        then rewrites the file on a timer whenever the terminal changed, so the
        file can be opened and read at any time while the script runs."""
        if not self._write_log_file(path):
            return
        self._pending_log_path = path
        self._log_dirty = False
        self._set_logging_mode(True)
        self.status.set_text(
            f"Logging live to {os.path.basename(path)}. "
            f"Deleting the file stops logging.")
        if self._log_flush_id is None:
            self._log_flush_id = GLib.timeout_add(1000, self._continuous_flush)

    def _on_contents_changed(self, _terminal):
        self._log_dirty = True

    def _continuous_flush(self):
        """Timer callback: rewrite the live log if the terminal changed. Stops
        logging if the target file was deleted or a write fails. Returns True to
        keep the timer, False to remove it."""
        if not self._pending_log_path:
            self._log_flush_id = None
            return False
        if not os.path.exists(self._pending_log_path):
            self._disarm_continuous_log()
            self.status.set_text("Log file was removed. Logging stopped.")
            return False
        if self._log_dirty:
            self._log_dirty = False
            if not self._write_log_file(self._pending_log_path, show_errors=False):
                self._disarm_continuous_log()
                self.status.set_text("Writing the log failed. Logging stopped.")
                return False
        return True

    def _disarm_continuous_log(self):
        """Cancel live logging and revert the button. Returns the path that was
        being logged (does not write it)."""
        path = self._pending_log_path
        self._pending_log_path = None
        if self._log_flush_id is not None:
            GLib.source_remove(self._log_flush_id)
            self._log_flush_id = None
        self._set_logging_mode(False)
        return path

    def on_destroy(self, _widget):
        # Window closed while logging live: the child dies with the pty, so
        # flush the final buffer (if the file still exists) before tearing down.
        if self._pending_log_path and not self._exited:
            path = self._disarm_continuous_log()
            if os.path.exists(path):
                self._write_log_file(path, show_errors=False)
        else:
            self._disarm_continuous_log()
        _unregister_output_window(self)

    def _set_exit_status(self, extra=None):
        parts = [self._exit_msg]
        if extra:
            parts.append(extra)
        parts.append("Press Esc to close.")
        self.status.set_text(" ".join(parts))

    def on_save_log(self, button):
        # Logging live: this click is "Stop logging" — flush the final buffer
        # and disarm.
        if self._pending_log_path:
            path = self._disarm_continuous_log()
            if os.path.exists(path) and self._write_log_file(path):
                self.status.set_text(
                    f"Logging stopped. Log saved to {os.path.basename(path)}.")
            else:
                self.status.set_text("Logging stopped.")
            return
        save_on_exit = False
        if not self._exited:
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.QUESTION,
                text="Script is still running")
            dialog.format_secondary_text(
                "Save the current contents once, or log continuously to a file "
                "you can open and read while the script runs?")
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Save current state", self._RESP_SAVE_NOW)
            dialog.add_button("Log continuously", self._RESP_SAVE_ON_EXIT)
            response = dialog.run()
            dialog.destroy()
            if response == self._RESP_SAVE_ON_EXIT:
                save_on_exit = True
            elif response != self._RESP_SAVE_NOW:
                return
        path = self._pick_log_path()
        if not path:
            return
        if save_on_exit:
            self._arm_continuous_log(path)
        elif self._write_log_file(path):
            saved = f"Log saved to {os.path.basename(path)}."
            if self._exited:
                self._set_exit_status(saved)
            else:
                self.status.set_text(saved)

    def _pick_log_path(self):
        """Save-file picker, opened at the last used log folder if it still
        exists. Remembers the chosen folder in state under log_dir."""
        dialog = Gtk.FileChooserDialog(
            title="Save terminal log", parent=self,
            action=Gtk.FileChooserAction.SAVE)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save", Gtk.ResponseType.ACCEPT)
        dialog.set_do_overwrite_confirmation(True)
        last_dir = load_state().get("log_dir")
        if last_dir and os.path.isdir(last_dir):
            dialog.set_current_folder(last_dir)
        script_name = os.path.splitext(os.path.basename(self.script_path))[0]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dialog.set_current_name(f"{script_name}-{stamp}.log")
        response = dialog.run()
        path = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        if path:
            with update_state() as state:
                state["log_dir"] = os.path.dirname(path)
        return path

    def _write_log_file(self, path, show_errors=True):
        """Write the terminal's full contents (including scrollback) to path.
        Returns True on success.

        The buffer is dumped into an in-memory stream and then written straight
        to the file. Gio.File.replace() is deliberately avoided: it writes via a
        hidden .goutputstream-XXXX temp in the target directory, which the
        once-a-second continuous flush would churn constantly and could strand
        on a crash. A direct write leaves no stray temp files in the user's
        chosen folder.
        """
        try:
            mem = Gio.MemoryOutputStream.new_resizable()
            self.terminal.write_contents_sync(mem, Vte.WriteFlags.DEFAULT, None)
            mem.close(None)
            data = mem.steal_as_bytes().get_data()
        except GLib.Error as e:
            return self._log_write_failed(path, e, show_errors)
        try:
            with open(path, "wb") as f:
                f.write(data)
        except OSError as e:
            return self._log_write_failed(path, e, show_errors)
        return True

    def _log_write_failed(self, path, err, show_errors):
        if show_errors:
            dialog = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Could not save log")
            dialog.format_secondary_text(f"{path}\n{err}")
            dialog.run()
            dialog.destroy()
        else:
            print(f"hwntools: could not save log to {path}: {err}",
                  file=sys.stderr)
        return False

    def on_configure(self, widget, event):
        w, h = self.get_size()
        self._pending_geo = (w, h, event.x, event.y)
        if self._save_pending_id is None:
            self._save_pending_id = GLib.timeout_add(150, self._flush_geo_save)

    def _flush_geo_save(self):
        self._save_pending_id = None
        if self._pending_geo is None:
            return False
        w, h, x, y = self._pending_geo
        self._pending_geo = None
        with update_state() as state:
            state["output_width"] = w
            state["output_height"] = h
            state["output_x"] = x
            state["output_y"] = y
        return False

    def on_key(self, widget, event):
        if _handle_terminal_copy_paste(self.terminal, event):
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
