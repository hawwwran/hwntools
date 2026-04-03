import os
import subprocess

from .config import parse_config, label_from_filename
from .constants import PACKAGES_DIR, REPOS_DIR


def _version_tuple(v):
    """Parse 'X.Y.Z' into a tuple of ints."""
    try:
        return tuple(int(x) for x in v.split("."))
    except (ValueError, AttributeError):
        return (0, 0, 0)


def _version_newer(remote, local):
    """Return True if remote version is strictly higher than local."""
    return _version_tuple(remote) > _version_tuple(local)


def _scan_installed_packages():
    """Scan packages/ for installed packages. Returns {name: {version, path}}."""
    installed = {}
    if not os.path.isdir(PACKAGES_DIR):
        return installed
    for entry in os.listdir(PACKAGES_DIR):
        path = os.path.join(PACKAGES_DIR, entry)
        if not os.path.isdir(path) or entry.startswith(".") or entry.startswith("_"):
            continue
        config_file = os.path.join(path, ".config")
        if os.path.isfile(config_file):
            config = parse_config(config_file)
            pkg_name = config.get("package")
            if pkg_name:
                installed[pkg_name] = {
                    "version": config.get("version", "0.0.0"),
                    "path": path,
                }
    return installed


def _repo_dir_from_url(url):
    """Extract owner/repo from a git URL and return the local clone path under REPOS_DIR.
    Handles https://host/owner/repo.git and git@host:owner/repo.git formats."""
    clean = url.strip().rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]
    if ":" in clean and clean.startswith("git@"):
        clean = clean.split(":", 1)[1]
    else:
        from urllib.parse import urlparse
        parsed = urlparse(clean)
        clean = parsed.path.strip("/")
    parts = clean.split("/")
    if len(parts) >= 2:
        owner, repo = parts[-2], parts[-1]
    else:
        owner, repo = "unknown", parts[-1] if parts else "repo"
    return os.path.join(REPOS_DIR, owner, repo)


def _git_run(args, cwd=None, timeout=60):
    """Run a git command and return (success, stdout, stderr)."""
    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        return False, "", "git command timed out"
    except FileNotFoundError:
        return False, "", "git is not installed"


def _ensure_repo(url, path=""):
    """Clone or pull a git repo. Returns (repo_dir, pkg_base, error).
    pkg_base is the directory to scan for packages (repo_dir/path or repo_dir)."""
    repo_dir = _repo_dir_from_url(url)
    git_dir = os.path.join(repo_dir, ".git")

    if os.path.isdir(git_dir):
        ok, out, err = _git_run(["pull", "--ff-only"], cwd=repo_dir, timeout=30)
        if not ok and "already up to date" not in err.lower():
            return repo_dir, None, f"git pull failed: {err}"
    else:
        os.makedirs(os.path.dirname(repo_dir), exist_ok=True)
        if path:
            ok, out, err = _git_run(
                ["clone", "--depth", "1", "--filter=blob:none", "--sparse", url, repo_dir],
                timeout=60
            )
            if not ok:
                return repo_dir, None, f"git clone failed: {err}"
            ok, out, err = _git_run(["sparse-checkout", "set", path], cwd=repo_dir)
            if not ok:
                return repo_dir, None, f"sparse-checkout failed: {err}"
        else:
            ok, out, err = _git_run(["clone", "--depth", "1", url, repo_dir], timeout=60)
            if not ok:
                return repo_dir, None, f"git clone failed: {err}"

    pkg_base = os.path.join(repo_dir, path) if path else repo_dir
    if not os.path.isdir(pkg_base):
        return repo_dir, None, f"path '{path}' not found in repository"
    return repo_dir, pkg_base, None


def _scan_repo_packages(pkg_base):
    """Scan a cloned repo path for packages. Returns list of {name, version, label, path, scripts}."""
    packages = []
    if not pkg_base or not os.path.isdir(pkg_base):
        return packages
    for entry in sorted(os.listdir(pkg_base)):
        pkg_path = os.path.join(pkg_base, entry)
        if not os.path.isdir(pkg_path) or entry.startswith(".") or entry.startswith("_"):
            continue
        config_file = os.path.join(pkg_path, ".config")
        if not os.path.isfile(config_file):
            continue
        config = parse_config(config_file)
        pkg_name = config.get("package")
        if not pkg_name:
            continue
        scripts = []
        for root, _dirs, files in os.walk(pkg_path):
            for f in sorted(files):
                if f.endswith((".sh", ".py")) and not f.startswith("_"):
                    fpath = os.path.join(root, f)
                    sc = parse_config(fpath)
                    scripts.append({
                        "path": os.path.relpath(fpath, pkg_path),
                        "version": sc.get("version", "?"),
                        "label": sc.get("label", label_from_filename(os.path.splitext(f)[0])),
                        "description": sc.get("description", ""),
                        "target": sc.get("target", ""),
                    })
        packages.append({
            "name": pkg_name,
            "version": config.get("version", "0.0.0"),
            "label": config.get("label", label_from_filename(entry)),
            "path": pkg_path,
            "folder": entry,
            "scripts": scripts,
        })
    return packages


def _check_repo_updates(url, path=""):
    """Pull a repo and check if the path has changes. Returns (has_updates, updatable_packages, error).
    updatable_packages is a list of package names with newer versions."""
    repo_dir = _repo_dir_from_url(url)
    git_dir = os.path.join(repo_dir, ".git")

    if not os.path.isdir(git_dir):
        return False, [], None

    ok, old_head, _ = _git_run(["rev-parse", "HEAD"], cwd=repo_dir)
    if not ok:
        return False, [], "could not read HEAD"

    _git_run(["pull", "--ff-only"], cwd=repo_dir, timeout=30)

    ok, new_head, _ = _git_run(["rev-parse", "HEAD"], cwd=repo_dir)
    if not ok:
        return False, [], "could not read HEAD after pull"

    pkg_base = os.path.join(repo_dir, path) if path else repo_dir
    remote_packages = _scan_repo_packages(pkg_base)
    installed = _scan_installed_packages()
    updatable = []
    for rpkg in remote_packages:
        name = rpkg["name"]
        if name in installed:
            if _version_newer(rpkg["version"], installed[name]["version"]):
                updatable.append(name)
    return len(updatable) > 0, updatable, None
