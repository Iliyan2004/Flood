# sailab-client

A thin Python client for the **SAILAB** flood-intelligence server. Install it in another project (for example SimCity)
and call SAILAB's simulations without copying the pipeline, its data, or its GPU solver.

## Does SAILAB have to keep running?

**Yes, while a call is in progress.** This package does not contain the model. The terrain (13 GB), buildings, tide
data, the GPU flood solver and its caches all live in the SAILAB install, and the client only sends requests to it.

What you can do about it:
- **Start it once and leave it** (`cd ~/flood && venv/bin/python fis_app.py`).
- **Let the plugin start it for you:** `ensure_server()` launches SAILAB from `SAILAB_HOME` (default `~/flood`) if
  nothing answers on `127.0.0.1`, waits until it is ready, and returns a client. It only does this for a local address.
- **Run it elsewhere:** point `SAILAB_URL` at a server on another machine (SSH tunnel, or its address with
  `SAILAB_ACCESS_TOKEN`). The other project then needs nothing but this package and a network path.

Results of the full model are cached by the server, so repeating the same request returns immediately.

## Install

    pip install git+https://github.com/<you>/sailab-client.git      # or: pip install -e .

## Use

```python
from sailab_client import SailabClient, ensure_server

client = ensure_server()                       # or SailabClient("http://host:8000", token="...")

aoi = {"type": "Polygon", "coordinates": [[[73.023,33.734],[73.033,33.734],[73.033,33.742],[73.023,33.742],[73.023,33.734]]]}

r = client.run_flood(aoi, rain_mm_hr=60, duration_hr=2)   # full 2D physics; waits for the result
print(r.flooded_area_km2, r.max_depth_m, r.building_loss_pkr, r.report_url)

client.instant_flood(aoi, discharge_m3s=300)               # sub-second terrain-index preview (approximate)
client.coastal_flood(karachi_aoi, return_period_years=100)
client.coastal_dynamic(karachi_aoi, anchor_mode="static", tide_phase="spring_high")
client.glof_discharge(lake_key="shishper_2022")
client.validate_observed(r.run_id, "observed_extent.geojson")   # CSI / POD / FAR against a mapped flood
```

`run_flood(..., wait=False)` returns the run id at once; `wait_flood(run_id)` collects it later. Any `/run` field
(friction, `infiltration_mm_hr`, `amc`, `inflows`, `rainfall_series`, ...) can be passed as a keyword. For anything not
wrapped, use `client.get(path)` / `client.post(path, json)`.

Errors: `SailabUnavailable` (server unreachable), `SailabError` (server refused, job failed; `.payload` has the server's JSON).

## SimCity

`SailabFloodSimulator` implements SimCity's `Simulator` contract (`id`, `metrics`, `levers`, `evaluate`):

```python
from sailab_client import SailabFloodSimulator
from simcity.core.registry import register_simulator
register_simulator(SailabFloodSimulator())            # full model
register_simulator(SailabFloodSimulator(fast=True))   # sub-second preview for the optimiser loop
```

Metrics returned: `flooded_area_km2`, `max_depth_m`, `building_loss_pkr` (all lower is better). A full run takes seconds
to minutes, so use `fast=True` inside an optimisation loop and re-check the finalists with the full model. SimCity
is not imported by this package; if it is installed its own `Metric`/`Lever` classes are used.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `SAILAB_URL` | Server address | `http://127.0.0.1:8000` |
| `SAILAB_ACCESS_TOKEN` | Token for a server on another machine (not needed locally) | none |
| `SAILAB_HOME` | SAILAB install, used only by `ensure_server()` | `~/flood` |

## Limits

- Pakistan only (the server's terrain and datasets). Coastal levels are for the Sindh/Balochistan coast, from the Karachi gauge.
- One physics run at a time on the server; other calls queue (`client.solver_status()`).
- `instant_flood` is an approximation; the full `run_flood` is the authoritative result.

## Tests

    pip install -e .[dev] && pytest        # uses a fake server; no SAILAB needed
