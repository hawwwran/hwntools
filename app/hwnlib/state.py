import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager

from .constants import STATE_FILE

_LOCK_FILE = STATE_FILE + ".lock"
_LOCK_TIMEOUT_SEC = 2.0


@contextmanager
def _flock_exclusive():
    """Acquire an exclusive cross-process lock on the state file.

    Polls non-blocking flock and bounds the wait at _LOCK_TIMEOUT_SEC. If the
    lock can't be acquired in time (another instance hung or wedged), prints a
    warning and yields anyway — atomic writes still prevent file corruption,
    and freezing the GTK main thread indefinitely is worse than a rare write
    race in a degraded mode.
    """
    fd = os.open(_LOCK_FILE, os.O_RDWR | os.O_CREAT, 0o644)
    locked = False
    deadline = time.monotonic() + _LOCK_TIMEOUT_SEC
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print(
                        f"hwntools: state lock contended for >{_LOCK_TIMEOUT_SEC}s; "
                        f"proceeding without lock (writes may race)",
                        file=sys.stderr,
                    )
                    break
                time.sleep(0.02)
        yield
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _read_locked():
    """Read and parse the state file. Caller must hold the lock.

    Side effect: if the file is present but contains invalid JSON, it is
    renamed to .state.json.corrupt-<unixtime> so the operator can recover it
    by hand, and {} is returned. An empty or missing file returns {} with no
    side effect (treated as a fresh install).
    """
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
    """Return a snapshot of saved state, or {} if the file is missing/empty.

    A corrupt state file is quarantined to .state.json.corrupt-<unixtime> as a
    side effect — see _read_locked.
    """
    with _flock_exclusive():
        return _read_locked()


def save_state(state):
    """Atomically overwrite the state file.

    Prefer update_state() for any read-modify-write — save_state replaces the
    whole file and is only safe when the caller already has the complete state.
    """
    with _flock_exclusive():
        _write_locked(state)


@contextmanager
def update_state():
    """Hold the state lock across load → mutate → save.

    Prevents concurrent hwntools instances (or threads) from clobbering each
    other's keys via the classic read-modify-write race. Writes back only if
    the yielded dict was actually mutated, so early returns that don't change
    state don't trigger a redundant fsync + replace.
    """
    with _flock_exclusive():
        state = _read_locked()
        before = json.dumps(state, sort_keys=True)
        yield state
        if json.dumps(state, sort_keys=True) != before:
            _write_locked(state)
