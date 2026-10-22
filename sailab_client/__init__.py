"""sailab-client: call a SAILAB flood server from another project."""
from .client import FloodResult, SailabClient, SailabError, SailabUnavailable
from .server import ensure_server
from .simcity_adapter import SailabFloodSimulator

__all__ = ["SailabClient", "FloodResult", "SailabError", "SailabUnavailable", "ensure_server", "SailabFloodSimulator"]
__version__ = "0.1.0"
