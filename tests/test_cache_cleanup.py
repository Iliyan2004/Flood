import os, time
import cache_cleanup as cc


def _mk(root, name, age_days, size=10):
    d = root / name; d.mkdir()
    f = d / "x.bin"; f.write_bytes(b"0" * size)
    t = time.time() - age_days * 86400
    os.utime(f, (t, t)); os.utime(d, (t, t))


def test_candidates_respect_age_newest_and_protected(tmp_path):
    for n, a in [("old1", 90), ("old2", 60), ("mid", 10), ("new", 1), ("pinned", 100)]:
        _mk(tmp_path, n, a)
    got = cc.find_candidates(str(tmp_path), days=30, keep_newest=1, protected={"pinned"})
    assert [g[0] for g in got] == ["old1", "old2"]
    assert all(g[2] == 10 for g in got)


def test_keep_newest_beats_age(tmp_path):
    _mk(tmp_path, "a", 90); _mk(tmp_path, "b", 80)
    assert cc.find_candidates(str(tmp_path), days=30, keep_newest=2) == []
