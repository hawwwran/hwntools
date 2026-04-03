import io
import os
import shutil
import threading
import urllib.request
import zipfile

from gi.repository import Gtk, Gdk, GLib

from .constants import VERSION, APP_DIR
from .git_packages import _check_app_update, HWNTOOLS_DOWNLOAD


class UpdateManager(Gtk.Window):
    """Window for checking and applying HWN Tools updates."""

    def __init__(self, parent):
        super().__init__(title="Update HWN Tools")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_icon_name("application-x-shellscript")
        self.set_default_size(400, -1)
        self.parent_win = parent
        self.destroyed = False
        self.latest_version = None

        self.connect("destroy", lambda w: setattr(self, "destroyed", True))
        self.connect("key-press-event", self.on_key)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_top(16)
        vbox.set_margin_bottom(16)
        vbox.set_margin_start(20)
        vbox.set_margin_end(20)

        self.current_label = Gtk.Label()
        self.current_label.set_markup(f"Current version: <b>v{VERSION}</b>")
        self.current_label.set_xalign(0)
        vbox.pack_start(self.current_label, False, False, 0)

        self.remote_label = Gtk.Label()
        self.remote_label.set_xalign(0)
        vbox.pack_start(self.remote_label, False, False, 0)

        self.status_label = Gtk.Label()
        self.status_label.set_xalign(0)
        self.status_label.get_style_context().add_class("dim-label")
        vbox.pack_start(self.status_label, False, False, 0)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.update_btn = Gtk.Button(label="Update")
        self.update_btn.set_sensitive(False)
        self.update_btn.connect("clicked", self.on_update)
        btn_box.pack_start(self.update_btn, False, False, 0)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(16, 16)
        btn_box.pack_start(self.spinner, False, False, 0)

        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: self.destroy())
        btn_box.pack_end(close_btn, False, False, 0)

        vbox.pack_start(btn_box, False, False, 0)

        self.add(vbox)
        self.show_all()
        self._check()

    def _check(self):
        self.remote_label.set_text("Checking for updates\u2026")
        self.spinner.start()

        def worker():
            remote_ver, has_update, error = _check_app_update()
            GLib.idle_add(self._on_check_done, remote_ver, has_update, error)

        threading.Thread(target=worker, daemon=True).start()

    def _on_check_done(self, remote_ver, has_update, error):
        if self.destroyed:
            return
        self.spinner.stop()
        if error:
            self.remote_label.set_markup(f'<span color="#cc3333">Check failed: {GLib.markup_escape_text(error)}</span>')
            return
        self.latest_version = remote_ver
        is_dev = os.path.isdir(os.path.join(APP_DIR, "..", ".git")) or os.path.isfile(os.path.join(APP_DIR, "CLAUDE.md"))
        if has_update:
            self.remote_label.set_markup(
                f"Latest version: <b><span color=\"#2e7d32\">v{remote_ver}</span></b> \u2014 update available!"
            )
            if is_dev:
                self.status_label.set_markup('<span color="#cc3333">Development install detected. Update disabled.</span>')
            else:
                self.update_btn.set_sensitive(True)
        else:
            self.remote_label.set_markup(f"Latest version: <b>v{remote_ver}</b> \u2014 you are up to date.")

    def on_update(self, button):
        button.set_sensitive(False)
        button.set_label("Updating\u2026")
        self.status_label.set_text("Downloading\u2026")
        self.spinner.start()

        def worker():
            try:
                req = urllib.request.Request(HWNTOOLS_DOWNLOAD)
                resp = urllib.request.urlopen(req, timeout=30)
                zip_bytes = resp.read()
                GLib.idle_add(self._apply_update, zip_bytes)
            except Exception as e:
                GLib.idle_add(self._on_update_error, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_error(self, error):
        if self.destroyed:
            return
        self.spinner.stop()
        self.status_label.set_markup(f'<span color="#cc3333">Update failed: {GLib.markup_escape_text(error)}</span>')
        self.update_btn.set_label("Update")
        self.update_btn.set_sensitive(True)

    def _apply_update(self, zip_bytes):
        if self.destroyed:
            return
        self.status_label.set_text("Installing\u2026")
        try:
            import tempfile
            tmp_dir = tempfile.mkdtemp(prefix="hwntools_update_")
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                zf.extractall(tmp_dir)

            skip = {".state.json", "packages", ".packages", "hwntools.vbs", "__pycache__", "CLAUDE.md"}
            for item in os.listdir(tmp_dir):
                if item in skip:
                    continue
                src = os.path.join(tmp_dir, item)
                dst = os.path.join(APP_DIR, item)
                if os.path.isdir(src):
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    shutil.copytree(src, dst)
                else:
                    shutil.copy2(src, dst)

            shutil.rmtree(tmp_dir, ignore_errors=True)

            self.spinner.stop()
            self.status_label.set_markup(
                f'<span color="#2e7d32">Updated to v{self.latest_version}. Restart HWN Tools to apply.</span>'
            )
            self.update_btn.set_label("Updated")
            self.update_btn.set_sensitive(False)

            self.parent_win._app_update_available = False
            self.parent_win._update_dot_state()

        except Exception as e:
            self._on_update_error(str(e))

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
