"""WSLg session anchor — holds the display connection alive while hwntools instances are running."""
import subprocess
import signal

signal.signal(signal.SIGHUP, signal.SIG_IGN)

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib


def _start_monitoring():
    """Begin checking for hwntools instances after initial grace period."""
    GLib.timeout_add_seconds(2, _check_instances)
    return False


def _check_instances():
    """Exit if no hwntools.py processes are running."""
    try:
        r = subprocess.run(
            ["pgrep", "-cf", "hwntools.py"],
            capture_output=True, text=True,
        )
        count = int(r.stdout.strip())
    except Exception:
        count = 1
    if count == 0:
        Gtk.main_quit()
        return False
    return True


def main():
    _win = Gtk.Window()
    _win.set_default_size(1, 1)
    _win.set_decorated(False)
    _win.set_skip_taskbar_hint(True)
    _win.set_skip_pager_hint(True)
    _win.set_opacity(0)
    _win.show()

    GLib.timeout_add_seconds(10, _start_monitoring)
    Gtk.main()


if __name__ == "__main__":
    main()
