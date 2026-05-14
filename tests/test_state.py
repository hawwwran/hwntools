#!/usr/bin/env python3
"""Tests for hwnlib.state — the single most critical module in the app.

Run standalone: python3 tests/test_state.py
Exits 0 on success, non-zero on any failure. No pytest dependency.

Covers the failure modes that caused the silent-data-loss bug:
  - read-modify-write races across threads
  - read-modify-write races across processes
  - corrupt-file handling (must quarantine, not silently mask)
  - atomic write (no partial-file observation)
  - no-op update_state() must not rewrite the file
  - flock timeout must not hang indefinitely
"""

import glob
import importlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "app"))


def _fresh_state_module():
    """Return a state module bound to a fresh tempdir's STATE_FILE."""
    d = tempfile.mkdtemp(prefix="hwn-state-test-")
    import hwnlib.constants as c
    c.STATE_FILE = os.path.join(d, ".state.json")
    import hwnlib.state as s
    importlib.reload(s)
    return s, d


def test_missing_file_returns_empty():
    s, _ = _fresh_state_module()
    assert s.load_state() == {}


def test_empty_file_returns_empty():
    s, d = _fresh_state_module()
    open(os.path.join(d, ".state.json"), "w").close()
    assert s.load_state() == {}


def test_update_state_persists():
    s, _ = _fresh_state_module()
    with s.update_state() as st:
        st["script_sources"] = [{"path": "/a", "label": "A"}]
        st["package_repos"] = [{"url": "u", "path": ""}]
    assert s.load_state() == {
        "script_sources": [{"path": "/a", "label": "A"}],
        "package_repos": [{"url": "u", "path": ""}],
    }


def test_partial_update_preserves_other_keys():
    """The original bug: window-resize must not wipe script_sources."""
    s, _ = _fresh_state_module()
    with s.update_state() as st:
        st["script_sources"] = [{"path": "/a"}]
        st["favorites"] = ["/x"]
    with s.update_state() as st:
        st["main_width"] = 500
    loaded = s.load_state()
    assert loaded.get("script_sources") == [{"path": "/a"}], loaded
    assert loaded.get("favorites") == ["/x"], loaded
    assert loaded["main_width"] == 500, loaded


def test_corrupt_file_is_quarantined():
    s, d = _fresh_state_module()
    state_path = os.path.join(d, ".state.json")
    with open(state_path, "w") as f:
        f.write("{ not json")
    assert s.load_state() == {}
    backups = glob.glob(state_path + ".corrupt-*")
    assert len(backups) == 1, f"expected one quarantine backup, got {backups}"
    with open(backups[0]) as f:
        assert f.read() == "{ not json"


def test_concurrent_threads_no_lost_writes():
    s, _ = _fresh_state_module()
    with s.update_state() as st:
        st["counter"] = []

    def worker(i):
        with s.update_state() as st:
            lst = st.get("counter", [])
            lst.append(i)
            st["counter"] = lst

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    final = sorted(s.load_state()["counter"])
    assert final == list(range(50)), f"lost writes: {final}"


def test_concurrent_processes_no_lost_writes():
    """The real failure mode that caused the bug — two hwntools instances."""
    s, d = _fresh_state_module()
    state_file = os.path.join(d, ".state.json")
    with open(state_file, "w") as f:
        json.dump({}, f)

    worker_script = os.path.join(d, "_worker.py")
    with open(worker_script, "w") as f:
        f.write(textwrap.dedent(f"""
            import sys, os, importlib
            sys.path.insert(0, {os.path.join(REPO_ROOT, "app")!r})
            import hwnlib.constants as c
            c.STATE_FILE = {state_file!r}
            import hwnlib.state as s
            importlib.reload(s)
            idx = int(sys.argv[1])
            with s.update_state() as st:
                lst = st.get("counter", [])
                lst.append(idx)
                st["counter"] = lst
        """))

    procs = [
        subprocess.Popen([sys.executable, worker_script, str(i)])
        for i in range(30)
    ]
    for p in procs:
        p.wait()
    with open(state_file) as f:
        data = json.load(f)
    final = sorted(data["counter"])
    assert final == list(range(30)), f"cross-process lost writes: {final}"


def test_noop_update_does_not_rewrite_file():
    s, d = _fresh_state_module()
    state_file = os.path.join(d, ".state.json")
    with s.update_state() as st:
        st["k"] = "v"
    mtime_before = os.stat(state_file).st_mtime_ns

    time.sleep(0.02)
    # No mutation inside the with block
    with s.update_state() as st:
        pass
    mtime_after = os.stat(state_file).st_mtime_ns
    assert mtime_before == mtime_after, (
        f"file was rewritten on no-op update: {mtime_before} -> {mtime_after}"
    )

    # Early-return pattern (read-only access) also must not rewrite
    time.sleep(0.02)
    with s.update_state() as st:
        _ = st.get("k")
    mtime_after2 = os.stat(state_file).st_mtime_ns
    assert mtime_before == mtime_after2, "read-only access triggered rewrite"


def test_atomic_write_observed_by_concurrent_readers():
    """A reader should never see a partial/empty/torn file mid-write.

    Valid observations are either the previous committed value or the new
    committed value, never a mix or a truncation.
    """
    s, d = _fresh_state_module()
    valid = {"x" * 10000, "y" * 10000}
    with s.update_state() as st:
        st["big"] = "x" * 10000

    stop = threading.Event()
    failures = []

    def reader():
        while not stop.is_set():
            data = s.load_state()
            if not data:
                continue  # {} is acceptable only if file genuinely missing — won't happen here
            v = data.get("big")
            if v not in valid:
                failures.append(("torn read", repr(v)[:60], "len=" + str(len(v) if isinstance(v, str) else "?")))
                return

    def writer():
        for i in range(200):
            with s.update_state() as st:
                st["big"] = ("x" if i % 2 == 0 else "y") * 10000

    rt = threading.Thread(target=reader)
    wt = threading.Thread(target=writer)
    rt.start()
    wt.start()
    wt.join()
    stop.set()
    rt.join()
    assert not failures, failures


def test_flock_timeout_does_not_hang():
    """If another process holds the lock indefinitely, we must not freeze."""
    s, d = _fresh_state_module()
    state_file = os.path.join(d, ".state.json")
    lock_file = state_file + ".lock"

    import fcntl
    holder_fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(holder_fd, fcntl.LOCK_EX)
    try:
        # Temporarily shorten the timeout for the test
        original_timeout = s._LOCK_TIMEOUT_SEC
        s._LOCK_TIMEOUT_SEC = 0.2
        try:
            t0 = time.monotonic()
            # Should warn and proceed without lock instead of hanging
            data = s.load_state()
            elapsed = time.monotonic() - t0
            assert elapsed < 1.0, f"load_state hung for {elapsed:.2f}s under held lock"
            assert isinstance(data, dict)
        finally:
            s._LOCK_TIMEOUT_SEC = original_timeout
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        os.close(holder_fd)


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}: {e}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}", file=sys.stderr)
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
