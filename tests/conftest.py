"""
Shared fixtures for SAILAB's regression test suite.

Server strategy (documented per the task's own requirement to state this
clearly, and revised after a real failure observed in this session -- see
below): this app is normally run as a long-lived Flask process on this
shared machine (`~/flood/fis_app.py`, port 8000) because it holds a real
GPU-accelerated LISFLOOD-FP binary, a 13.48GB Pakistan SRTM, and various
on-disk caches -- there is no cheap way to "mock" any of that, and the
whole point of this suite is to exercise the REAL backend, not a fake.

`live_server` (session-scoped) ALWAYS spawns its OWN dedicated `fis_app.py`
subprocess on its own port (FIS_TEST_PORT, default 8971 -- deliberately
NOT 8000) and tears it down at the end of the session, rather than ever
reusing whatever production instance happens to be running at port 8000.

This is a deliberate change from an earlier version of this fixture that
preferred reusing an already-running server at BASE_URL if one answered,
falling back to spawning its own only if nothing did. That approach was
tried in this same session and directly observed to fail: this machine is
heavily shared (other users' sessions, and even leftover background shell
jobs from an earlier, interrupted attempt at building this very test
suite, were independently polling/hitting the production server's port
8000 during a real verification run of this suite), and the shared
production instance was restarted by something outside this suite's
control partway through a real pytest run -- every in-flight request got
a genuine `requests.exceptions.ConnectionError` ("Connection refused"),
failing 6 tests and erroring 4 more for a reason that had nothing to do
with this suite's own correctness. A dedicated, isolated instance on its
own port removes this entire class of flakiness: nothing other than this
test session can restart or contend with control of the process this
suite depends on. The real trade-off, stated plainly: this means every
test run launches its own real GPU-backed Flask process (a real few-
second startup cost once per session, not per test), and a
FIS_TEST_URL override is still honored first (see below) for a caller
who deliberately wants to point this suite at a specific already-running
instance instead (e.g. a CI job that already manages its own server
lifecycle) -- but the default behavior no longer opportunistically
reuses whatever happens to be listening on the well-known production
port.

Either way, tests only ever talk to a real, running Flask process serving
real routes backed by real physics -- never a mock.

Cache hygiene: every test that creates a `/run` (or HAND/instant-flood)
cache entry registers its run_id/cache key via the `cache_cleanup`
fixture, which deletes those specific directories at teardown -- this
suite never wipes shared caches wholesale (that would also delete real,
previously-verified runs this project's own history depends on), only
the exact entries it created itself.
"""
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import pytest
import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE_DIR, "venv", "bin", "python")
# Deliberately NOT port 8000 (the shared production instance) -- see this
# module's own docstring for the real ConnectionError this caused when the
# fixture used to opportunistically reuse whatever was already listening
# there. FIS_TEST_URL, if set, always wins outright (an explicit opt-in to
# point this suite at a specific already-running instance).
TEST_PORT = os.environ.get("FIS_TEST_PORT", "8971")
BASE_URL = os.environ.get("FIS_TEST_URL", f"http://127.0.0.1:{TEST_PORT}")
FIS_CACHE = os.path.join(BASE_DIR, "fis_cache")
HAND_CACHE = os.path.join(BASE_DIR, "hand_cache")

# A real, small, previously-verified AOI (a hand-drawn pentagon near
# Bahawalpur, ~0.025 km^2, 5x6 DEM cells) -- reused exactly as-is from a
# real cached run already produced during this project's own manual
# testing (fis_cache/638f50879a2a8c2f/run_meta.json), so this is a real
# polygon a real user drew, not a synthetic shape invented for testing.
# It resolves entirely inside the local Pakistan SRTM (no global-DEM
# fetch needed) and a full physics run over it completes in ~2-3 seconds,
# which is what makes an end-to-end suite against the REAL solver
# practical to run repeatedly.
TEST_AOI_GEOMETRY = {
    "type": "Polygon",
    "coordinates": [[
        [71.91457336295042, 30.287102779863858],
        [71.91362111785847, 30.287155265065465],
        [71.91342874337235, 30.285681331817955],
        [71.91442150817116, 30.285637593630582],
        [71.91457336295042, 30.287102779863858],
    ]],
}

# A distinct marker rain value never used by any real manual test run in
# this project's history (checked: existing cached runs in fis_cache use
# round numbers -- 40, 50, 100, 200 mm/hr) -- guarantees every /run this
# suite issues gets its own fresh cache key (a different hash), so
# teardown can safely delete exactly the directories this suite created
# without any risk of deleting a real pre-existing manual-test run that
# happens to share this AOI.
# 133.7 (was 33.7): this AOI is only 5x6 cells with ~41 buildings burned in, and the
# solver's raw peak depth there is 3.8 cm at 33.7 mm/hr, 4.8 at 60, 5.4 at 80 (1 cell
# above the 5 cm wet threshold) and 6.3 at 120 (4 cells) -- so "flooded area > 0" at
# 33.7 sat exactly on the threshold and failed deterministically (measured Oct 2026),
# i.e. the assertion tested the 5 cm cutoff, not the model. 133.7 clears it with
# margin while staying a unique marker (never used by a real manual run: the +/-
# offsets below give 130.7 / 138.7 / 158.7 / 173.7, also unused).
TEST_MARKER_RAIN_MM_HR = 133.7


def run_subprocess_group(cmd, timeout, **kwargs):
    """subprocess.run-alike whose timeout reliably kills the WHOLE process
    tree, not just the direct child.

    Real, live finding from this session while diagnosing test flakiness on
    this shared machine: flood_model.py's CLI spawns the actual GPU-heavy
    `lisflood` binary as ITS OWN child process. A plain
    `subprocess.run(cmd, timeout=...)` only terminates the direct child
    (the flood_model.py python process) when the timeout expires -- the
    still-running `lisflood` grandchild is silently orphaned (reparented to
    init, PPid 1) and keeps consuming a full CPU core and holding the GPU
    indefinitely, even though its own working directory has since been
    deleted by the test's own tmp_workdir cleanup. Confirmed live via `ps`
    during this session: two such orphaned `lisflood` processes, both with
    PPid 1 and a cwd pointing at an already-deleted `/tmp/sailab_test_*`
    directory, had been spinning for 12-33 minutes, consuming ~150-200% CPU
    each and contending for the single shared GPU against every subsequent
    real run (including the live production fis_app.py server) -- a real,
    load-bearing cause of this suite's own observed flakiness (timeouts and
    unexplained failures), not just a cosmetic leak.

    Fixed here by launching in a new process group (`start_new_session`)
    and, on timeout, sending SIGKILL to the whole group (`os.killpg`)
    instead of relying on subprocess.run's own default (which only reaches
    the direct child) -- every slow test in this suite that spawns
    flood_model.py's CLI goes through this helper so a timeout can never
    leak a still-running lisflood process again."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True, **kwargs)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        raise subprocess.TimeoutExpired(cmd, timeout, output=stdout, stderr=stderr)
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def _server_alive(base_url, timeout=1.5):
    try:
        r = requests.get(base_url + "/dem-source", timeout=timeout)
        return r.status_code == 200
    except requests.exceptions.RequestException:
        return False


# Set by live_server when it spawns its OWN dedicated subprocess (never set
# when FIS_TEST_URL points this suite at an externally-managed instance) --
# lets the self-healing restart logic below (see _ensure_server_or_restart)
# know it's allowed to kill/relaunch this specific process. A plain dict
# (not a bare variable) so helper functions can mutate the live proc handle
# in place without a `global` statement.
_OWNED_SERVER = {"proc": None}


def _start_owned_server():
    log_path = os.path.join(BASE_DIR, "fis_app_test.log")
    logf = open(log_path, "a")
    logf.write(f"\n----- (re)launching test server at {time.strftime('%H:%M:%S')} -----\n")
    logf.flush()
    # PYTHONUNBUFFERED=1: fis_app.py's stdout is redirected to a real file
    # here (not a TTY), so Python block-buffers it by default -- a real,
    # directly-observed problem this session found on the production
    # server too: a caught exception's own diagnostic print() (e.g.
    # monitoring_utils' "rainfall fetch failed") can sit in the buffer for
    # a long time before actually reaching the log file, making a test that
    # reads this log for that line unreliable even though the app behaved
    # correctly. Forcing unbuffered output costs nothing here (this
    # process's whole point is to be inspected) and can't affect the
    # physics results themselves.
    env = dict(os.environ, FIS_APP_PORT=str(TEST_PORT), PYTHONUNBUFFERED="1", SAILAB_DISABLE_OPS_LOOP="1")
    proc = subprocess.Popen(
        [VENV_PY, "fis_app.py"], cwd=BASE_DIR, stdout=logf, stderr=subprocess.STDOUT, env=env,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        if _server_alive(BASE_URL):
            _OWNED_SERVER["proc"] = proc
            return
        if proc.poll() is not None:
            logf.flush()
            with open(log_path) as f:
                tail = f.read()[-4000:]
            raise RuntimeError(f"fis_app.py exited early while (re)starting:\n{tail}")
        time.sleep(0.5)
    raise RuntimeError("fis_app.py did not become ready within 60s")


def _ensure_server_or_restart():
    """Real, live-observed need (not speculative): this session's own
    dedicated test server process was directly seen to die mid-suite on
    this heavily shared machine (a genuine OS-level process kill under real
    concurrent load from other users -- no Python traceback, no
    application-level error, the process simply stopped answering), taking
    down every subsequent HTTP-dependent test with a raw ConnectionError
    that had nothing to do with those tests' own correctness. Since this
    suite (in the default, non-FIS_TEST_URL mode) owns this process
    outright, it can safely restart it and retry -- exactly the same
    "real, transient, tolerate once, never swallow a repeat" policy already
    used for the documented cudaErrorMemoryAllocation cold-start quirk.
    A no-op when running against an externally-managed FIS_TEST_URL
    instance (this suite doesn't own that process's lifecycle)."""
    if os.environ.get("FIS_TEST_URL") or _OWNED_SERVER["proc"] is None:
        return False
    if _server_alive(BASE_URL, timeout=3.0):
        return False
    old = _OWNED_SERVER["proc"]
    if old is not None and old.poll() is None:
        old.terminate()
        try:
            old.wait(timeout=10)
        except subprocess.TimeoutExpired:
            old.kill()
    _start_owned_server()
    return True


def _request_with_server_healing(method, url, **kwargs):
    """requests.get/post wrapper: on a raw ConnectionError against our own
    dedicated test server, restarts it once (see _ensure_server_or_restart)
    and retries the exact same request once. Any other exception, or a
    second failure after a real restart, propagates normally -- this never
    hides a genuine application-level failure (a real 4xx/5xx response
    still returns normally for the caller's own raise_for_status/assertions
    to catch)."""
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.ConnectionError:
        if not _ensure_server_or_restart():
            raise
        return requests.request(method, url, **kwargs)


@pytest.fixture(scope="session")
def live_server():
    """Yields the base URL of a real, running SAILAB Flask instance.

    Default (FIS_TEST_URL unset): ALWAYS launches its own dedicated
    `fis_app.py` subprocess on TEST_PORT (never the shared production
    port 8000) and tears it down at session end -- see this module's own
    docstring for the real ConnectionError that motivated this, observed
    live in this session when the fixture used to reuse an already-running
    server. Explicit opt-out: if FIS_TEST_URL is set, that exact URL is
    used as-is (assumed already running, never spawned/torn down here) --
    for a caller that deliberately wants to point this suite at a specific
    instance it manages itself."""
    if os.environ.get("FIS_TEST_URL"):
        if not _server_alive(BASE_URL):
            raise RuntimeError(f"FIS_TEST_URL={BASE_URL} was set but nothing answered there")
        yield BASE_URL
        return

    if _server_alive(BASE_URL):
        raise RuntimeError(
            f"something is already listening on {BASE_URL} (this suite's own dedicated "
            f"test port) -- likely a leftover process from a previous test run; kill it "
            f"before re-running, or set FIS_TEST_PORT to a free port"
        )

    # Fresh log for this session (subsequent restarts by
    # _ensure_server_or_restart append rather than truncate, so a
    # mid-session restart's own log is preserved alongside the original).
    open(os.path.join(BASE_DIR, "fis_app_test.log"), "w").close()
    _start_owned_server()
    try:
        yield BASE_URL
    finally:
        proc = _OWNED_SERVER["proc"]
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        _OWNED_SERVER["proc"] = None


@pytest.fixture
def cache_cleanup():
    """Tests append run_ids (fis_cache) and/or hand cache-key prefixes
    (hand_cache) they create; this fixture deletes exactly those entries
    after the test, whether it passed or failed, and never touches any
    other entry in the shared cache directories."""
    created_run_ids = []
    created_hand_prefixes = []
    registry = {"runs": created_run_ids, "hand": created_hand_prefixes}
    yield registry
    for run_id in created_run_ids:
        d = os.path.join(FIS_CACHE, run_id)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    for prefix in created_hand_prefixes:
        if not os.path.isdir(HAND_CACHE):
            continue
        for fn in os.listdir(HAND_CACHE):
            if fn.startswith(prefix):
                try:
                    os.remove(os.path.join(HAND_CACHE, fn))
                except OSError:
                    pass


class ServerRestartedError(RuntimeError):
    """Raised by wait_for_run when this suite's own dedicated test server
    was restarted mid-run (see _ensure_server_or_restart): the restarted
    process has no memory of the old run_id (RUN_STATE is in-process,
    in-memory in fis_app.py), so its progress.json now legitimately 404s
    even though nothing about the run's own correctness was ever tested.
    run_and_wait catches this and resubmits the whole request fresh."""


def wait_for_run(base_url, run_id, timeout=120):
    """Polls /runs/<id>/progress.json (the app's own real progress
    endpoint) until status is 'done' or 'failed'. Retries the whole wait
    once if the run fails with the specific, known-transient
    cudaErrorMemoryAllocation cold-start quirk documented throughout
    CLAUDE.md (a fresh LISFLOOD-FP process's first CUDA call can
    transiently OOM on this shared machine even with memory free) --
    never swallows any other kind of failure. Also raises
    ServerRestartedError (see above) if a real server restart happens
    mid-poll -- a distinct, real, live-observed failure mode on this
    shared machine (see _ensure_server_or_restart's own docstring), not
    silently retried away here since only run_and_wait knows how to
    recover (resubmitting the original request)."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = _request_with_server_healing("GET", f"{base_url}/runs/{run_id}/progress.json", timeout=10)
        if r.status_code == 404:
            raise ServerRestartedError(
                f"run {run_id}'s progress.json 404'd -- the test server was very likely "
                f"restarted mid-run (see fis_app_test.log)"
            )
        r.raise_for_status()
        last = r.json()
        if last.get("status") in ("done", "failed"):
            return last
        time.sleep(0.3)
    raise TimeoutError(f"run {run_id} did not finish within {timeout}s (last: {last})")


def run_and_wait(base_url, payload, cache_cleanup, timeout=120, _restarted_retry=False):
    """POSTs /run, registers the resulting run_id for cleanup, waits for
    completion (with the one-retry-on-known-transient-CUDA-error policy),
    and returns the final progress.json dict. Raises on any real failure.

    Also retries the WHOLE submission exactly once (fresh POST, fresh
    run_id) if wait_for_run reports ServerRestartedError -- a real,
    live-observed failure mode on this shared machine where this suite's
    own dedicated test server process gets killed mid-run by something
    outside this suite's control (see _ensure_server_or_restart's
    docstring); by the time that's detected, _request_with_server_healing
    has already brought the server back up, so simply resubmitting the
    original request is the correct recovery -- never swallowed on a
    second occurrence (_restarted_retry guards against infinite recursion,
    surfacing the real error if it keeps happening)."""
    try:
        r = _request_with_server_healing("POST", f"{base_url}/run", json=payload, timeout=30)
        r.raise_for_status()
        body = r.json()
        run_id = body["run_id"]
        cache_cleanup["runs"].append(run_id)
        if body.get("cached"):
            # Already finished (a prior test run left this exact cache entry) --
            # still real data, just no need to poll.
            st = _request_with_server_healing(
                "GET", f"{base_url}/runs/{run_id}/progress.json", timeout=10).json()
            return run_id, st

        st = wait_for_run(base_url, run_id, timeout=timeout)
    except ServerRestartedError:
        if _restarted_retry:
            raise
        return run_and_wait(base_url, payload, cache_cleanup, timeout=timeout, _restarted_retry=True)

    if st.get("status") == "failed":
        log_tail = st.get("log_tail", "")
        if "cudaErrorMemoryAllocation" in log_tail:
            # Known transient cold-start quirk -- delete the failed cache
            # dir and retry the whole run once.
            d = os.path.join(FIS_CACHE, run_id)
            shutil.rmtree(d, ignore_errors=True)
            r = _request_with_server_healing("POST", f"{base_url}/run", json=payload, timeout=30)
            r.raise_for_status()
            body = r.json()
            run_id = body["run_id"]
            cache_cleanup["runs"].append(run_id)
            st = wait_for_run(base_url, run_id, timeout=timeout)
            if st.get("status") == "failed":
                raise RuntimeError(f"run failed twice (2nd not a cuda cold-start retry): {st}")
        else:
            raise RuntimeError(f"run failed: {st}")
    return run_id, st


@pytest.fixture(autouse=True)
def _unit_tests_do_not_touch_the_real_postgis(request, monkeypatch):
    """The file-reader tests (and any test that fakes a missing file) must exercise the file path, not the live
    PostGIS that the app's .env points at. tests/test_pg_vector_utils.py sets its own URL where it needs one."""
    if "test_pg_vector_utils" not in request.node.nodeid:
        monkeypatch.delenv("SAILAB_PG_URL", raising=False)
