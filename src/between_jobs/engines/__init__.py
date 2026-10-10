"""The engine seam -- every resume engine, remote or built in, answers the same seven operations.

`EngineBackend` is the contract and `EngineCapabilities` is what each backend says it can do;
`RemoteBackend` is the client of a separate resume-engine service and `GenericBackend` is the
engine that ships in this repository. The API picks between them per call
(`between_jobs.api.engine_gateway`); callers depend on the contract, never on a concrete
backend. Deterministic code owns structure and shape, and a backend's model calls choose
words only (see CLAUDE.md's engineering rules).
"""

from between_jobs.engines.backend import (
    ENGINE_OPERATIONS,
    EngineBackend,
    EngineCapabilities,
    EngineKind,
    EngineOperation,
)
from between_jobs.engines.catalog import CAPABILITIES_BY_KIND, claims_were_verified_by
from between_jobs.engines.generic import GenericBackend, not_available
from between_jobs.engines.remote import RemoteBackend

__all__ = [
    "CAPABILITIES_BY_KIND",
    "ENGINE_OPERATIONS",
    "EngineBackend",
    "EngineCapabilities",
    "EngineKind",
    "EngineOperation",
    "GenericBackend",
    "RemoteBackend",
    "claims_were_verified_by",
    "not_available",
]
