#!/usr/bin/env python3
"""Tests for hwnlib.identity: the .desktop entries GNOME uses to show the
hwntools icons in the taskbar.

Run standalone: python3 tests/test_identity.py
Exits 0 on success, non-zero on any failure. No pytest dependency.
"""

import os
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib  # noqa: E402

from hwnlib import identity  # noqa: E402


def _use_data_home(tmp):
    identity._APPLICATIONS_DIR = os.path.join(tmp, "applications")
    identity._ICON_INSTALL_DIR = os.path.join(tmp, "icons")
    identity.PLATFORM = "linux"


def test_exec_line_parses_back_to_the_launcher_path():
    """GLib must recover the exact path from Exec, as GNOME does at launch."""
    real_app_dir = identity.APP_DIR
    try:
        for app_dir in ["/home/u/hwn tools", '/tmp/a"b$c`d\\e', "/tmp/100%"]:
            identity.APP_DIR = app_dir
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "t.desktop")
                with open(path, "w") as f:
                    f.write(identity.desktop_entry(identity.APP_ID, "N", "C", False))
                info = Gio.DesktopAppInfo.new_from_filename(path)
                assert info is not None, f"GLib rejected the entry for {app_dir!r}"
                # GLib expands field codes (%% -> %) before splitting the line.
                ok, argv = GLib.shell_parse_argv(info.get_commandline().replace("%%", "%"))
                assert ok and argv[1] == os.path.join(app_dir, "hwntools.py"), argv
    finally:
        identity.APP_DIR = real_app_dir


def test_install_writes_both_entries_and_icons():
    with tempfile.TemporaryDirectory() as tmp:
        _use_data_home(tmp)
        identity.IS_DEV_CHECKOUT = False
        identity.install_desktop_entries()
        for app_id in (identity.APP_ID, identity.OUTPUT_APP_ID):
            assert os.path.isfile(os.path.join(tmp, "icons", f"{app_id}.svg"))
            info = Gio.DesktopAppInfo.new_from_filename(
                os.path.join(tmp, "applications", f"{app_id}.desktop"))
            assert info.get_string("StartupWMClass") == app_id
            assert info.get_string("Icon") == app_id
        output = Gio.DesktopAppInfo.new_from_filename(
            os.path.join(tmp, "applications", f"{identity.OUTPUT_APP_ID}.desktop"))
        assert output.get_nodisplay(), "output entry must stay out of the menu"


def test_dev_checkout_keeps_an_existing_entry():
    with tempfile.TemporaryDirectory() as tmp:
        _use_data_home(tmp)
        os.makedirs(os.path.join(tmp, "applications"))
        launcher = os.path.join(tmp, "applications", f"{identity.APP_ID}.desktop")
        with open(launcher, "w") as f:
            f.write("installed copy's entry\n")
        identity.IS_DEV_CHECKOUT = True
        identity.install_desktop_entries()
        with open(launcher) as f:
            assert f.read() == "installed copy's entry\n"
        assert os.path.isfile(os.path.join(
            tmp, "applications", f"{identity.OUTPUT_APP_ID}.desktop"))


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}", file=sys.stderr)
        except Exception as e:  # noqa: BLE001 report every failing test, not just the first
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
