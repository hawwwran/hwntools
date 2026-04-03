import os


def subsequence_match(query, text):
    """Check if query is a subsequence of text (case insensitive).
    Returns True if each character in query appears in text in order."""
    query = query.lower()
    text = text.lower()
    qi = 0
    for ch in text:
        if qi < len(query) and ch == query[qi]:
            qi += 1
    return qi == len(query)


def parse_config(path):
    """Parse @key: value config from comment lines at the top of a script.
    Also collects @dep entries as a list in config["deps"]."""
    config = {}
    deps = []
    try:
        with open(path) as f:
            for i, line in enumerate(f):
                if i == 0 and line.startswith("#!"):
                    continue  # skip shebang
                line = line.strip()
                if not line.startswith("#"):
                    break
                if "# @" in line or line.startswith("# @"):
                    part = line.lstrip("# ").strip()
                    if part.startswith("@dep "):
                        deps.append(part[5:].strip())
                    elif part.startswith("@") and ":" in part:
                        key, _, value = part[1:].partition(":")
                        config[key.strip()] = value.strip()
                    elif part.startswith("@") and ":" not in part:
                        config[part[1:].strip()] = ""
    except Exception:
        pass
    if deps:
        config["deps"] = deps
    return config


def label_from_filename(name):
    """Derive a display label from a filename: remove extension, dashes to spaces, title case."""
    base = os.path.splitext(name)[0]
    return base.replace("-", " ").replace("_", " ").title()
