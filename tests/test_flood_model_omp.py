"""
Fast, no-physics regression test for flood_model.py's real OpenMP
thread-count fix: run_lisflood explicitly pins OMP_NUM_THREADS=1 for the
CPU/ACC solver path. This looks backwards until you see the real
benchmark data (flood_model.py's own comment on this line, and
CLAUDE.md): the CPU/ACC solver (fp_acc.cpp) is genuinely OpenMP-parallel
but also uses `#pragma omp critical` inside the same parallel region,
and a real, controlled A/B benchmark on this project's own shared
machine showed 1 thread finishing in ~60s while every multi-threaded
value tried (2, 4, 20/unset-default) took 6-6.8 MINUTES -- a 6-7x
slowdown, not a speedup. Pinning to 1 is the measured-correct choice,
not an oversight. This mocks subprocess.run to inspect the actual env
dict passed, so it runs in milliseconds instead of needing a real
LISFLOOD-FP process.
"""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import flood_model  # noqa: E402


def test_run_lisflood_pins_omp_num_threads_to_one():
    fake_proc = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("flood_model.subprocess.run", return_value=fake_proc) as mock_run:
        flood_model.run_lisflood("/tmp/does_not_matter", "site.par", verbose=False)
    assert mock_run.called
    passed_env = mock_run.call_args.kwargs["env"]
    assert passed_env["OMP_NUM_THREADS"] == "1"


def test_run_lisflood_omp_num_threads_overrides_any_preexisting_env_value():
    """A caller/OS-level OMP_NUM_THREADS set BEFORE this process starts
    (e.g. a shell that exported a different value) must not silently
    override this app's own measured-correct pin -- confirmed here with
    a deliberately different pre-existing value to prove it gets
    overwritten, not just happens to already match."""
    fake_proc = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch.dict(os.environ, {"OMP_NUM_THREADS": "20"}):
        with mock.patch("flood_model.subprocess.run", return_value=fake_proc) as mock_run:
            flood_model.run_lisflood("/tmp/does_not_matter", "site.par", verbose=False)
    passed_env = mock_run.call_args.kwargs["env"]
    assert passed_env["OMP_NUM_THREADS"] == "1"
