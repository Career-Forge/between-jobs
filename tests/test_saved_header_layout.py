"""Which saved Header Composer layout real generation uses (launch plan P0.12)."""

from __future__ import annotations

from typing import Any

import pytest

from between_jobs.api.prepare_orchestrator import saved_header_layout

_PER_APP: dict[str, Any] = {"chips": [{"field": "github"}], "separator": "dot"}
_MASTER: dict[str, Any] = {"chips": [{"field": "email"}], "separator": "pipe"}


def test_the_per_application_layout_wins() -> None:
    assert saved_header_layout({"header_layout": _PER_APP}, {"header_layout": _MASTER}) == _PER_APP


def test_the_master_layout_is_the_default() -> None:
    assert saved_header_layout({"header_layout": {}}, {"header_layout": _MASTER}) == _MASTER
    assert saved_header_layout(None, {"header_layout": _MASTER}) == _MASTER


def test_a_per_application_layout_is_not_merged_with_the_masters() -> None:
    """A layout is one whole object -- chip order, labels, separator -- so there is nothing to
    merge field by field."""
    chosen = saved_header_layout(
        {"header_layout": {"separator": "dot"}}, {"header_layout": _MASTER}
    )

    assert chosen == {"separator": "dot"}


@pytest.mark.parametrize(
    ("per_application", "master"),
    [
        (None, None),
        ({}, {}),
        ({"header_layout": {}}, {"header_layout": {}}),
        ({"header_layout": None}, {"header_layout": None}),
        ({"header_layout": []}, {"header_layout": "chips"}),
    ],
)
def test_nothing_saved_means_none(per_application: Any, master: Any) -> None:
    """A document that never saved a layout stores `{}`; anything that is not a JSON object
    is not a layout either."""
    assert saved_header_layout(per_application, master) is None
