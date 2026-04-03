import os

from gi.repository import Gtk

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = APP_DIR  # Virtual root sentinel — not scanned for scripts
PACKAGES_DIR = os.path.join(APP_DIR, "packages")
REPOS_DIR = os.path.join(APP_DIR, ".packages")
STATE_FILE = os.path.join(APP_DIR, ".state.json")
PLATFORM = "windows" if os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or os.environ.get("WSL_DISTRO_NAME") else "linux"
ICON_SIZE = Gtk.IconSize.BUTTON
DEFAULT_SCRIPT_ICON = "text-x-script"
DEFAULT_FOLDER_ICON = "folder"

CSS = b"""
button.tool-btn:focus {
    border: 2px solid @theme_selected_bg_color;
    background-image: linear-gradient(alpha(@theme_selected_bg_color, 0.25), alpha(@theme_selected_bg_color, 0.25));
    box-shadow: 0 0 4px alpha(@theme_selected_bg_color, 0.5);
}
button.tool-btn:focus label {
    font-weight: bold;
}
button.folder-btn label {
    font-weight: bold;
}
.tree-row:focus {
    border-radius: 4px;
    background-image: linear-gradient(alpha(@theme_selected_bg_color, 0.25), alpha(@theme_selected_bg_color, 0.25));
}
.terminal-header {
    color: #888;
    font-size: 0.9em;
}
"""
