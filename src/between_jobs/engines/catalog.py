"""Every engine this build knows, by kind, and the questions asked of a stored document's
`generator` name."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from .backend import EngineCapabilities, EngineKind
from .generic import GENERIC_CAPABILITIES
from .remote import REMOTE_CAPABILITIES

CAPABILITIES_BY_KIND: Mapping[EngineKind, EngineCapabilities] = MappingProxyType(
    {"remote": REMOTE_CAPABILITIES, "generic": GENERIC_CAPABILITIES}
)


def claims_were_verified_by(generator: str | None) -> bool:
    """Whether a document stamped with this `generator` went through claim verification.

    A name this build does not know (a row from some other engine, or one with no name) is
    not verified: whether a check ran is not something to assume."""
    return any(
        capabilities.verifies_claims and capabilities.generator == generator
        for capabilities in CAPABILITIES_BY_KIND.values()
    )
