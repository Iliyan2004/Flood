"""Start the SAILAB server on demand, so the other project does not need a terminal left open.

The server needs the full SAILAB install (data, GPU solver); set SAILAB_HOME to that directory (default ~/flood)."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Optional

from .client import SailabClient, SailabError


def sailab_home() -> str:
    return os.path.expanduser(os.environ.get("SAILAB_HOME", "~/flood"))


def ensure_server(client: Optional[SailabClient] = None, wait_s: float = 120.0, log_path: Optional[str] = None) -> SailabClient:
    """Returns a client for a running server, launching one from SAILAB_HOME if none answers.

    Only launches when the target is this machine (127.0.0.1 / localhost); for a remote server it just checks."""
    client = client or SailabClient()
    if client.health():
        return client
    host = client.base_url.split("//", 1)[-1].split(":")[0]
    if host not in ("127.0.0.1", "localhost"):
        raise SailabError(f"SAILAB at {client.base_url} is not answering and is not local, so it was not started")
    home = sailab_home()
    app = os.path.join(home, "fis_app.py")
    py = os.path.join(home, "venv", "bin", "python")
    if not (os.path.exists(app) and os.path.exists(py)):
        raise SailabError(f"cannot start SAILAB: {app} or {py} not found (set SAILAB_HOME)")
    log = open(log_path or os.path.join(home, "sailab_autostart.log"), "ab")
    subprocess.Popen([py, app], cwd=home, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                     start_new_session=True)
    end = time.time() + wait_s
    while time.time() < end:
        if client.health():
            return client
        time.sleep(2)
    raise SailabError(f"started SAILAB but it did not answer within {wait_s:.0f} s (see {log.name})")
