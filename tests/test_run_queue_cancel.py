import fis_app


def test_cancel_queued_run_marks_it_cancelled_and_removes_from_queue():
    rid = "unittestqueued01"
    with fis_app.RUN_STATE_LOCK:
        fis_app.RUN_STATE[rid] = {"status": "queued", "stage": "buildings", "queued_at": 0}
        fis_app.RUN_QUEUE.append(rid)
    c = fis_app.app.test_client()
    assert c.get(f"/runs/{rid}/progress.json").get_json()["queue_position"] == 1
    r = c.post(f"/runs/{rid}/cancel")
    assert r.status_code == 200 and r.get_json()["status"] == "cancelled"
    assert rid not in fis_app.RUN_QUEUE
    assert c.get(f"/runs/{rid}/progress.json").get_json()["status"] == "cancelled"


def test_cannot_cancel_during_postprocessing_and_unknown_is_404():
    rid = "unittestpost01"
    with fis_app.RUN_STATE_LOCK:
        fis_app.RUN_STATE[rid] = {"status": "postprocessing", "stage": "postprocess"}
    c = fis_app.app.test_client()
    assert c.post(f"/runs/{rid}/cancel").status_code == 409
    assert c.post("/runs/doesnotexist123/cancel").status_code == 404


def test_version_shown_in_page():
    html = fis_app.app.test_client().get("/").get_data(as_text=True)
    assert f"v{fis_app.APP_VERSION}" in html
