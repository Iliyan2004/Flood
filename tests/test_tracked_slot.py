import threading, time
import fis_app


def test_slot_reports_holder_and_waiter():
    slot = fis_app.TrackedSlot()
    started, release = threading.Event(), threading.Event()

    def holder():
        with slot.labelled("job A"):
            started.set(); release.wait(5)

    t = threading.Thread(target=holder); t.start(); started.wait(5)
    waiter_done = threading.Event()

    def waiter():
        with slot.labelled("job B"):
            waiter_done.set()
    t2 = threading.Thread(target=waiter); t2.start(); time.sleep(0.3)
    snap = slot.snapshot()
    assert snap["holder"]["label"] == "job A" and [w["label"] for w in snap["waiting"]] == ["job B"]
    release.set(); t.join(5); t2.join(5)
    assert waiter_done.is_set() and slot.snapshot() == {"holder": None, "waiting": []}


def test_plain_with_still_works():
    slot = fis_app.TrackedSlot()
    with slot:
        assert slot.snapshot()["holder"]["label"] == "flood run"
    assert slot.snapshot()["holder"] is None
