import hashlib
import os
import shutil
import subprocess

from .config import parse_config, label_from_filename
from .constants import PACKAGES_DIR, REPOS_DIR, VERSION


def _is_auth_error(friendly_msg):
    """Check if a friendly error message indicates an authentication problem."""
    if not friendly_msg:
        return False
    return (friendly_msg.startswith("Authentication failed")
            or friendly_msg.startswith("Permission denied"))


def _ensure_credential_helper():
    """If no global credential.helper is configured, set it to 'store' so that
    credentials entered in the interactive auth terminal persist for later
    background operations (which run with GIT_TERMINAL_PROMPT=0)."""
    ok, out, _ = _git_run(["config", "--global", "credential.helper"], timeout=5)
    if ok and out:
        return
    _git_run(["config", "--global", "credential.helper", "store"], timeout=5)


def _friendly_git_error(stderr):
    """Translate raw git stderr into a short user-facing message."""
    low = stderr.lower()
    if "repository not found" in low or "does not exist" in low:
        return "Repository not found — check the URL"
    if ("authentication failed" in low
            or "could not read username" in low
            or "logon failed" in low
            or "invalid credentials" in low):
        return "Authentication failed — check your credentials or repository access"
    if "permission denied" in low:
        return "Permission denied — SSH key not configured or not accepted"
    if "could not resolve host" in low:
        return "Could not resolve host — check the URL and your network connection"
    if "timed out" in low or "connection timed out" in low:
        return "Connection timed out"
    if "not a git repository" in low:
        return "Not a valid git repository"
    if ("unable to access" in low
            or "failed to connect" in low
            or "couldn't connect" in low
            or "connection refused" in low):
        return "Unable to connect to the repository"
    if "ssl" in low or "certificate" in low:
        return "SSL/TLS error — connection to the server is not trusted"
    return "Unable to connect to the repository"


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


def _domain_label(hostname):
    """Extract a short recognizable label from a hostname.
    github.com -> github, gitlab.simpleway.global -> simpleway."""
    parts = hostname.lower().split(".")
    while len(parts) > 1 and parts[0] in ("www", "git", "gitlab", "gitea"):
        parts.pop(0)
    while len(parts) > 1 and parts[-1] in (
        "com", "org", "net", "io", "dev", "global", "eu", "co", "uk",
    ):
        parts.pop()
    return parts[0] if parts else hostname.split(".")[0]


def _repo_dir_from_url(url, path=""):
    """Derive a flat, unique directory name for a git repo clone under REPOS_DIR.
    Format: domain-first-last[-pathlast]-XXXXX (5-char MD5 suffix for uniqueness)."""
    clean = url.strip().rstrip("/")
    if clean.endswith(".git"):
        clean = clean[:-4]

    if ":" in clean and clean.startswith("git@"):
        host = clean.split("@", 1)[1].split(":", 1)[0]
        url_path = clean.split(":", 1)[1]
    else:
        from urllib.parse import urlparse
        parsed = urlparse(clean)
        host = parsed.hostname or "local"
        url_path = parsed.path.strip("/")

    domain = _domain_label(host)
    segments = [s for s in url_path.split("/") if s]
    first = segments[0] if segments else "repo"
    last_url = segments[-1] if segments else first

    name_parts = [domain, first, last_url]
    path_clean = path.strip().strip("/")
    if path_clean:
        path_segments = [s for s in path_clean.split("/") if s]
        if path_segments:
            name_parts.append(path_segments[-1])

    # Deduplicate adjacent identical parts
    deduped = [name_parts[0]]
    for p in name_parts[1:]:
        if p != deduped[-1]:
            deduped.append(p)

    digest = hashlib.md5((url.strip() + "\0" + path.strip()).encode()).hexdigest()
    deduped.append(digest[-5:])

    return os.path.join(REPOS_DIR, "-".join(deduped))


def _git_run(args, cwd=None, timeout=60):
    """Run a git command and return (success, stdout, stderr)."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        result = subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            timeout=timeout, env=env,
        )
        return result.returncode == 0, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired as e:
        partial = ""
        if e.stderr:
            partial = e.stderr.strip() if isinstance(e.stderr, str) else e.stderr.decode(errors="replace").strip()
        return False, "", partial or "git command timed out"
    except FileNotFoundError:
        return False, "", "git is not installed"


def _check_repo_access(url):
    """Quick check if a git repo is reachable. Returns (ok, error_or_none)."""
    ok, _, err = _git_run(["ls-remote", "--heads", url], timeout=10)
    if ok:
        return True, None
    return False, _friendly_git_error(err)


def _ensure_repo(url, path=""):
    """Clone or pull a git repo. Returns (repo_dir, pkg_base, error).
    Checks remote accessibility first, cleans up broken clones automatically."""
    repo_dir = _repo_dir_from_url(url, path)
    git_dir = os.path.join(repo_dir, ".git")

    # Check remote accessibility before any heavy operation
    accessible, access_err = _check_repo_access(url)
    if not accessible:
        return repo_dir, None, access_err

    if os.path.isdir(git_dir):
        # Verify local repo is healthy
        ok, _, _ = _git_run(["rev-parse", "HEAD"], cwd=repo_dir)
        if not ok:
            # Broken clone — remove and re-clone below
            shutil.rmtree(repo_dir, ignore_errors=True)
        else:
            ok, out, err = _git_run(["pull", "--ff-only"], cwd=repo_dir, timeout=30)
            if not ok and "already up to date" not in err.lower():
                return repo_dir, None, _friendly_git_error(err)
            pkg_base = os.path.join(repo_dir, path) if path else repo_dir
            if not os.path.isdir(pkg_base):
                return repo_dir, None, f"Path '{path}' not found in repository"
            return repo_dir, pkg_base, None

    # Clone
    os.makedirs(REPOS_DIR, exist_ok=True)
    if path:
        ok, out, err = _git_run(
            ["clone", "--depth", "1", "--filter=blob:none", "--sparse", url, repo_dir],
            timeout=60
        )
        if not ok:
            return repo_dir, None, _friendly_git_error(err)
        ok, out, err = _git_run(["sparse-checkout", "set", path], cwd=repo_dir)
        if not ok:
            return repo_dir, None, f"Path configuration failed for '{path}'"
    else:
        ok, out, err = _git_run(["clone", "--depth", "1", url, repo_dir], timeout=60)
        if not ok:
            return repo_dir, None, _friendly_git_error(err)

    pkg_base = os.path.join(repo_dir, path) if path else repo_dir
    if not os.path.isdir(pkg_base):
        return repo_dir, None, f"Path '{path}' not found in repository"
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
    repo_dir = _repo_dir_from_url(url, path)
    git_dir = os.path.join(repo_dir, ".git")

    if not os.path.isdir(git_dir):
        return False, [], None

    # Check remote accessibility — skip silently if unreachable
    accessible, _ = _check_repo_access(url)
    if not accessible:
        return False, [], None

    ok, old_head, _ = _git_run(["rev-parse", "HEAD"], cwd=repo_dir)
    if not ok:
        return False, [], "Local repository is corrupt — try removing and re-adding it"

    _git_run(["pull", "--ff-only"], cwd=repo_dir, timeout=30)

    ok, new_head, _ = _git_run(["rev-parse", "HEAD"], cwd=repo_dir)
    if not ok:
        return False, [], "Failed to update — local repository may be corrupt"

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


HWNTOOLS_REPO = "https://github.com/hawwwran/hwntools.git"
HWNTOOLS_DOWNLOAD = "https://github.com/hawwwran/hwntools/releases/latest/download/hwntools.zip"


def _check_app_update():
    """Check if a newer version of HWN Tools is available.
    Returns (latest_version, has_update, error)."""
    ok, out, err = _git_run(
        ["ls-remote", "--tags", "--sort=-v:refname", HWNTOOLS_REPO],
        timeout=10
    )
    if not ok:
        return None, False, _friendly_git_error(err)
    latest = None
    for line in out.splitlines():
        parts = line.split("refs/tags/")
        if len(parts) == 2:
            tag = parts[1]
            if tag.startswith("v") and not tag.endswith("^{}"):
                latest = tag
                break
    if not latest:
        return None, False, "No releases found"
    remote_ver = latest.lstrip("v")
    return remote_ver, _version_newer(remote_ver, VERSION), None
