import json, math, time, urllib.request
BASE = "http://localhost:8000"
CENTER = (73.0620, 33.6070)   # Rawalpindi / Islamabad, along Nullah Lai
SIDE_KM = 3.2
def box(lon, lat, km):
    dlat = km / 2 / 111.0; dlon = km / 2 / (111.0 * math.cos(math.radians(lat)))
    return {"type": "Polygon", "coordinates": [[[lon - dlon, lat - dlat], [lon + dlon, lat - dlat], [lon + dlon, lat + dlat], [lon - dlon, lat + dlat], [lon - dlon, lat - dlat]]]}
GEOM = box(*CENTER, SIDE_KM)
def post(path, body, timeout=600):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(r, timeout=timeout))
def get(path, timeout=60):
    return json.load(urllib.request.urlopen(BASE + path, timeout=timeout))
def poll_job(job_id, every=4, maxwait=1800):
    t0 = time.time()
    while time.time() - t0 < maxwait:
        p = get(f"/climate-jobs/{job_id}/progress.json")
        if p.get("status") in ("done", "error", "failed", "cancelled"): return p
        time.sleep(every)
    raise TimeoutError(job_id)
