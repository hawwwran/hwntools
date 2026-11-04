"""How hwntools windows are told apart on the desktop.

The launcher and its dialogs show the launcher icon in the taskbar; script
output windows get a taskbar entry of their own with a variant icon.

GNOME Shell takes a window's taskbar icon from the .desktop file matching the
window's app ID, never from Gtk.Window.set_icon_name on Wayland (GTK3 has no
protocol to send it). The app ID is the Wayland app_id, which GTK3 reads from
g_get_prgname() each time a toplevel maps, or on X11 the WM_CLASS, which
Gtk.Window.set_wmclass sets per window. A window with a transient parent always
shares the parent's taskbar entry, whatever its own ID.
"""
import contextlib
import os
import re
import sys

from gi.repository import GLib, Gtk

from .constants import APP_DIR, PLATFORM, IS_DEV_CHECKOUT

APP_ID = "com.hawwwran.hwntools"
OUTPUT_APP_ID = "com.hawwwran.hwntools.output"
ICONS_DIR = os.path.join(APP_DIR, "icons")

_DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
_APPLICATIONS_DIR = os.path.join(_DATA_HOME, "applications")
_ICON_INSTALL_DIR = os.path.join(_DATA_HOME, "icons", "hicolor", "scalable", "apps")


def setup_app_identity():
    """Call once before the first window maps."""
    GLib.set_prgname(APP_ID)
    install_desktop_entries()
    # Resolves the icon names in-process even where nothing is installed (WSL).
    Gtk.IconTheme.get_default().append_search_path(ICONS_DIR)
    Gtk.Window.set_default_icon_name(APP_ID)


@contextlib.contextmanager
def program_name(name):
    """Map a window inside this block to make name its Wayland app_id."""
    previous = GLib.get_prgname()
    GLib.set_prgname(name)
    try:
        yield
    finally:
        GLib.set_prgname(previous)


def install_desktop_entries():
    """Install the icons, and the .desktop entries tying them to the app IDs.

    The launcher entry is visible in the applications menu; the output entry
    is hidden and only serves the taskbar. Linux only: on WSL the entries would
    turn up in the Windows Start menu. A dev checkout writes an entry only when
    it is missing, so the menu keeps launching the installed copy.
    """
    if PLATFORM != "linux":
        return
    entries = [
        (APP_ID, "HWN Tools", "Run your shell and Python scripts from a button launcher", False),
        (OUTPUT_APP_ID, "HWN Tools Output", "Script output window of HWN Tools", True),
    ]
    try:
        os.makedirs(_ICON_INSTALL_DIR, exist_ok=True)
        os.makedirs(_APPLICATIONS_DIR, exist_ok=True)
        for app_id, name, comment, hidden in entries:
            with open(os.path.join(ICONS_DIR, f"{app_id}.svg"), "rb") as f:
                _write_if_changed(os.path.join(_ICON_INSTALL_DIR, f"{app_id}.svg"), f.read())
            desktop_path = os.path.join(_APPLICATIONS_DIR, f"{app_id}.desktop")
            if IS_DEV_CHECKOUT and os.path.exists(desktop_path):
                continue
            _write_if_changed(desktop_path, desktop_entry(app_id, name, comment, hidden).encode())
    except OSError as e:
        print(f"hwntools: could not install desktop entries: {e}", file=sys.stderr)


def desktop_entry(app_id, name, comment, hidden):
    launcher = os.path.join(APP_DIR, "hwntools.py")
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        f"Name={name}",
        f"Comment={comment}",
        f"Exec={_exec_arg(sys.executable or 'python3')} {_exec_arg(launcher)}",
        f"Icon={app_id}",
        "Terminal=false",
        "Categories=Utility;",
        f"StartupWMClass={app_id}",
    ]
    if hidden:
        lines.append("NoDisplay=true")
    return "\n".join(lines) + "\n"


def _exec_arg(arg):
    """Quote one Exec argument per the Desktop Entry spec: escape what stays
    special inside double quotes, then every backslash once more because the
    whole line is also a string value, and double % so it is no field code."""
    quoted = '"' + re.sub(r'(["`$\\])', r"\\\1", arg) + '"'
    return quoted.replace("\\", "\\\\").replace("%", "%%")


def _write_if_changed(path, content):
    try:
        with open(path, "rb") as f:
            if f.read() == content:
                return
    except FileNotFoundError:
        pass
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as f:
        f.write(content)
    os.replace(tmp, path)
