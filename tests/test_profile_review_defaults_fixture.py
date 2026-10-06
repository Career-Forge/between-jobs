"""The profile a draft carries, with every default the server fills in, as the web client's
review screen is tested against it.

`web/src/testing/serverProfileDefaults.json` is `ResumeTemplate.model_dump(mode="json")` of a
minimal profile (a name and one experience entry). The server's canonical profile always has this
shape -- every optional field present at its default -- and the review screen
(`web/src/lib/resumeImportReview.ts`) must label every value of it. A fixture written by hand
would drift from the model, so this test fails when the model changes, and the fix is to
regenerate the file from the model (and give any new field a label in the review).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from between_jobs.api.profile import ResumeTemplate

_FIXTURE = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "testing" / "serverProfileDefaults.json"
)


def _minimal_profile() -> dict[str, Any]:
    return {
        "personal": {"name": "Pat Example"},
        "experience": [
            {
                "title": "Data Engineer",
                "company": "Acme Corp",
                "start_date": "2022-01",
                "end_date": "present",
            }
        ],
    }


def test_the_web_fixture_is_the_models_own_dump_of_a_minimal_profile() -> None:
    dumped = ResumeTemplate.model_validate(_minimal_profile()).model_dump(mode="json")
    assert json.loads(_FIXTURE.read_text(encoding="utf-8")) == dumped


def test_the_fixture_still_has_the_render_only_defaults_the_review_must_not_show() -> None:
    # The reason the file exists: these always ride along, so a review that lists every leaf
    # would show them for every draft.
    personal = json.loads(_FIXTURE.read_text(encoding="utf-8"))["personal"]
    assert personal["signature"] is False
    assert personal["work_authorization_status"] == {}
