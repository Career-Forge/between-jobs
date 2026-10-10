"""Fixtures and a scripted model for the built-in engine's tests.

Everything here is synthetic: a made-up candidate, a made-up employer, a made-up job posting.
`ScriptedModel` stands in for `api/llm_client.generate`: each stage of the engine (reading the
job, the experience and projects, the summary, the cover letter) has its own queue of replies,
a call with nothing queued fails the test (so a stage that asks more often than the plan says
is caught), and every call is recorded so a test can read what was asked.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from between_jobs.api.credential_resolver import ResolvedCredential
from between_jobs.api.llm_client import LLMResponse

CREDENTIAL = ResolvedCredential(
    provider="openrouter",
    model="test/model",
    secret="sk-or-v1-test-secret-not-real",
    base_url=None,
    source="byok",
)

NOW = "2026-10-10T12:00:00.000Z"

SNAPSHOT: dict[str, Any] = {
    "job_id": "job-1",
    "title": "Staff Data Engineer",
    "company_name": "Globex Corporation",
    "location_text": "Remote (US)",
    "description_text": (
        "Globex is hiring a Staff Data Engineer to build streaming data platforms. You will "
        "design Kafka-based pipelines, operate Kubernetes clusters on AWS, and mentor "
        "engineers. Requirements: 8+ years of experience, Python, SQL, Kafka, Snowflake. "
        "Nice to have: Terraform, dbt, Spark."
    ),
    "source_url": "https://example.com/jobs/1",
    "source_kind": "manual_paste",
}

_PROFILE: dict[str, Any] = {
    "personal": {
        "name": "Avery Quill",
        "headline": "Backend engineer focused on data systems",
        "emails": [{"address": "avery.quill@example.com", "primary": True}],
        "phones": [{"number": "+1 555 010 0199", "primary": True}],
        "links": {
            "linkedin": "linkedin.com/in/averyquill",
            "github": "https://github.com/averyquill",
            "portfolio": "",
            "scholar": "",
            "other": [],
        },
        "location": {
            "city": "Springfield",
            "region": "NJ",
            "country": "USA",
            "show_on_resume": True,
        },
        "work_authorization": "Authorized to work in the US without sponsorship",
        "nationality": "Examplian",
        "dob": "1990-01-01",
    },
    "summary_bullets": ["Builds reliable data pipelines and the services around them."],
    "experience": [
        {
            "title": "Senior Software Engineer",
            "company": "Northwind Labs",
            "location": "Remote",
            "start_date": "2022-03",
            "end_date": "present",
            "is_current": True,
            "bullets": [
                "Reduced p95 API latency by 40% across 12 services by introducing request "
                "batching in Python and Go",
                "Built an event pipeline on Kafka processing 2M events per day with "
                "exactly-once delivery",
                "Mentored 3 junior engineers and led the migration of the billing service to "
                "PostgreSQL",
                "Cut cloud spend by $120K per year by right-sizing Kubernetes workloads on AWS",
                "Wrote the on-call runbook used by a team of 8 engineers",
                "Introduced contract tests that caught 15 breaking changes before release",
            ],
            "skills": ["Python", "Go", "Kafka", "PostgreSQL", "Kubernetes", "AWS"],
            "metrics": ["p95 latency -40%", "$120K annual savings"],
        },
        {
            "title": "Software Engineer",
            "company": "Contoso Analytics",
            "location": "Newark, NJ",
            "start_date": "2019-06",
            "end_date": "2022-02",
            "is_current": False,
            "bullets": [
                "Developed ETL jobs in Airflow loading 50 GB nightly into Snowflake",
                "Created a dashboard in Tableau used by 40 analysts",
                "Improved test coverage from 55% to 85% in the ingestion service",
            ],
            "skills": ["Airflow", "Snowflake", "Tableau", "Python"],
            "metrics": [],
        },
        {
            "title": "Intern",
            "company": "Fabrikam",
            "location": "Jersey City, NJ",
            "start_date": "2018-06",
            "end_date": "2018-08",
            "is_current": False,
            "bullets": ["Built an internal tool for tagging support tickets using scikit-learn"],
            "skills": ["Python", "scikit-learn"],
            "metrics": [],
        },
    ],
    "projects": [
        {
            "name": "logship",
            "url": "https://github.com/averyquill/logship",
            "tech": ["Rust", "SQLite"],
            "bullets": [
                "Wrote a log shipper in Rust that batches and compresses events",
                "Added a SQLite-backed retry queue",
            ],
            "metrics": [],
        },
        {
            "name": "tabletop-tracker",
            "url": "",
            "tech": ["TypeScript", "React"],
            "bullets": ["Built a campaign tracker used by 30 players"],
            "metrics": [],
        },
    ],
    "education": [
        {
            "degree": "BS",
            "field": "Computer Science",
            "institution": "State University",
            "location": "Springfield, NJ",
            "start_date": "2015-09",
            "end_date": "2019-05",
            "gpa": "3.7",
            "coursework": ["Databases", "Distributed Systems"],
        }
    ],
    "skills": {
        "programming": ["Python", "Go", "Rust", "SQL", "TypeScript"],
        "ai_ml": ["scikit-learn"],
        "data_mlops": ["Kafka", "Airflow", "Snowflake", "PostgreSQL"],
        "cloud_devops": ["AWS", "Kubernetes", "Docker", "Terraform"],
        "tools": ["Tableau", "Git"],
        "other": [],
    },
    "certifications": [
        {
            "name": "AWS Certified Developer",
            "issuer": "Amazon Web Services",
            "date": "2021-05",
            "url": "",
        }
    ],
    "achievements": ["Winner, internal hackathon 2023"],
    "languages": [
        {"language": "English", "fluency": "native"},
        {"language": "Spanish", "fluency": "conversational"},
    ],
}


def profile(**changes: Any) -> dict[str, Any]:
    """A fresh copy of the sample profile with top-level sections replaced."""
    value = copy.deepcopy(_PROFILE)
    value.update(copy.deepcopy(changes))
    return value


def snapshot(**changes: Any) -> dict[str, Any]:
    return {**SNAPSHOT, **changes}


STEP0_ANSWER = json.dumps(
    {
        "company_name": "Globex Corporation",
        "role_name": "Staff Data Engineer",
        "short_role": "Data Engineer",
        "target_tier": "staff",
        "clusters": [
            {"name": "Streaming data", "priority": "must_have", "keywords": ["Kafka", "pipelines"]},
            {"name": "Cloud", "priority": "nice_to_have", "keywords": ["Terraform"]},
        ],
        "dealbreakers": [],
        "key_terms": ["Kafka", "Snowflake", "Terraform", "dbt"],
    }
)


def body_answer(entries: dict[str, list[tuple[list[int], str]]]) -> str:
    """The bullets stage's JSON answer: `{pointer: [(source numbers, text), ...]}`."""
    return json.dumps(
        {
            "entries": [
                {
                    "pointer": pointer,
                    "bullets": [{"from": numbers, "text": text} for numbers, text in bullets],
                }
                for pointer, bullets in entries.items()
            ]
        }
    )


_ENTRY = re.compile(
    r"\[(/(?:experience|projects)/\d+)\][^\n]*\(minimum (\d+), maximum (\d+) bullets\)\n"
    r"((?:  [^\n]*\n?)+)"
)


def offered_entries(user_prompt: str) -> dict[str, list[str]]:
    """The entries the bullets prompt offered, `{pointer: [bullet text, ...]}`, read back from
    the prompt (which pins that the prompt shows each entry's pointer and numbered bullets)."""
    offered: dict[str, list[str]] = {}
    for match in _ENTRY.finditer(user_prompt):
        offered[match.group(1)] = [
            text for _number, text in re.findall(r"^  (\d+): (.*)$", match.group(4), re.M)
        ]
    return offered


def echo_body(user_prompt: str) -> str:
    """A faithful answer: every offered bullet, unchanged, up to each entry's maximum."""
    entries: dict[str, list[tuple[list[int], str]]] = {}
    for match in _ENTRY.finditer(user_prompt):
        pointer, maximum = match.group(1), int(match.group(3))
        bullets = re.findall(r"^  (\d+): (.*)$", match.group(4), re.M)
        entries[pointer] = [([int(n)], text) for n, text in bullets[:maximum]]
    return body_answer(entries)


SUMMARY_ANSWER = json.dumps(
    {"summary": "Backend engineer focused on data systems. Builds reliable data pipelines."}
)

COVER_ANSWER = json.dumps(
    {
        "paragraphs": [
            "I am excited to apply for the Staff Data Engineer role at Globex Corporation. My "
            "work on Kafka pipelines at Northwind Labs lines up closely with the streaming "
            "platforms your team builds, and I enjoy the reliability side of that work.",
            "At Northwind Labs I reduced p95 API latency by 40% across 12 services and built an "
            "event pipeline on Kafka processing 2M events per day. Before that, at Contoso "
            "Analytics, I developed ETL jobs in Airflow that loaded 50 GB nightly into "
            "Snowflake, which taught me how much operational discipline a data platform needs.",
            "I would welcome the chance to talk with the team about how that experience could "
            "help Globex Corporation. Thank you for your time and consideration.",
        ]
    }
)


def summary_from_notes(user_prompt: str) -> str:
    """A summary that is the candidate's own first summary note (or one plain word), so it passes
    the check for any profile."""
    notes = re.findall(r"^summary note: (.*)$", user_prompt, re.M)
    return json.dumps({"summary": notes[0] if notes else "Engineer."})


def cover_from_bullets(user_prompt: str) -> str:
    """A letter whose middle paragraph is the candidate's own first bullets, so it passes the
    check for any profile (special characters and all)."""
    own = [b.rstrip(".") + "." for b in re.findall(r"^  - (.*)$", user_prompt, re.M)[:3]]
    return json.dumps(
        {
            "paragraphs": [
                "I am excited to apply for this role because the work matches what I have done.",
                " ".join(own) or "I enjoy this work.",
                "I would welcome the chance to talk with the team about how that experience "
                "could help. Thank you for your time and your consideration of my application.",
            ]
        }
    )


Reply = str | Exception | Callable[[str], str]


@dataclass(slots=True)
class ModelCall:
    stage: str
    system: str
    user: str
    max_tokens: int | None
    api_key: str = ""
    model: str = ""
    base_url: str | None = None


_STAGES = (
    ("step0", "You read a job posting"),
    ("body", "You reword resume bullets"),
    ("summary", "You write the short summary"),
    ("cover", "You write the body of a cover letter"),
)


@dataclass(slots=True)
class ScriptedModel:
    """A stand-in for `llm_client.generate`. `replies` maps a stage name (`step0`, `body`,
    `summary`, `cover`) to what each successive call to that stage answers: a string, an
    exception to raise (a provider failure), or a function of the user prompt."""

    replies: dict[str, list[Reply]] = field(default_factory=dict)
    calls: list[ModelCall] = field(default_factory=list)

    @classmethod
    def faithful(cls, **overrides: list[Reply]) -> ScriptedModel:
        """Answers every stage the way a well-behaved model would, one call each."""
        replies: dict[str, list[Reply]] = {
            "step0": [STEP0_ANSWER],
            "body": [echo_body],
            "summary": [SUMMARY_ANSWER],
            "cover": [COVER_ANSWER],
        }
        replies.update(overrides)
        return cls(replies=replies)

    @classmethod
    def lenient(cls) -> ScriptedModel:
        """Answers whatever profile it is given in a way that passes the checks, and may be asked
        twice per stage, so a test about something else never trips over a repair."""
        return cls(
            replies={
                "step0": [STEP0_ANSWER],
                "body": [echo_body, echo_body],
                "summary": [summary_from_notes, summary_from_notes],
                "cover": [cover_from_bullets, cover_from_bullets],
            }
        )

    def count(self, stage: str) -> int:
        return sum(1 for call in self.calls if call.stage == stage)

    def last(self, stage: str) -> ModelCall:
        return [call for call in self.calls if call.stage == stage][-1]

    async def __call__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        stage = next((name for name, marker in _STAGES if marker in system_prompt), "unknown")
        self.calls.append(
            ModelCall(stage, system_prompt, user_prompt, max_tokens, api_key, model, base_url)
        )
        queue = self.replies.get(stage, [])
        assert len(queue) >= self.count(stage), f"unexpected call to the {stage} stage"
        reply = queue[self.count(stage) - 1]
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(content=reply(user_prompt) if callable(reply) else reply)
