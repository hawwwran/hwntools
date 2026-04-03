import os

from gi.repository import Gtk, Gdk, Pango, GLib, Vte

from .config import parse_config
from .deps import check_dependencies
from .state import load_state, save_state


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
        self.status.set_margin_start(8)
        self.status.set_margin_bottom(4)
        vbox.pack_start(self.status, False, False, 0)

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
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
