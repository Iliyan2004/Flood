"""
Shared Overpass API helper -- used by every real OSM fetch in this app
(infra_utils.py's roads/hospitals/power/trees, rainfall_rivers_utils.py's
waterway siting) so the retry/reliability logic lives in exactly one
place instead of being copy-pasted per call site.
"""
import time

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"


def query_overpass(query, timeout=40):
    """POSTs a real Overpass QL query, with the same real-data-first
    header workaround and retry logic already proven in this app:

    - Overpass's Apache front-end 406s requests lacking a proper Accept
      header (python-requests doesn't set one by default; curl does).
    - The public overpass-api.de instance genuinely 504s under its own
      load from time to time (confirmed directly via curl retries a few
      seconds apart during this app's own testing -- the identical query
      succeeds moments later) -- a single failed call here would silently
      drop every feature for whichever real-data lookup called it, with
      no error surfaced anywhere except a log line. Retried up to 2 more
      times with a short backoff, but ONLY for the transient failure
      modes (timeout, 5xx) -- a real 4xx (a malformed query) fails
      immediately, since retrying something that can never succeed would
      only add delay before the same real error surfaces.

    Returns the parsed JSON response's "elements" list."""
    headers = {"Accept": "*/*", "User-Agent": "SAILAB/1.0"}
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=timeout + 10)
            resp.raise_for_status()
            return resp.json().get("elements", [])
        except requests.exceptions.HTTPError as e:
            last_exc = e
            if e.response is not None and e.response.status_code < 500:
                raise
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    raise last_exc
