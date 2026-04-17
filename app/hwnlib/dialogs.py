import os

from gi.repository import Gtk, Gdk, Pango, GLib, Vte

from .config import parse_config
from .deps import check_dependencies
from .state import load_state, save_state


def _status_hint_row(status=None):
    """HBox: optional status label on the left, copy/paste hint on the right.
    Overrides the status label's start/bottom margins so the row aligns as one line."""
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    box.set_margin_start(8)
    box.set_margin_end(8)
    box.set_margin_bottom(4)
    if status is not None:
        status.set_margin_start(0)
        status.set_margin_bottom(0)
        box.pack_start(status, False, False, 0)
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


def _make_git_terminal(parent, title, command, on_success=None, on_failure=None,
                       cleanup_dir=None):
    """Open a terminal window that runs a git command interactively.
    on_success: called and window auto-closes on exit code 0.
    on_failure: called on non-zero exit or dismiss; window stays open for retry.
    cleanup_dir: directory to remove before retry (partial clone leftovers)."""
    win = Gtk.Window(title=title)
    win.set_transient_for(parent)
    win.set_modal(True)
    win.set_icon_name("application-x-shellscript")
    win.set_default_size(600, 350)
    failed = [False]  # track whether on_failure was already called

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    win.add(vbox)

    terminal = Vte.Terminal()
    terminal.set_font(Pango.FontDescription("monospace 11"))
    terminal.set_color_background(Gdk.RGBA(0.118, 0.118, 0.118, 1))
    terminal.set_color_foreground(Gdk.RGBA(0.831, 0.831, 0.831, 1))
    terminal.set_scroll_on_output(True)
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
        self.set_icon_name("application-x-shellscript")
        self.set_default_size(500, 350)
        self.dep_name = dep_name

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription("monospace 11"))
        self.terminal.set_color_background(Gdk.RGBA(0.118, 0.118, 0.118, 1))
        self.terminal.set_color_foreground(Gdk.RGBA(0.831, 0.831, 0.831, 1))
        self.terminal.set_scroll_on_output(True)
        self.terminal.set_scrollback_lines(10000)
        self.terminal.connect("key-press-event", self.on_key)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_margin_top(6)
        scrolled.set_margin_bottom(6)
        scrolled.set_margin_start(6)
        scrolled.set_margin_end(6)
        scrolled.add(self.terminal)
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
        self.set_icon_name("application-x-shellscript")
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
    def __init__(self, parent, title, script_path):
        super().__init__(title=title)
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_icon_name("application-x-shellscript")
        self.parent_win = parent

        state = load_state()
        w = min(state.get("output_width", 550), 1024)
        h = min(state.get("output_height", 420), 800)
        self.set_default_size(w, h)
        if "output_x" in state and "output_y" in state:
            self.move(state["output_x"], state["output_y"])

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
        self.terminal.set_scroll_on_output(True)
        self.terminal.set_scrollback_lines(10000)
        self.terminal.connect("key-press-event", self.on_key)
        self.terminal.connect("child-exited", self.on_child_exited)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_margin_top(2)
        scrolled.set_margin_bottom(6)
        scrolled.set_margin_start(6)
        scrolled.set_margin_end(6)
        scrolled.add(self.terminal)
        vbox.pack_start(scrolled, True, True, 0)

        self.status = Gtk.Label(label="Running...")
        self.status.set_xalign(0)
        self.status.get_style_context().add_class("terminal-header")
        vbox.pack_start(_status_hint_row(self.status), False, False, 0)

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

    def on_child_exited(self, terminal, status):
        code = status >> 8
        if code == 0:
            self.status.set_text("Done. Press Esc to close.")
        else:
            self.status.set_text(f"Exited with code {code}. Press Esc to close.")

    def on_configure(self, widget, event):
        state = load_state()
        w, h = self.get_size()
        state["output_width"] = w
        state["output_height"] = h
        state["output_x"] = event.x
        state["output_y"] = event.y
        save_state(state)

    def on_key(self, widget, event):
        if _handle_terminal_copy_paste(self.terminal, event):
            return True
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
