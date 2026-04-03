import os
import subprocess
import threading

from gi.repository import Gtk, Gdk, Gio, Pango, GLib

from .config import parse_config, label_from_filename, subsequence_match
from .constants import (
    ROOT_DIR, PACKAGES_DIR, PLATFORM, ICON_SIZE,
    DEFAULT_SCRIPT_ICON, DEFAULT_FOLDER_ICON, CSS,
)
from .deps import check_dependencies
from .dialogs import OutputDialog, DepDialog
from .git_packages import _check_repo_updates
from .sources_manager import SourcesManager
from .state import load_state, save_state


class HwnTools(Gtk.Window):
    def __init__(self):
        super().__init__(title="HWN Tools")
        state = load_state()
        w = min(state.get("main_width", 300), 1024)
        h = min(state.get("main_height", 400), 800)
        self.set_default_size(w, h)
        if "main_x" in state and "main_y" in state:
            self.move(state["main_x"], state["main_y"])
        self.set_icon_name("application-x-shellscript")
        self.current_path = ROOT_DIR
        self.effective_root = ROOT_DIR
        self.history = []
        self.buttons = []
        self.focused_index = 0
        self.search_query = ""
        self.search_mode = False
        self.tree_mode = state.get("tree_mode", False)
        self.last_esc_time = 0
        self.ready = False

        def on_shown(widget):
            self.ready = True
        self.connect("show", on_shown)
        self.connect("configure-event", self.on_configure)

        style_provider = Gtk.CssProvider()
        style_provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), style_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.connect("key-press-event", self.on_key)

        header_bar = Gtk.HeaderBar()
        header_bar.set_show_close_button(True)
        header_bar.set_title("HWN Tools")

        self.updates_available = {}
        menu_btn = Gtk.MenuButton()
        menu_btn.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", ICON_SIZE))
        popover = Gtk.Popover()
        menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        menu_box.set_margin_top(4)
        menu_box.set_margin_bottom(4)
        for label, icon_name, handler in [
            ("Manage Script Sources", "folder-open-symbolic", self.on_manage_sources),
            ("Help", "help-browser-symbolic", self.on_help),
        ]:
            item = Gtk.Button()
            item.set_relief(Gtk.ReliefStyle.NONE)
            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            hbox.pack_start(Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU), False, False, 0)
            hbox.pack_start(Gtk.Label(label=label, xalign=0), True, True, 0)
            item.add(hbox)
            item.connect("clicked", lambda b, h=handler: (popover.popdown(), h(b)))
            menu_box.pack_start(item, False, False, 0)
        menu_box.show_all()
        popover.add(menu_box)
        menu_btn.set_popover(popover)

        self.menu_btn = menu_btn
        self._show_update_dot = False
        menu_btn.connect_after("draw", self._draw_update_dot)
        header_bar.pack_start(menu_btn)

        self.set_titlebar(header_bar)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(vbox)

        self.nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.nav_bar.set_margin_top(6)
        self.nav_bar.set_margin_start(10)
        self.nav_bar.set_margin_end(10)
        self.nav_bar.set_margin_bottom(2)

        self.back_btn = Gtk.Button()
        back_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        back_hbox.pack_start(Gtk.Image.new_from_icon_name("go-previous", ICON_SIZE), False, False, 0)
        back_hbox.pack_start(Gtk.Label(label="Back"), False, False, 0)
        self.back_btn.add(back_hbox)
        self.back_btn.connect("clicked", self.on_back)
        self.back_btn.set_tooltip_text("Backspace")
        self.nav_bar.pack_start(self.back_btn, False, False, 0)

        self.top_btn = Gtk.Button()
        top_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        top_hbox.pack_start(Gtk.Image.new_from_icon_name("go-top", ICON_SIZE), False, False, 0)
        top_hbox.pack_start(Gtk.Label(label="Top"), False, False, 0)
        self.top_btn.add(top_hbox)
        self.top_btn.connect("clicked", self.on_top)
        self.top_btn.set_tooltip_text("Home")
        self.nav_bar.pack_start(self.top_btn, False, False, 0)

        self.path_label = Gtk.Label()
        self.path_label.set_xalign(1)
        self.path_label.set_ellipsize(Pango.EllipsizeMode.START)
        self.path_label.get_style_context().add_class("dim-label")
        self.nav_bar.pack_end(self.path_label, True, True, 4)

        vbox.pack_start(self.nav_bar, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep, False, False, 0)

        self.scrolled = Gtk.ScrolledWindow()
        self.scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        vbox.pack_start(self.scrolled, True, True, 0)

        if self.tree_mode:
            self.populate_tree()
        else:
            self.populate(ROOT_DIR)

        self._check_for_updates()

    def _draw_update_dot(self, widget, cr):
        if not self._show_update_dot:
            return False
        alloc = widget.get_allocation()
        cr.set_source_rgb(0.8, 0.1, 0.1)
        cr.arc(alloc.width - 13, 18, 4, 0, 2 * 3.14159)
        cr.fill()
        return False

    def _check_for_updates(self):
        repos = load_state().get("package_repos", [])
        if not repos:
            return

        def worker():
            results = {}
            for repo in repos:
                url = repo.get("url", "")
                if not url:
                    continue
                try:
                    has_updates, updatable, error = _check_repo_updates(url, repo.get("path", ""))
                    if updatable:
                        results[url] = updatable
                except Exception:
                    continue
            GLib.idle_add(self._on_update_check_done, results)

        threading.Thread(target=worker, daemon=True).start()

    def _on_update_check_done(self, results):
        self.updates_available = results
        total = sum(len(v) for v in results.values())
        if total > 0:
            self._show_update_dot = True
            self.menu_btn.set_tooltip_text(
                f"{total} package update{'s' if total != 1 else ''} available"
            )
        else:
            self._show_update_dot = False
            self.menu_btn.set_tooltip_text(None)
        self.menu_btn.queue_draw()

    def update_nav_bar(self):
        if self.tree_mode or self.search_mode:
            self.back_btn.hide()
            self.top_btn.hide()
            if self.search_mode:
                self.path_label.set_text(self.search_query)
            else:
                self.path_label.set_markup('<span foreground="#888">type to search</span>')
        else:
            self.back_btn.show()
            self.top_btn.show()
            at_effective_root = os.path.realpath(self.current_path) == os.path.realpath(self.effective_root)
            self.back_btn.set_sensitive(not at_effective_root)
            self.top_btn.set_sensitive(not at_effective_root)
            rel = self._virtual_relpath(self.current_path)
            self.path_label.set_text("/" if rel == "." else "/" + rel)

    def _scan_directory(self, directory):
        folders = []
        scripts = []
        at_root = os.path.realpath(directory) == os.path.realpath(ROOT_DIR)
        if at_root:
            pkg_root = os.path.normpath(os.path.realpath(PACKAGES_DIR))
            for src in load_state().get("script_sources", []):
                if isinstance(src, str):
                    src_path, src_label = src, ""
                else:
                    src_path, src_label = src["path"], src.get("label", "")
                real_src = os.path.normpath(os.path.realpath(src_path))
                cyclic = (pkg_root == real_src
                          or pkg_root.startswith(real_src + os.sep)
                          or real_src.startswith(pkg_root + os.sep))
                name = os.path.basename(src_path)
                missing = cyclic or not os.path.isdir(src_path)
                if not missing:
                    config_file = os.path.join(src_path, ".config")
                    config = parse_config(config_file) if os.path.isfile(config_file) else {}
                else:
                    config = {}
                sort_label = (src_label or config.get("label", label_from_filename(name))).lower()
                folders.append((name, src_path, src_label or None, missing, config.get("order"), sort_label))

            if os.path.isdir(PACKAGES_DIR):
                existing_paths = {os.path.normpath(os.path.realpath(f[1])) for f in folders}
                for pkg_name in os.listdir(PACKAGES_DIR):
                    pkg_path = os.path.join(PACKAGES_DIR, pkg_name)
                    if not os.path.isdir(pkg_path) or pkg_name.startswith(".") or pkg_name.startswith("_"):
                        continue
                    real_pkg = os.path.normpath(os.path.realpath(pkg_path))
                    if real_pkg in existing_paths:
                        continue
                    config_file = os.path.join(pkg_path, ".config")
                    config = parse_config(config_file) if os.path.isfile(config_file) else {}
                    pkg_label = config.get("label", label_from_filename(pkg_name))
                    sort_label = pkg_label.lower()
                    folders.append((pkg_name, pkg_path, None, False, config.get("order"), sort_label))
        else:
            try:
                entries = os.listdir(directory)
            except OSError:
                entries = []
            for name in entries:
                path = os.path.join(directory, name)
                if name.startswith(".") or name.startswith("_"):
                    continue
                if os.path.isdir(path):
                    config_file = os.path.join(path, ".config")
                    config = parse_config(config_file) if os.path.isfile(config_file) else {}
                    target = config.get("target")
                    if target and target != PLATFORM:
                        continue
                    sort_label = config.get("label", label_from_filename(name)).lower()
                    folders.append((name, path, None, False, config.get("order"), sort_label))
                elif os.path.isfile(path) and name.endswith((".sh", ".py")):
                    config = parse_config(path)
                    target = config.get("target")
                    if target and target != PLATFORM:
                        continue
                    sort_label = config.get("label", label_from_filename(name)).lower()
                    scripts.append((name, path, config.get("order"), sort_label))

        def _sort_key(item):
            order, sort_label = item[-2], item[-1]
            if order is not None:
                return (0, order, sort_label)
            return (1, "", sort_label)

        folders.sort(key=_sort_key)
        scripts.sort(key=_sort_key)
        folders = [(name, path, ovr, miss) for name, path, ovr, miss, _o, _s in folders]
        scripts = [(name, path) for name, path, _o, _s in scripts]
        return folders, scripts

    def _source_display_name(self, src_path, src_entry):
        if isinstance(src_entry, dict) and src_entry.get("label"):
            return src_entry["label"]
        config_file = os.path.join(src_path, ".config")
        if os.path.isfile(config_file):
            config = parse_config(config_file)
            if "label" in config:
                return config["label"]
        return label_from_filename(os.path.basename(src_path))

    def _virtual_relpath(self, path):
        real = os.path.realpath(path)
        for src in load_state().get("script_sources", []):
            s = src if isinstance(src, str) else src["path"]
            sr = os.path.realpath(s)
            if real == sr or real.startswith(sr + os.sep):
                base = self._source_display_name(s, src)
                if real == sr:
                    return base
                return os.path.join(base, os.path.relpath(path, s))
        pkg_root = os.path.realpath(PACKAGES_DIR)
        if real.startswith(pkg_root + os.sep):
            rel = os.path.relpath(path, PACKAGES_DIR)
            parts = rel.split(os.sep)
            pkg_path = os.path.join(PACKAGES_DIR, parts[0])
            config_file = os.path.join(pkg_path, ".config")
            config = parse_config(config_file) if os.path.isfile(config_file) else {}
            base = config.get("label", label_from_filename(parts[0]))
            if len(parts) == 1:
                return base
            return os.path.join(base, *parts[1:])
        return os.path.basename(path)

    def populate(self, directory):
        self.current_path = directory
        self.buttons = []
        self.focused_index = 0

        child = self.scrolled.get_child()
        if child:
            self.scrolled.remove(child)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        folders, scripts = self._scan_directory(directory)

        at_root = os.path.realpath(directory) == os.path.realpath(ROOT_DIR)
        if at_root and len(folders) == 1 and not scripts and not folders[0][3]:
            self.effective_root = folders[0][1]
            self.populate(folders[0][1])
            return
        if at_root:
            self.effective_root = ROOT_DIR

        for name, path in scripts:
            if not os.access(path, os.X_OK):
                os.chmod(path, os.stat(path).st_mode | 0o755)

        for name, path, override_label, missing in folders:
            config_file = os.path.join(path, ".config")
            config = parse_config(config_file) if not missing and os.path.isfile(config_file) else {}
            label = override_label or config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_FOLDER_ICON)
            btn = self.make_button(icon, label, missing=missing)
            btn.get_style_context().add_class("folder-btn")
            if missing:
                btn.set_tooltip_text(f"Path not found: {path}")
                btn.connect("clicked", lambda *a: None)
            else:
                btn.connect("clicked", self.on_folder_click, path)
            box.pack_start(btn, False, False, 0)
            self.buttons.append(btn)

        for name, path in scripts:
            config = parse_config(path)
            label = config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_SCRIPT_ICON)
            btn = self.make_button(icon, label)
            btn.connect("clicked", self.on_script_click, path)
            box.pack_start(btn, False, False, 0)
            self.buttons.append(btn)

        if not folders and not scripts:
            at_root = os.path.realpath(directory) == os.path.realpath(ROOT_DIR)
            if at_root:
                empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                empty_box.set_valign(Gtk.Align.CENTER)
                empty_box.set_halign(Gtk.Align.CENTER)
                lbl = Gtk.Label(label="No script sources configured.\nAdd folders or packages via the \u2630 menu top left.")
                lbl.set_justify(Gtk.Justification.CENTER)
                empty_box.pack_start(lbl, False, False, 0)
                open_btn = Gtk.Button(label="Open Manager")
                open_btn.set_halign(Gtk.Align.CENTER)
                open_btn.connect("clicked", self.on_manage_sources)
                empty_box.pack_start(open_btn, False, False, 0)
                box.pack_start(empty_box, True, True, 20)
            else:
                empty = Gtk.Label(label="No scripts found")
                box.pack_start(empty, True, True, 20)

        self.scrolled.add(box)
        self.show_all()
        self.update_nav_bar()

        if self.buttons:
            self.buttons[0].grab_focus()

    def make_button(self, icon_name, label, is_back=False, missing=False):
        btn = Gtk.Button()
        btn.get_style_context().add_class("tool-btn")
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        image = Gtk.Image.new_from_icon_name(icon_name, ICON_SIZE)
        lbl = Gtk.Label()
        lbl.set_xalign(0)
        if missing:
            lbl.set_markup(f'<span color="#cc3333">{GLib.markup_escape_text(label)}</span>')
        else:
            lbl.set_text(label)
        hbox.pack_start(image, False, False, 0)
        hbox.pack_start(lbl, True, True, 0)
        btn.add(hbox)
        return btn

    def collect_all_entries(self, directory=None, _visited=None, _at_root=None):
        if directory is None:
            directory = ROOT_DIR
        if _at_root is None:
            _at_root = os.path.realpath(directory) == os.path.realpath(ROOT_DIR)
        if _visited is None:
            _visited = set()
        real = os.path.realpath(directory)
        if real in _visited:
            return []
        _visited.add(real)
        results = []
        folders, scripts = self._scan_directory(directory)
        for name, path, override_label, missing in folders:
            config_file = os.path.join(path, ".config")
            config = parse_config(config_file) if not missing and os.path.isfile(config_file) else {}
            label = override_label or config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_FOLDER_ICON)
            search = config.get("search")
            rel_dir = "." if _at_root else self._virtual_relpath(os.path.dirname(path)) if not missing else "."
            rel_path = self._virtual_relpath(path) if not missing else name
            results.append((label, icon, path, rel_dir, rel_path, True, missing, search))
            if not missing:
                results.extend(self.collect_all_entries(path, _visited, _at_root=False))
        for name, path in scripts:
            config = parse_config(path)
            label = config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_SCRIPT_ICON)
            search = config.get("search")
            rel_dir = "." if _at_root else self._virtual_relpath(os.path.dirname(path))
            rel_path = self._virtual_relpath(path)
            results.append((label, icon, path, rel_dir, rel_path, False, False, search))
        return results

    def populate_search(self):
        self.buttons = []
        self.focused_index = 0

        child = self.scrolled.get_child()
        if child:
            self.scrolled.remove(child)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        all_entries = self.collect_all_entries()
        matches = []
        for label, icon, path, rel_dir, rel_path, is_folder, missing, search in all_entries:
            match_text = search if search else label
            if subsequence_match(self.search_query, match_text) or subsequence_match(self.search_query, os.path.basename(rel_path)):
                matches.append((label, icon, path, rel_dir, is_folder, missing))

        groups = {}
        group_order = []
        for label, icon, path, rel_dir, is_folder, missing in matches:
            if rel_dir not in groups:
                groups[rel_dir] = []
                group_order.append(rel_dir)
            groups[rel_dir].append((label, icon, path, is_folder, missing))

        for rel_dir in group_order:
            items = groups[rel_dir]
            display_path = "/" if rel_dir == "." else "/" + rel_dir
            header = Gtk.Label(label=display_path)
            header.set_xalign(0)
            header.get_style_context().add_class("dim-label")
            header.set_margin_start(4)
            header.set_margin_top(4 if not self.buttons else 8)
            box.pack_start(header, False, False, 0)

            for label, icon, path, is_folder, missing in items:
                btn = self.make_button(icon, label, missing=missing)
                if is_folder:
                    btn.get_style_context().add_class("folder-btn")
                    if not missing:
                        btn.connect("clicked", self.on_search_folder_click, path)
                    else:
                        btn.connect("clicked", lambda *a: None)
                else:
                    btn.connect("clicked", self.on_script_click, path)
                box.pack_start(btn, False, False, 0)
                self.buttons.append(btn)

        if not matches:
            empty = Gtk.Label(label="No matches")
            box.pack_start(empty, True, True, 20)

        self.scrolled.add(box)
        self.show_all()
        self.update_nav_bar()

        if self.buttons:
            self.buttons[0].grab_focus()

    def collect_tree_entries(self, directory=None, ancestor_last=None, _visited=None):
        if directory is None:
            directory = ROOT_DIR
        if ancestor_last is None:
            ancestor_last = []
        if _visited is None:
            _visited = set()
        real = os.path.realpath(directory)
        if real in _visited:
            return []
        _visited.add(real)
        results = []
        folders, scripts = self._scan_directory(directory)
        items = []
        for name, path, override_label, missing in folders:
            config_file = os.path.join(path, ".config")
            config = parse_config(config_file) if not missing and os.path.isfile(config_file) else {}
            label = override_label or config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_FOLDER_ICON)
            search = config.get("search")
            rel_path = self._virtual_relpath(path) if not missing else name
            items.append((label, icon, path, rel_path, True, missing, search))
        for name, path in scripts:
            config = parse_config(path)
            label = config.get("label", label_from_filename(name))
            icon = config.get("icon", DEFAULT_SCRIPT_ICON)
            search = config.get("search")
            rel_path = self._virtual_relpath(path)
            items.append((label, icon, path, rel_path, False, False, search))
        for i, (label, icon, path, rel_path, is_folder, missing, search) in enumerate(items):
            is_last = (i == len(items) - 1)
            prefix = ""
            for a in ancestor_last:
                prefix += "    " if a else "\u2502   "
            prefix += "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
            results.append((label, icon, path, rel_path, is_folder, prefix, missing, search))
            if is_folder and not missing:
                results.extend(self.collect_tree_entries(path, ancestor_last + [is_last], _visited))
        return results

    def make_tree_row(self, icon_name, label, prefix, is_folder, missing=False):
        ebox = Gtk.EventBox()
        if not is_folder and not missing:
            ebox.set_can_focus(True)
            ebox.get_style_context().add_class("tree-row")
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_margin_top(2)
        hbox.set_margin_bottom(2)
        if prefix:
            prefix_lbl = Gtk.Label(label=prefix)
            prefix_lbl.override_font(Pango.FontDescription("monospace"))
            prefix_lbl.get_style_context().add_class("dim-label")
            hbox.pack_start(prefix_lbl, False, False, 0)
        image = Gtk.Image.new_from_icon_name(icon_name, ICON_SIZE)
        hbox.pack_start(image, False, False, 4)
        lbl = Gtk.Label()
        lbl.set_xalign(0)
        if missing:
            lbl.set_markup(f'<span color="#cc3333"><b>{GLib.markup_escape_text(label)}</b></span>')
        elif is_folder:
            lbl.set_markup(f"<b>{GLib.markup_escape_text(label)}</b>")
        else:
            lbl.set_text(label)
        hbox.pack_start(lbl, True, True, 0)
        ebox.add(hbox)
        return ebox

    def populate_tree(self):
        self.buttons = []
        self.focused_index = 0

        child = self.scrolled.get_child()
        if child:
            self.scrolled.remove(child)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(10)
        box.set_margin_end(10)

        all_entries = self.collect_tree_entries()

        if self.search_query:
            all_entries = [
                e for e in all_entries
                if subsequence_match(self.search_query, e[7] if e[7] else e[0])
                or subsequence_match(self.search_query, os.path.basename(e[3]))
            ]

        for label, icon, path, rel_path, is_folder, prefix, missing, _search in all_entries:
            row = self.make_tree_row(icon, label, prefix, is_folder, missing=missing)
            if not is_folder and not missing:
                row._activate = lambda p=path: self.on_script_click(None, p)
                row.connect("button-press-event",
                            lambda w, e, p=path: self.on_script_click(w, p))
                self.buttons.append(row)
            box.pack_start(row, False, False, 0)

        if not all_entries:
            if self.search_query:
                empty = Gtk.Label(label="No matches")
                box.pack_start(empty, True, True, 20)
            else:
                empty_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
                empty_box.set_valign(Gtk.Align.CENTER)
                empty_box.set_halign(Gtk.Align.CENTER)
                lbl = Gtk.Label(label="No script sources configured.\nAdd folders or packages via the \u2630 menu top left.")
                lbl.set_justify(Gtk.Justification.CENTER)
                empty_box.pack_start(lbl, False, False, 0)
                open_btn = Gtk.Button(label="Open Manager")
                open_btn.set_halign(Gtk.Align.CENTER)
                open_btn.connect("clicked", self.on_manage_sources)
                empty_box.pack_start(open_btn, False, False, 0)
                box.pack_start(empty_box, True, True, 20)

        self.scrolled.add(box)
        self.show_all()
        self.update_nav_bar()

        if self.buttons:
            self.buttons[0].grab_focus()

    def exit_search(self):
        self.search_mode = False
        self.search_query = ""
        if self.tree_mode:
            self.populate_tree()
        else:
            self.populate(self.current_path)

    def on_configure(self, widget, event):
        if not self.ready:
            return
        state = load_state()
        w, h = self.get_size()
        state["main_width"] = w
        state["main_height"] = h
        state["main_x"] = event.x
        state["main_y"] = event.y
        save_state(state)

    def on_search_folder_click(self, button, path):
        self.search_mode = False
        self.search_query = ""
        self.history.clear()
        self.history.append(ROOT_DIR)
        real = os.path.realpath(path)
        source_root = None
        for src in load_state().get("script_sources", []):
            s = src if isinstance(src, str) else src["path"]
            sr = os.path.realpath(s)
            if real == sr or real.startswith(sr + os.sep):
                source_root = s
                break
        if source_root is None and os.path.isdir(PACKAGES_DIR):
            pkg_root = os.path.realpath(PACKAGES_DIR)
            if real.startswith(pkg_root + os.sep):
                rel = os.path.relpath(path, PACKAGES_DIR)
                source_root = os.path.join(PACKAGES_DIR, rel.split(os.sep)[0])
        if source_root and real != os.path.realpath(source_root):
            self.history.append(source_root)
            rel = os.path.relpath(path, source_root)
            parts = rel.split(os.sep)
            for i in range(1, len(parts)):
                self.history.append(os.path.join(source_root, *parts[:i]))
        self.populate(path)

    def on_folder_click(self, button, path):
        self.history.append(self.current_path)
        self.populate(path)

    def on_back(self, *args):
        if self.history:
            prev = self.history.pop()
            self.populate(prev)

    def on_top(self, *args):
        if os.path.realpath(self.current_path) != os.path.realpath(self.effective_root):
            self.history.clear()
            self.populate(self.effective_root)

    def on_key(self, widget, event):
        key = event.keyval

        if key == Gdk.KEY_Escape:
            now = GLib.get_monotonic_time() / 1_000_000
            if self.search_mode:
                self.exit_search()
                self.last_esc_time = now
                return True
            if now - self.last_esc_time < 0.25:
                self.destroy()
                return True
            self.last_esc_time = now
            return False

        if key == Gdk.KEY_t and event.state & Gdk.ModifierType.CONTROL_MASK:
            self.search_mode = False
            self.search_query = ""
            self.tree_mode = not self.tree_mode
            s = load_state()
            s["tree_mode"] = self.tree_mode
            save_state(s)
            if self.tree_mode:
                self.populate_tree()
            else:
                self.populate(self.current_path)
            return True

        if key == Gdk.KEY_F1:
            self.on_help()
            return True

        if key == Gdk.KEY_F5:
            if self.search_mode:
                self.exit_search()
            self.populate(self.current_path)
            return True

        if key == Gdk.KEY_BackSpace:
            if self.search_mode:
                if self.search_query:
                    self.search_query = self.search_query[:-1]
                    if self.search_query:
                        if self.tree_mode:
                            self.populate_tree()
                        else:
                            self.populate_search()
                    else:
                        self.exit_search()
                return True
            if not self.tree_mode:
                self.on_back()
            return True

        if key == Gdk.KEY_Home:
            if self.search_mode:
                self.exit_search()
            self.on_top()
            return True

        if key in (Gdk.KEY_Up, Gdk.KEY_Down) and self.buttons:
            if key == Gdk.KEY_Up:
                self.focused_index = max(0, self.focused_index - 1)
            else:
                self.focused_index = min(len(self.buttons) - 1, self.focused_index + 1)
            self.buttons[self.focused_index].grab_focus()
            return True

        if key in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and self.buttons:
            widget = self.buttons[self.focused_index]
            if hasattr(widget, '_activate'):
                widget._activate()
            else:
                widget.clicked()
            return True

        char = chr(key) if 32 <= key <= 126 else None
        if char is None and key >= 0xfe00:
            char = event.string if event.string and event.string.isprintable() else None
        if char:
            self.search_mode = True
            self.search_query += char
            if self.tree_mode:
                self.populate_tree()
            else:
                self.populate_search()
            return True

        return False

    def on_manage_sources(self, *args):
        SourcesManager(self)

    def refresh_view(self):
        if self.search_mode:
            if self.tree_mode:
                self.populate_tree()
            else:
                self.populate_search()
        elif self.tree_mode:
            self.populate_tree()
        else:
            self.populate(self.current_path)

    def on_help(self, *args):
        shortcuts = [
            ("Up / Down", "Navigate between buttons"),
            ("Enter", "Activate focused button"),
            ("Backspace", "Go back one level"),
            ("Home", "Jump to root"),
            ("F5", "Refresh current folder"),
            ("Type anything", "Search scripts and folders"),
            ("Esc", "Clear search"),
            ("Esc \u00d7 2", "Close app (within 250ms)"),
            ("Ctrl+T", "Toggle tree / button view"),
            ("F1", "Show this help"),
        ]
        dialog = Gtk.Dialog(title="Keyboard Shortcuts", transient_for=self, modal=True)
        dialog.set_default_size(320, -1)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_margin_top(12)
        content.set_margin_bottom(8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        grid = Gtk.Grid(column_spacing=16, row_spacing=6)
        for i, (key, desc) in enumerate(shortcuts):
            key_label = Gtk.Label(label=key)
            key_label.set_xalign(0)
            key_label.override_font(Pango.FontDescription("bold"))
            desc_label = Gtk.Label(label=desc)
            desc_label.set_xalign(0)
            grid.attach(key_label, 0, i, 1, 1)
            grid.attach(desc_label, 1, i, 1, 1)
        content.add(grid)
        dialog.show_all()
        dialog.run()
        dialog.destroy()

    def on_script_click(self, button, script_path):
        config = parse_config(script_path)
        deps = config.get("deps", [])
        if deps:
            failures = check_dependencies(deps)
            if failures:
                self.show_dep_error(script_path, failures)
                return
        if "detach" in config or "standalone" in config:
            subprocess.Popen(
                [script_path],
                cwd=os.path.dirname(script_path),
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if "standalone" in config:
                self.destroy()
            return
        title = config.get("label", label_from_filename(os.path.basename(script_path)))
        OutputDialog(self, title, script_path)

    def show_dep_error(self, script_path, failures):
        dialog = DepDialog(self, script_path, failures)
        dialog.show_all()
