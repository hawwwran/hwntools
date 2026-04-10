import os
import shutil
import threading

from gi.repository import Gtk, Gdk, Pango, GLib

from .config import parse_config, label_from_filename
from .constants import PACKAGES_DIR
from .state import load_state, save_state
from .git_packages import (
    _repo_dir_from_url, _ensure_repo, _scan_repo_packages,
    _scan_installed_packages, _check_repo_updates, _is_auth_error,
)
from .package_manager import PackageManager


class SourcesManager(Gtk.Window):
    """Manager for additional script source folders."""
    def __init__(self, parent):
        super().__init__(title="Script Sources Manager")
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_icon_name("application-x-shellscript")
        self.set_default_size(500, 450)
        self.parent_win = parent
        self.updates_available = getattr(parent, "updates_available", {})

        self.connect("key-press-event", self.on_key)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)

        # --- Package repositories (first) ---
        self.repos_label = Gtk.Label()
        self.repos_label.set_xalign(0)
        vbox.pack_start(self.repos_label, False, False, 0)

        srv_scrolled = Gtk.ScrolledWindow()
        srv_scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.servers_list_box = Gtk.ListBox()
        self.servers_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        srv_scrolled.add(self.servers_list_box)
        vbox.pack_start(srv_scrolled, True, True, 0)

        add_srv_btn = Gtk.Button(label="Add Repository\u2026")
        add_srv_btn.connect("clicked", self.on_add_server)
        srv_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        srv_btn_box.pack_start(add_srv_btn, False, False, 0)
        vbox.pack_start(srv_btn_box, False, False, 0)

        # --- Separator ---
        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        # --- Local script source folders (collapsible) ---
        sources = load_state().get("script_sources", [])
        self.folders_expander = Gtk.Expander()
        self.folders_expander.set_expanded(len(sources) > 0)
        self.folders_expander.set_resize_toplevel(True)

        folders_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        folders_vbox.set_margin_top(4)
        folders_vbox.set_vexpand(True)
        folders_vbox.set_hexpand(True)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.list_box = Gtk.ListBox()
        self.list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        scrolled.add(self.list_box)
        folders_vbox.pack_start(scrolled, True, True, 0)

        add_btn = Gtk.Button(label="Add Folder\u2026")
        add_btn.connect("clicked", self.on_add)
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        add_box.pack_start(add_btn, False, False, 0)
        folders_vbox.pack_start(add_box, False, False, 0)

        self.folders_expander.add(folders_vbox)
        has_sources = len(sources) > 0
        vbox.pack_start(self.folders_expander, has_sources, has_sources, 0)

        def on_expander_toggled(expander, param):
            expanded = expander.get_expanded()
            vbox.child_set_property(self.folders_expander, "expand", expanded)
            vbox.child_set_property(self.folders_expander, "fill", expanded)

        self.folders_expander.connect("notify::expanded", on_expander_toggled)

        # --- Done button ---
        done_btn = Gtk.Button(label="Done")
        done_btn.connect("clicked", lambda w: self.destroy())
        done_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        done_box.pack_end(done_btn, False, False, 0)
        vbox.pack_start(done_box, False, False, 0)

        self.add(vbox)
        self.refresh_list()
        self.refresh_servers_list()
        self.show_all()

    def _normalize_source(self, src):
        if isinstance(src, str):
            return {"path": src, "label": ""}
        return src

    @staticmethod
    def _is_cyclic(src_path):
        real_src = os.path.normpath(os.path.realpath(src_path))
        rr = os.path.normpath(os.path.realpath(PACKAGES_DIR))
        if (rr == real_src
                or rr.startswith(real_src + os.sep)
                or real_src.startswith(rr + os.sep)):
            return True
        return False

    def refresh_list(self):
        for child in self.list_box.get_children():
            self.list_box.remove(child)
        sources = load_state().get("script_sources", [])
        self.folders_expander.set_label(f"Additional script source folders ({len(sources)})")
        if not sources:
            empty = Gtk.Label(label="No additional sources configured.")
            empty.get_style_context().add_class("dim-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            self.list_box.add(empty)
        else:
            for i, raw_src in enumerate(sources):
                src = self._normalize_source(raw_src)
                row = Gtk.ListBoxRow()
                vrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                vrow.set_margin_top(6)
                vrow.set_margin_bottom(6)
                vrow.set_margin_start(8)
                vrow.set_margin_end(8)
                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                path_lbl = Gtk.Label()
                path_lbl.set_xalign(0)
                path_lbl.set_ellipsize(Pango.EllipsizeMode.START)
                src_invalid = not os.path.isdir(src["path"]) or self._is_cyclic(src["path"])
                if src_invalid:
                    path_lbl.set_markup(f'<span color="#cc3333">{GLib.markup_escape_text(src["path"])}</span>')
                else:
                    path_lbl.set_text(src["path"])
                    path_lbl.get_style_context().add_class("dim-label")
                hbox.pack_start(path_lbl, True, True, 0)
                remove_btn = Gtk.Button.new_from_icon_name("list-remove", Gtk.IconSize.BUTTON)
                remove_btn.set_tooltip_text("Remove from sources")
                remove_btn.connect("clicked", self.on_remove, i)
                hbox.pack_end(remove_btn, False, False, 0)
                vrow.pack_start(hbox, False, False, 0)
                label_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                label_label = Gtk.Label(label="Label:")
                label_label.set_xalign(0)
                label_box.pack_start(label_label, False, False, 0)
                entry = Gtk.Entry()
                entry.set_text(src.get("label", ""))
                entry.set_placeholder_text("(use folder name)")
                entry.connect("changed", self.on_label_changed, i)
                label_box.pack_start(entry, True, True, 0)
                vrow.pack_start(label_box, False, False, 0)
                row.add(vrow)
                self.list_box.add(row)
        self.list_box.show_all()

    def on_label_changed(self, entry, index):
        state = load_state()
        sources = state.get("script_sources", [])
        if index < len(sources):
            src = self._normalize_source(sources[index])
            src["label"] = entry.get_text().strip()
            sources[index] = src
            state["script_sources"] = sources
            save_state(state)
            self.parent_win.refresh_view()

    def on_add(self, button):
        dialog = Gtk.FileChooserDialog(
            title="Select Script Source Folder",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            folder = dialog.get_filename()
            state = load_state()
            sources = state.get("script_sources", [])
            paths = [self._normalize_source(s)["path"] for s in sources]
            if folder not in paths:
                sources.append({"path": folder, "label": ""})
                state["script_sources"] = sources
                save_state(state)
                self.folders_expander.set_expanded(True)
                self.refresh_list()
                self.parent_win.refresh_view()
        dialog.destroy()

    def on_remove(self, button, index):
        state = load_state()
        sources = state.get("script_sources", [])
        if index < len(sources):
            del sources[index]
            state["script_sources"] = sources
            save_state(state)
            self.refresh_list()
            self.parent_win.refresh_view()

    # --- Package repositories ---

    def refresh_servers_list(self):
        for child in self.servers_list_box.get_children():
            self.servers_list_box.remove(child)
        repos = load_state().get("package_repos", [])
        self.repos_label.set_text(f"Package repositories ({len(repos)}):")
        if not repos:
            empty = Gtk.Label(label="No package repositories configured.")
            empty.get_style_context().add_class("dim-label")
            empty.set_margin_top(12)
            empty.set_margin_bottom(12)
            self.servers_list_box.add(empty)
            self.servers_list_box.show_all()
            return

        deferred_visibility = []
        for i, repo in enumerate(repos):
                row = Gtk.ListBoxRow()
                vrow = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                vrow.set_margin_top(6)
                vrow.set_margin_bottom(6)
                vrow.set_margin_start(8)
                vrow.set_margin_end(8)

                url_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                url_entry = Gtk.Entry()
                url_entry.set_text(repo.get("url", ""))
                url_entry.set_placeholder_text("https://github.com/user/repo.git")
                url_box.pack_start(url_entry, True, True, 0)
                remove_btn = Gtk.Button.new_from_icon_name("list-remove", Gtk.IconSize.BUTTON)
                remove_btn.set_tooltip_text("Remove repository")
                remove_btn.connect("clicked", self.on_remove_server, i)
                url_box.pack_end(remove_btn, False, False, 0)
                vrow.pack_start(url_box, False, False, 0)

                path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                path_label = Gtk.Label(label="Path:")
                path_label.set_xalign(0)
                path_box.pack_start(path_label, False, False, 0)
                path_entry = Gtk.Entry()
                path_entry.set_text(repo.get("path", ""))
                path_entry.set_placeholder_text("(optional subfolder, e.g. packages)")
                path_entry.connect("changed", self.on_repo_field_changed, i, "path")
                path_box.pack_start(path_entry, True, True, 0)
                init_btn = Gtk.Button(label="Init Repository")
                init_btn.set_no_show_all(True)
                path_box.pack_end(init_btn, False, False, 0)
                vrow.pack_start(path_box, False, False, 0)

                btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

                status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
                status_lbl = Gtk.Label()
                status_lbl.set_xalign(0)
                status_box.pack_start(status_lbl, False, False, 0)
                spinner = Gtk.Spinner()
                spinner.set_size_request(14, 14)
                spinner.set_no_show_all(True)
                status_box.pack_start(spinner, False, False, 0)
                btn_row.pack_start(status_box, True, True, 0)

                manage_btn = Gtk.Button(label="Manage Packages")
                manage_btn.set_no_show_all(True)
                manage_btn.connect("clicked", self.on_manage_packages, i)
                init_btn.connect("clicked", self._on_init_repo, i, init_btn, spinner, status_lbl, manage_btn)
                btn_row.pack_end(manage_btn, False, False, 0)
                vrow.pack_start(btn_row, False, False, 0)

                url_entry.connect("changed", self.on_repo_field_changed, i, "url", init_btn, manage_btn)

                repo_url = repo.get("url", "")
                deferred_visibility.append((repo_url, init_btn, manage_btn, spinner, status_lbl, repo))

                row.add(vrow)
                self.servers_list_box.add(row)
        self.servers_list_box.show_all()

        for repo_url, init_btn, manage_btn, spinner, status_lbl, repo in deferred_visibility:
            init_btn.hide()
            manage_btn.hide()
            spinner.hide()
            if repo_url:
                repo_dir = _repo_dir_from_url(repo_url, repo.get("path", ""))
                if os.path.isdir(os.path.join(repo_dir, ".git")):
                    manage_btn.show()
                    spinner.start()
                    spinner.show()
                    self._check_repo(repo, status_lbl, spinner)
                else:
                    init_btn.show()

    def on_add_server(self, button):
        state = load_state()
        repos = state.get("package_repos", [])
        repos.append({"url": "", "path": ""})
        state["package_repos"] = repos
        save_state(state)
        self.refresh_servers_list()

    def _on_init_repo(self, button, index, init_btn, spinner, status_lbl, manage_btn):
        repos = load_state().get("package_repos", [])
        if index >= len(repos):
            return
        repo = repos[index]
        url = repo.get("url", "")
        if not url:
            return

        init_btn.hide()
        spinner.start()
        spinner.show()
        status_lbl.set_text("Cloning\u2026")

        def worker():
            path = repo.get("path", "")
            repo_dir, pkg_base, error = _ensure_repo(url, path)
            if error:
                GLib.idle_add(done, error)
            else:
                GLib.idle_add(done, None)

        def done(error):
            spinner.stop()
            spinner.hide()
            if error and _is_auth_error(error):
                status_lbl.set_text("")
                self._open_git_terminal(url, repo, init_btn, spinner, status_lbl, manage_btn)
            elif error:
                status_lbl.set_markup(f'<span size="small" color="#cc3333">{GLib.markup_escape_text(error)}</span>')
                init_btn.show()
            else:
                status_lbl.set_text("")
                manage_btn.show()
                self._check_repo(repo, status_lbl, spinner)

        threading.Thread(target=worker, daemon=True).start()

    def _open_git_terminal(self, url, repo, init_btn, spinner, status_lbl, manage_btn):
        """Open a VTE terminal running git clone so the user can authenticate interactively."""
        import shlex
        from .dialogs import _make_git_terminal
        path = repo.get("path", "")
        repo_dir = _repo_dir_from_url(url, path)
        q_url, q_dir = shlex.quote(url), shlex.quote(repo_dir)
        if path:
            q_path = shlex.quote(path)
            cmd = f"git clone --depth 1 --filter=blob:none --sparse {q_url} {q_dir}"
            cmd += f" && cd {q_dir} && git sparse-checkout set {q_path}"
        else:
            cmd = f"git clone --depth 1 {q_url} {q_dir}"

        def on_success():
            manage_btn.show()
            self._check_repo(repo, status_lbl, spinner)

        def on_failure():
            status_lbl.set_markup(
                '<span size="small" color="#cc3333">Authentication required</span>')
            init_btn.show()

        _make_git_terminal(self, "Git authentication required", cmd,
                           on_success=on_success, on_failure=on_failure,
                           cleanup_dir=repo_dir)

    def on_remove_server(self, button, index):
        state = load_state()
        repos = state.get("package_repos", [])
        if index < len(repos):
            repo = repos[index]
            url = repo.get("url", "")
            path = repo.get("path", "")

            if url:
                repo_dir = _repo_dir_from_url(url, path)
                cloned_pkg_base = os.path.join(repo_dir, path) if path else repo_dir
                if os.path.isdir(cloned_pkg_base):
                    for rpkg in _scan_repo_packages(cloned_pkg_base):
                        dest = os.path.join(PACKAGES_DIR, rpkg["folder"])
                        if os.path.isdir(dest):
                            shutil.rmtree(dest, ignore_errors=True)

                if os.path.isdir(repo_dir):
                    shutil.rmtree(repo_dir, ignore_errors=True)

            del repos[index]
            state["package_repos"] = repos
            save_state(state)
            self.refresh_servers_list()
            self.parent_win.refresh_view()

    def on_repo_field_changed(self, entry, index, field, init_btn=None, manage_btn=None):
        state = load_state()
        repos = state.get("package_repos", [])
        if index < len(repos):
            repos[index][field] = entry.get_text().strip()
            state["package_repos"] = repos
            save_state(state)
            if field == "url" and init_btn and manage_btn:
                url = entry.get_text().strip()
                if url and not manage_btn.get_visible():
                    init_btn.show()
                elif not url:
                    init_btn.hide()

    def on_manage_packages(self, button, index):
        state = load_state()
        repos = state.get("package_repos", [])
        if index < len(repos):
            repo = repos[index]
            if repo.get("url"):
                pm = PackageManager(self, repo)
                pm.connect("destroy", self._on_package_manager_closed)

    def _on_package_manager_closed(self, widget):
        self.refresh_servers_list()
        self.parent_win._check_for_updates()
        self.parent_win.refresh_view()

    def _check_repo(self, repo, status_lbl, spinner):
        def worker():
            url = repo.get("url", "")
            path = repo.get("path", "")
            try:
                has_updates, updatable, error = _check_repo_updates(url, path)
                if error:
                    GLib.idle_add(done, 0, 0, None, error)
                    return
                repo_dir = _repo_dir_from_url(url, path)
                pkg_base = os.path.join(repo_dir, path) if path else repo_dir
                remote_packages = _scan_repo_packages(pkg_base)
                installed = _scan_installed_packages()
                total = len(remote_packages)
                inst_count = sum(1 for p in remote_packages if p["name"] in installed)
                GLib.idle_add(done, total, inst_count, updatable, None)
            except Exception as e:
                GLib.idle_add(done, 0, 0, None, str(e))

        def done(total_available, installed_count, updatable, error):
            spinner.stop()
            spinner.hide()
            if error:
                status_lbl.set_markup(f'<span size="small" color="#999">{GLib.markup_escape_text(error)}</span>')
            else:
                parts = [f'{total_available} available', f'{installed_count} installed']
                if updatable:
                    self.updates_available[repo.get("url", "")] = updatable
                    parts.append(f'<span color="#cc3333">{len(updatable)} to update</span>')
                status_lbl.set_markup(f'<span size="small">{", ".join(parts)}</span>')

        threading.Thread(target=worker, daemon=True).start()

    def on_key(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.destroy()
            return True
        return False
