# HWN Tools

GTK script launcher with built-in terminal. Organize and run shell and Python scripts from a clean button interface.

## Features

- Scripts become clickable buttons with icons and labels
- Built-in VTE terminal with full interactive support, ANSI colors
- Two views: button grid and tree view (toggle with Ctrl+T)
- Fuzzy search — just start typing
- Script sources from local folders or package servers
- Package manager for installing and updating script packages
- Works on Linux natively and on Windows via WSLg
- Keyboard-driven — navigate, search, and launch without a mouse

## Installation

### Linux

```bash
python3 app/hwntools.py
```

Dependencies (`python3`, `gir1.2-gtk-3.0`, `gir1.2-vte-2.91`) are auto-detected — if missing, the app prompts to install them.

### Windows (WSLg)

Run `app/windows-setup.bat` to set up WSL dependencies and create a keyboard shortcut. Then launch via `hwntools.cmd` or `Ctrl+Shift+~`.

## Adding Scripts

1. Add a folder via the menu > **Manage Script Sources**, or install a package
2. Place `.sh` or `.py` files in the folder
3. Press F5 to refresh

### Script Config

Optional comment block after the shebang:

```bash
#!/bin/bash
# @label: My Tool
# @icon: utilities-terminal
# @dep: jq
```

| Field | Description |
|-------|-------------|
| `@label` | Button text (default: filename, title-cased) |
| `@description` | Short description of what the script does |
| `@icon` | GTK icon name (default: `text-x-script`) |
| `@order` | Custom sort priority |
| `@dep` | Dependency checked before running |
| `@detach` | Run as detached process (no terminal) |
| `@standalone` | Detach and close HWN Tools |
| `@target` | Platform filter: `windows` or `linux` |
| `@search` | Alternative search text |

Folders support a `.config` file with the same format for `@label`, `@icon`, `@order`.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| Up / Down | Navigate |
| Enter | Activate |
| Backspace | Go back / delete search char |
| Home | Jump to root |
| F5 | Refresh |
| Type anything | Search |
| Esc | Clear search / close output |
| Esc x 2 | Close app |
| Ctrl+T | Toggle button / tree view |
| F1 | Help |

## Repository Structure

```
hwntools/
├── app/          ← the launcher application
└── packages/     ← published script packages
```

## License

[GPL-3.0](LICENSE)
