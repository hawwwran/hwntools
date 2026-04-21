#!/usr/bin/env python3
import os
import signal
import subprocess

signal.signal(signal.SIGHUP, signal.SIG_IGN)

# Bootstrap: check GTK/VTE dependencies before importing anything else
_MISSING_DEPS = []
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
except (ImportError, ValueError):
    _MISSING_DEPS.append("gir1.2-gtk-3.0")

if not _MISSING_DEPS:
    try:
        gi.require_version("Vte", "2.91")
        from gi.repository import Vte
    except (ImportError, ValueError):
        _MISSING_DEPS.append("gir1.2-vte-2.91")

if _MISSING_DEPS:
    import sys

    def _check_deps_and_exit():
        pkgs = " ".join(_MISSING_DEPS)
        print(f"\n  HWN Tools is missing required packages: {pkgs}\n")
        try:
            answer = input("  Install them now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            raise SystemExit(1)
        if answer in ("", "y", "yes"):
            cmd = ["sudo", "apt", "install", "-y"] + _MISSING_DEPS
            print(f"  Running: {' '.join(cmd)}\n")
            result = subprocess.run(cmd)
            if result.returncode == 0:
                print("\n  Dependencies installed. Launching HWN Tools...\n")
                subprocess.Popen(
                    [sys.executable] + sys.argv,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                raise SystemExit(0)
            else:
                print("\n  Installation failed. Please install manually:")
                print(f"    sudo apt install {pkgs}\n")
                raise SystemExit(1)
        else:
            print(f"\n  Please install manually: sudo apt install {pkgs}\n")
            raise SystemExit(1)

    _check_deps_and_exit()

from hwnlib.main_window import HwnTools
from hwnlib.dialogs import _on_main_window_closed

win = HwnTools()
win.connect("destroy", lambda _w: _on_main_window_closed())
win.show_all()
win.update_nav_bar()
Gtk.main()
