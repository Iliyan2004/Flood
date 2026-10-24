import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from sailab_client import SailabClient, SailabError, SailabFloodSimulator, SailabUnavailable

STATE = {"polls": 0, "auth": []}
SUMMARY = {"flooded_area_km2": 1.5, "max_depth_m": 2.2, "estimated_building_loss": {"total_loss_pkr": 1000.0},
           "infra_risk": {"shelters": {"in_domain": 4, "flooded_count": 1, "flooded_pct_of_in_domain": 25.0}, "roads": {"in_domain": 10, "flooded_count": 0, "flooded_pct_of_in_domain": 0.0,
                         "road_detail": {"major": {"in_domain": 3, "flooded": 1}, "bridges": {"in_domain": 2, "flooded": 1}, "flooded_length_km": 0.4}}}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        STATE["auth"].append(self.headers.get("Authorization"))
        if self.path == "/solver-status":
            return self._send(200, {"holder": None, "waiting": []})
        if self.path == "/runs/r1/progress.json":
            STATE["polls"] += 1
            return self._send(200, {"status": "running"} if STATE["polls"] < 2 else {"status": "done", "summary": SUMMARY})
        if self.path == "/runs/bad/progress.json":
            return self._send(200, {"status": "failed", "log_tail": "boom"})
        if self.path == "/climate-jobs/j1/progress.json":
            return self._send(200, {"status": "done", "result": {"flooded_area_km2": 3.0}})
        self._send(404, {"error": "nope"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0)); body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/run":
            return self._send(400, {"error": "no area selected"}) if not body.get("geometry") else self._send(200, {"run_id": "r1", "cached": False, "echo": body})
        if self.path == "/area/instant-flood":
            return self._send(200, {"flooded_area_km2": 0.5, "max_depth_m": 1.0})
        if self.path == "/area/coastal-flood-dynamic":
            return self._send(200, {"job_id": "j1"})
        self._send(404, {"error": "nope"})


@pytest.fixture(scope="module")
def url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


AOI = {"type": "Polygon", "coordinates": [[[73, 33], [73.1, 33], [73.1, 33.1], [73, 33]]]}


def test_run_flood_waits_and_returns_result(url):
    STATE["polls"] = 0
    r = SailabClient(url).run_flood(AOI, 55, 3, poll=0.01, friction=0.04)
    assert r.run_id == "r1" and r.flooded_area_km2 == 1.5 and r.building_loss_pkr == 1000.0
    assert r.report_url.endswith("/runs/r1/report.html")
    assert r.road_detail["bridges"]["flooded"] == 1 and r.road_detail["flooded_length_km"] == 0.4
    assert r.infrastructure["shelters"] == {"in_domain": 4, "flooded": 1, "flooded_pct": 25.0} and r.infrastructure["roads"]["flooded"] == 0


def test_params_are_forwarded(url):
    c = SailabClient(url)
    assert c.start_flood(AOI, 55, 3, friction=0.04)["echo"]["friction"] == 0.04


def test_server_error_message_surfaces(url):
    with pytest.raises(SailabError, match="no area selected"):
        SailabClient(url).start_flood({})


def test_failed_run_raises(url):
    with pytest.raises(SailabError, match="boom"):
        SailabClient(url).wait_flood("bad", poll=0.01)


def test_unreachable_server_is_distinct_error():
    c = SailabClient("http://127.0.0.1:9", timeout=2)
    assert c.health() is False
    with pytest.raises(SailabUnavailable):
        c.solver_status()


def test_token_sent_as_bearer(url):
    STATE["auth"].clear()
    SailabClient(url, token="abc").health()
    assert STATE["auth"][-1] == "Bearer abc"


def test_background_job_result(url):
    assert SailabClient(url).coastal_dynamic(AOI, tide_phase="low")["flooded_area_km2"] == 3.0


def test_simcity_adapter_scores_with_scenario_object(url):
    class Sc:  # stands in for simcity Scenario
        aoi = AOI

    sim = SailabFloodSimulator(SailabClient(url))
    STATE["polls"] = 0
    out = sim.evaluate({"rain_mm_hr": 60}, Sc())
    assert set(out) == {m.key for m in sim.metrics} and out["flooded_area_km2"] == 1.5
    assert SailabFloodSimulator(SailabClient(url), fast=True).evaluate({}, Sc())["flooded_area_km2"] == 0.5
