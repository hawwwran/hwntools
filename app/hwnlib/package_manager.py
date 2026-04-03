import os
import shutil
import threading

from gi.repository import Gtk, Gdk, GLib

from .config import parse_config, label_from_filename
from .constants import PACKAGES_DIR
from .git_packages import (
    _version_newer, _scan_installed_packages, _ensure_repo, _scan_repo_packages,
)
from .state import load_state, save_state


class PackageManager(Gtk.Window):
    """Window for browsing, installing, and updating packages from a git repository."""

    def __init__(self, parent, repo):
        url = repo.get("url", "")
        super().__init__(title=f"Package Manager \u2014 {url}")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_icon_name("application-x-shellscript")
        self.set_default_size(600, 500)
        self.repo = repo
        self.parent_win = parent
        self.destroyed = False

        self.connect("destroy", lambda w: setattr(self, "destroyed", True))
        self.connect("key-press-event", self.on_key)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        installed_label = Gtk.Label()
        installed_label.set_markup("<b>Installed Packages</b>")
        installed_label.set_xalign(0)
        vbox.pack_start(installed_label, False, False, 0)

        installed_scrolled = Gtk.ScrolledWindow()
        installed_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.installed_box = Gtk.ListBox()
        self.installed_box.set_selection_mode(Gtk.SelectionMode.NONE)
        installed_scrolled.add(self.installed_box)
        vbox.pack_start(installed_scrolled, True, True, 0)

        available_label = Gtk.Label()
        available_label.set_markup("<b>Available Packages</b>")
        available_label.set_xalign(0)
        vbox.pack_start(available_label, False, False, 0)

        available_scrolled = Gtk.ScrolledWindow()
        available_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.available_box = Gtk.ListBox()
        self.available_box.set_selection_mode(Gtk.SelectionMode.NONE)
        available_scrolled.add(self.available_box)
        vbox.pack_start(available_scrolled, True, True, 0)

        self.status_label = Gtk.Label()
        self.status_label.set_xalign(0)
        self.status_label.get_style_context().add_class("dim-label")
        vbox.pack_start(self.status_label, False, False, 0)

        close_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        close_btn = Gtk.Button(label="Close")
        close_btn.connect("clicked", lambda w: self.destroy())
        close_box.pack_end(close_btn, False, False, 0)
        vbox.pack_start(close_box, False, False, 0)

        self.add(vbox)
        self.show_all()
        self._set_status("Syncing repository\u2026")
        self._load_data()

    def _set_status(self, text):
        self.status_label.set_text(text)

    def _load_data(self):
        def worker():
            url = self.repo.get("url", "")
            path = self.repo.get("path", "")
            repo_dir, pkg_base, error = _ensure_repo(url, path)
            if error:
                GLib.idle_add(self._on_load_error, error)
                return
            remote_packages = _scan_repo_packages(pkg_base)
            GLib.idle_add(self._populate, remote_packages)

        threading.Thread(target=worker, daemon=True).start()

    def _on_load_error(self, error):
        if self.destroyed:
            return
        self._set_status(f"Error: {error}")

    def _populate(self, remote_packages):
        if self.destroyed:
            return

        for child in self.installed_box.get_children():
            self.installed_box.remove(child)
        for child in self.available_box.get_children():
            self.available_box.remove(child)

        installed = _scan_installed_packages()

        has_installed = False
        has_available = False

        for rpkg in remote_packages:
            name = rpkg["name"]
            remote_ver = rpkg["version"]
            label = rpkg["label"]

            if name in installed:
                has_installed = True
                local_ver = installed[name]["version"]
                row = self._make_installed_row(name, label, local_ver, remote_ver, rpkg, installed)
                self.installed_box.add(row)
            else:
                has_available = True
                row = self._make_available_row(name, label, remote_ver, rpkg)
                self.available_box.add(row)

        if not has_installed:
            lbl = Gtk.Label(label="No packages installed from this repository.")
            lbl.get_style_context().add_class("dim-label")
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            self.installed_box.add(lbl)

        if not has_available:
            lbl = Gtk.Label(label="All packages are installed.")
            lbl.get_style_context().add_class("dim-label")
            lbl.set_margin_top(8)
            lbl.set_margin_bottom(8)
            self.available_box.add(lbl)

        self.installed_box.show_all()
        self.available_box.show_all()
        self._set_status("")

    def _make_installed_row(self, name, label, local_ver, remote_ver, rpkg, installed):
        row = Gtk.ListBoxRow()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        vbox.set_margin_top(6)
        vbox.set_margin_bottom(6)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_lbl = Gtk.Label()
        can_update = _version_newer(remote_ver, local_ver)
        if can_update:
            name_lbl.set_markup(
                f"<b>{GLib.markup_escape_text(label)}</b>  "
                f"v{local_ver} \u2192 <b><span color=\"#2e7d32\">v{remote_ver}</span></b>"
            )
        else:
            name_lbl.set_markup(
                f"<b>{GLib.markup_escape_text(label)}</b>  v{local_ver}"
            )
        name_lbl.set_xalign(0)
        hbox.pack_start(name_lbl, True, True, 0)

        update_btn = Gtk.Button(label="Update Package")
        update_btn.set_sensitive(can_update)
        update_btn.connect("clicked", self._on_install, rpkg)
        hbox.pack_end(update_btn, False, False, 0)
        vbox.pack_start(hbox, False, False, 0)

        expander = Gtk.Expander(label="Show package details")
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        detail_box.set_margin_start(16)

        hidden = set(load_state().get("hidden_scripts", []))
        local_pkg_path = installed.get(name, {}).get("path", "")
        checkboxes = []

        for script in rpkg.get("scripts", []):
            spath = script["path"]
            sver = script.get("version", "?")
            slabel = script.get("label", label_from_filename(os.path.splitext(os.path.basename(spath))[0]))
            sdesc = script.get("description", "")
            starget = script.get("target", "")
            local_sver = "?"
            script_full_path = ""
            if local_pkg_path:
                local_script = os.path.join(local_pkg_path, spath)
                script_full_path = local_script
                if os.path.isfile(local_script):
                    sc = parse_config(local_script)
                    local_sver = sc.get("version", "?")
            srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

            if script_full_path:
                cb = Gtk.CheckButton()
                cb.set_active(script_full_path not in hidden)
                cb.connect("toggled", self._on_script_toggled, script_full_path)
                srow.pack_start(cb, False, False, 0)
                checkboxes.append(cb)

            if starget:
                target_icon = "computer-symbolic"
                timg = Gtk.Image.new_from_icon_name(target_icon, Gtk.IconSize.MENU)
                timg.set_tooltip_text(f"Target: {starget}")
                srow.pack_start(timg, False, False, 0)
            slbl = Gtk.Label()
            slbl.set_xalign(0)
            if local_sver != "?" and sver != "?" and _version_newer(sver, local_sver):
                slbl.set_markup(
                    f"<small>{GLib.markup_escape_text(slabel)}  "
                    f"{local_sver} \u2192 <b><span color=\"#2e7d32\">{sver}</span></b></small>"
                )
            else:
                slbl.set_markup(
                    f"<small>{GLib.markup_escape_text(slabel)}  v{local_sver}</small>"
                )
            if sdesc:
                slbl.set_tooltip_text(sdesc)
            srow.pack_start(slbl, True, True, 0)
            detail_box.pack_start(srow, False, False, 0)

        if checkboxes:
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_row.set_margin_top(4)
            sel_all = Gtk.Button(label="Show all")
            unsel_all = Gtk.Button(label="Hide all")

            def on_select_all(widget, cbs=checkboxes):
                for cb in cbs:
                    cb.set_active(True)

            def on_unselect_all(widget, cbs=checkboxes):
                for cb in cbs:
                    cb.set_active(False)

            sel_all.connect("clicked", on_select_all)
            unsel_all.connect("clicked", on_unselect_all)
            btn_row.pack_start(sel_all, False, False, 0)
            btn_row.pack_start(unsel_all, False, False, 0)
            detail_box.pack_start(btn_row, False, False, 0)

        expander.add(detail_box)
        vbox.pack_start(expander, False, False, 0)

        row.add(vbox)
        return row

    def _make_available_row(self, name, label, remote_ver, rpkg):
        row = Gtk.ListBoxRow()
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hbox.set_margin_top(6)
        hbox.set_margin_bottom(6)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        name_lbl = Gtk.Label()
        name_lbl.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>  v{remote_ver}")
        name_lbl.set_xalign(0)
        hbox.pack_start(name_lbl, True, True, 0)
        get_btn = Gtk.Button(label="Get")
        get_btn.connect("clicked", self._on_install, rpkg)
        hbox.pack_end(get_btn, False, False, 0)
        row.add(hbox)
        return row

    def _on_script_toggled(self, checkbox, script_path):
        state = load_state()
        hidden = state.get("hidden_scripts", [])
        if checkbox.get_active():
            hidden = [h for h in hidden if h != script_path]
        else:
            if script_path not in hidden:
                hidden.append(script_path)
        state["hidden_scripts"] = hidden
        save_state(state)
        try:
            self.parent_win.parent_win.refresh_view()
        except Exception:
            pass

    def _on_install(self, button, rpkg):
        button.set_sensitive(False)
        orig_label = button.get_label()
        button.set_label("Installing\u2026")
        pkg_name = rpkg["name"]
        src_path = rpkg["path"]
        folder = rpkg["folder"]
        self._set_status(f"Installing {pkg_name}\u2026")

        try:
            ok, err = self._validate_package(src_path, pkg_name)
            if not ok:
                self._show_validation_error(err, button, orig_label)
                return

            os.makedirs(PACKAGES_DIR, exist_ok=True)
            dest = os.path.join(PACKAGES_DIR, folder)
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(src_path, dest)

            for root, _dirs, files in os.walk(dest):
                for f in files:
                    if f.endswith((".sh", ".py")):
                        fpath = os.path.join(root, f)
                        os.chmod(fpath, os.stat(fpath).st_mode | 0o755)

            self._set_status(f"Installed {pkg_name} successfully.")
            self._load_data()
            try:
                self.parent_win.parent_win.refresh_view()
            except Exception:
                pass

        except Exception as e:
            self._show_validation_error(str(e), button, orig_label)

    def _validate_package(self, pkg_dir, expected_name):
        config_file = os.path.join(pkg_dir, ".config")
        if not os.path.isfile(config_file):
            return False, "Package is missing .config file."

        config = parse_config(config_file)
        pkg_name = config.get("package")
        if not pkg_name:
            return False, "Package .config is missing @package field."
        if pkg_name != expected_name:
            return False, f"Package name mismatch: expected '{expected_name}', got '{pkg_name}'."
        if not config.get("version"):
            return False, "Package .config is missing @version field."

        for root, _dirs, files in os.walk(pkg_dir):
            for f in files:
                if not f.endswith((".sh", ".py")):
                    continue
                fpath = os.path.join(root, f)
                rel = os.path.relpath(fpath, pkg_dir)
                sc = parse_config(fpath)
                s_pkg = sc.get("package")
                if not s_pkg:
                    return False, f"Script '{rel}' is missing @package field."
                if s_pkg != expected_name:
                    return False, f"Script '{rel}' has wrong @package: '{s_pkg}' (expected '{expected_name}')."
                if not sc.get("version"):
                    return False, f"Script '{rel}' is missing @version field."

        return True, ""

    def _show_validation_error(self, message, button, orig_label):
        if self.destroyed:
            return
        self._set_status("")
        button.set_label(orig_label)
        button.set_sensitive(True)
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Package validation failed",
        )
        dialog.format_secondary_text(message)
        dialog.run()
        dialog.destroy()

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
