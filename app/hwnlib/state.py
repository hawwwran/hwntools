import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager

from .constants import STATE_FILE

_LOCK_FILE = STATE_FILE + ".lock"


@contextmanager
def _flock_exclusive():
    fd = os.open(_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _read_locked():
    try:
        with open(STATE_FILE) as f:
            data = f.read()
    except FileNotFoundError:
        return {}
    except OSError as e:
        print(f"hwntools: cannot read state file: {e}", file=sys.stderr)
        return {}
    if not data.strip():
        return {}
    try:
        return json.loads(data)
    except json.JSONDecodeError as e:
        backup = f"{STATE_FILE}.corrupt-{int(time.time())}"
        try:
            os.replace(STATE_FILE, backup)
            print(
                f"hwntools: state file was corrupt ({e}); quarantined to {backup}",
                file=sys.stderr,
            )
        except OSError:
            pass
        return {}


def _write_locked(state):
    tmp = f"{STATE_FILE}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w") as f:
            json.dump(state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, STATE_FILE)
    except OSError as e:
        print(f"hwntools: failed to save state: {e}", file=sys.stderr)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def load_state():
    with _flock_exclusive():
        return _read_locked()


def save_state(state):
    with _flock_exclusive():
        _write_locked(state)


@contextmanager
def update_state():
    """Hold the state lock across load → mutate → save.

    Prevents concurrent hwntools instances (or threads) from clobbering each
    other's keys via the classic read-modify-write race.
    """
    with _flock_exclusive():
        state = _read_locked()
        yield state
        _write_locked(state)
