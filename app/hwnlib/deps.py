import re
import shutil
import subprocess


def check_dependencies(deps):
    """Check if all dependencies are satisfied.
    Each dep is a string like 'jq', 'curl 7.68.0', or 'jq 1.6+'.
    Returns list of (dep_name, problem_description) for failures."""
    failures = []
    for dep in deps:
        parts = dep.split(None, 1)
        name = parts[0]
        version_spec = parts[1] if len(parts) > 1 else None

        if not shutil.which(name):
            failures.append((name, "not found"))
            continue

        if version_spec:
            allow_higher = version_spec.endswith("+")
            required = version_spec.rstrip("+")
            installed = _get_version(name)
            if installed is None:
                failures.append((name, f"installed but cannot determine version (need {version_spec})"))
            elif not _version_ok(installed, required, allow_higher):
                if allow_higher:
                    failures.append((name, f"version {installed} < required {required}+"))
                else:
                    failures.append((name, f"version {installed} != required {required}"))
    return failures


def _get_version(name):
    """Try to extract version string from a command."""
    for flag in ("--version", "-version", "version"):
        try:
            out = subprocess.check_output([name, flag], stderr=subprocess.STDOUT, timeout=5).decode()
            m = re.search(r'(\d+\.\d+[\.\d]*)', out)
            if m:
                return m.group(1)
        except Exception:
            continue
    return None


def _version_ok(installed, required, allow_higher):
    """Compare version tuples."""
    def to_tuple(v):
        return tuple(int(x) for x in v.split("."))
    try:
        inst = to_tuple(installed)
        req = to_tuple(required)
        if allow_higher:
            return inst >= req
        return inst == req
    except ValueError:
        return False
