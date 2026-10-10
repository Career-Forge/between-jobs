"""The summary and cover letter stages (engines/generic/summary.py, cover.py).

Both are checked against the whole profile (and, for the letter, the job's company, title and
place), both get one repair call and no more, and both have a fallback that says nothing the
candidate did not write.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from generic_engine_fakes import (
    COVER_ANSWER,
    CREDENTIAL,
    SNAPSHOT,
    STEP0_ANSWER,
    SUMMARY_ANSWER,
    ScriptedModel,
    profile,
    snapshot,
)

from between_jobs.api.engine_contract import Step0Result
from between_jobs.engines.generic import cover as cover_module
from between_jobs.engines.generic.cover import (
    MAX_WORDS,
    MIN_WORDS,
    CoverResult,
    write_cover_letter,
)
from between_jobs.engines.generic.job import JobRead, combine_terms, prepare_job
from between_jobs.engines.generic.llm import ModelSession
from between_jobs.engines.generic.sources import Corpus, build_corpus
from between_jobs.engines.generic.summary import SummaryResult, wanted, write_summary

_NOW = datetime(2026, 10, 10, tzinfo=UTC)


def _context(
    job_snapshot: dict[str, Any] | None = None, **profile_changes: Any
) -> tuple[Corpus, JobRead]:
    corpus = build_corpus(profile(**profile_changes))
    job = prepare_job(job_snapshot or SNAPSHOT)
    read = combine_terms(corpus, job, Step0Result.model_validate(json.loads(STEP0_ANSWER)))
    return corpus, read


# -- summary ---------------------------------------------------------------------------------


async def _summary(model: ScriptedModel, mode: str = "on", **changes: Any) -> SummaryResult:
    corpus, read = _context(**changes)
    return await write_summary(
        ModelSession(CREDENTIAL, model), prepare_job(SNAPSHOT), read, corpus, mode
    )


def _summary_reply(text: str) -> str:
    return json.dumps({"summary": text})


@pytest.mark.parametrize(
    ("mode", "has_notes", "calls"),
    [("off", True, 0), ("off", False, 0), ("auto", False, 0), ("auto", True, 1), ("on", False, 1)],
)
async def test_summary_mode_decides_whether_a_summary_is_written_at_all(
    mode: str, has_notes: bool, calls: int
) -> None:
    model = ScriptedModel.faithful()
    notes = ["Builds data pipelines."] if has_notes else []

    result = await _summary(model, mode, summary_bullets=notes)

    assert model.count("summary") == calls
    assert (result.text is not None) is bool(calls)


async def test_an_unknown_summary_mode_is_treated_as_off() -> None:
    corpus, _read = _context()
    assert wanted("sometimes", corpus) is False


async def test_a_faithful_summary_is_used_and_costs_one_call() -> None:
    model = ScriptedModel.faithful()

    result = await _summary(model)

    assert result.text and result.from_model
    assert model.count("summary") == 1 and result.claim_warnings == [] and result.notes == []


@pytest.mark.parametrize(
    ("bad", "mention"),
    [
        ("Senior backend engineer with 12 years of experience.", "12"),
        ("Staff-level engineer building Kafka pipelines at Google.", "Google"),
        ("Backend engineer who managed teams building data systems.", None),
        ("I build reliable data pipelines.", None),
        ("Backend engineer. " + "Builds reliable pipelines. " * 40, None),
        ("Backend engineer fluent in Terraform and dbt.", "dbt"),
        ("Backend engineer with ten years of experience building data systems.", "ten years"),
        ("Backend engineer with over a decade of experience.", "decade"),
        ("Backend engineer with twenty years of experience.", "twenty years"),
        ("Principal-level backend engineer and recognised expert in data systems.", "principal"),
        ("Staff-level engineer building Kafka pipelines.", "staff"),
        ("An expert backend engineer with lead-level skills.", "expert"),
        ("Backend engineer who built dozens of data pipelines.", "dozens"),
        ("Backend engineer who owns the data platform.", "owns"),
        ("My work is data systems.", None),
        ("Backend engineer; ask me about pipelines.", None),
    ],
)
async def test_a_summary_that_overreaches_is_repaired_once_then_replaced_by_the_candidates_notes(
    bad: str, mention: str | None
) -> None:
    model = ScriptedModel.faithful(summary=[_summary_reply(bad), _summary_reply(bad)])

    result = await _summary(model)

    assert model.count("summary") == 2
    assert result.text == "Builds reliable data pipelines and the services around them."
    assert result.from_model is False
    assert any("could not be verified" in note for note in result.notes)
    if mention:
        assert any(
            w.startswith("unsupported claim") and mention in w for w in result.claim_warnings
        )


async def test_the_fallback_is_the_candidates_first_two_notes_and_no_more() -> None:
    notes = ["Builds data pipelines.", "Mentors junior engineers.", "Writes the runbooks."]
    bad = _summary_reply("Backend engineer with 12 years of experience.")
    model = ScriptedModel.faithful(summary=[bad, bad])

    result = await _summary(model, summary_bullets=notes)

    assert result.text == "Builds data pipelines. Mentors junior engineers."


async def test_the_summary_repair_is_told_what_was_wrong_with_the_first_answer() -> None:
    first = _summary_reply("I build reliable data pipelines with 12 years of experience.")
    model = ScriptedModel.faithful(summary=[first, SUMMARY_ANSWER])

    await _summary(model)

    repair = model.calls[-1].user
    assert "<problems>" in repair and "the summary uses the first person" in repair
    assert 'the number "12" is not in' in repair
    assert "<previous_answer>" in repair


@pytest.mark.parametrize("pronoun", ["I", "My", "my", "me", "mine"])
async def test_every_first_person_word_makes_a_summary_a_problem(pronoun: str) -> None:
    bad = _summary_reply(f"Backend engineer on data systems, {pronoun} specialty.")
    model = ScriptedModel.faithful(summary=[bad, bad])

    result = await _summary(model)

    assert model.count("summary") == 2 and not result.from_model


async def test_the_prompt_carries_at_most_four_of_the_candidates_summary_notes() -> None:
    notes = [f"Note number {n} about the work." for n in range(6)]
    model = ScriptedModel.faithful()

    await _summary(model, summary_bullets=notes)

    prompt = model.last("summary").user
    assert "Note number 3" in prompt and "Note number 4" not in prompt


async def test_a_level_the_profile_states_is_allowed_in_a_summary() -> None:
    """ "Senior" is in the profile (a job title), so a summary may use it; "ten years" and
    "decades" are not, until the candidate writes them."""
    senior = _summary_reply("Senior backend engineer focused on data systems.")
    model = ScriptedModel.faithful(summary=[senior])

    result = await _summary(model)

    assert result.from_model and model.count("summary") == 1 and result.claim_warnings == []


async def test_years_the_candidate_wrote_themselves_may_be_stated_in_words_in_a_summary() -> None:
    notes = ["Builds data pipelines, ten years of it, and the services around them."]
    said = _summary_reply("Backend engineer with ten years of building data pipelines.")
    model = ScriptedModel.faithful(summary=[said])

    result = await _summary(model, summary_bullets=notes)

    assert result.from_model and model.count("summary") == 1


async def test_a_word_of_scale_the_profile_uses_is_allowed_in_a_summary() -> None:
    notes = ["Shipped dozens of data pipelines."]
    said = _summary_reply("Backend engineer who shipped dozens of data pipelines.")
    model = ScriptedModel.faithful(summary=[said])

    result = await _summary(model, summary_bullets=notes)

    assert result.from_model and model.count("summary") == 1


async def test_io_is_not_the_first_person() -> None:
    model = ScriptedModel.faithful(
        summary=[_summary_reply("Backend engineer focused on data systems and Kafka I/O.")]
    )

    result = await _summary(model)

    assert result.from_model and model.count("summary") == 1


async def test_a_summary_the_repair_fixes_is_used() -> None:
    model = ScriptedModel.faithful(
        summary=[_summary_reply("Backend engineer with 12 years of experience."), SUMMARY_ANSWER]
    )

    result = await _summary(model)

    assert result.from_model and model.count("summary") == 2
    assert result.claim_warnings and result.claim_warnings[0].startswith(
        "unsupported claim (removed before output)"
    )


async def test_with_no_notes_of_the_candidates_own_a_failed_summary_means_no_summary() -> None:
    bad = _summary_reply("Staff engineer with 12 years of experience.")
    model = ScriptedModel.faithful(summary=[bad, bad])

    result = await _summary(model, summary_bullets=[])

    assert result.text is None
    assert any("no summary" in note for note in result.notes)


@pytest.mark.parametrize("garbage", ["", "not json", "{}", '{"summary": ""}', '{"summary": 5}'])
async def test_an_unreadable_summary_answer_is_repaired_once_then_given_up(garbage: str) -> None:
    model = ScriptedModel.faithful(summary=[garbage, garbage])

    result = await _summary(model)

    assert model.count("summary") == 2
    assert result.text == "Builds reliable data pipelines and the services around them."


async def test_the_summary_prompt_carries_no_contact_or_personal_detail() -> None:
    model = ScriptedModel.faithful()

    await _summary(model)

    prompt = model.last("summary").user + model.last("summary").system
    for private in (
        "avery.quill@example.com",
        "555 010 0199",
        "Examplian",
        "1990-01-01",
        "Authorized to work",
        "sponsorship",
        "linkedin.com",
    ):
        assert private not in prompt


# -- cover letter ------------------------------------------------------------------------------


async def _letter(
    model: ScriptedModel, job: dict[str, Any] | None = None, **profile_changes: Any
) -> CoverResult:
    corpus, read = _context(job, **profile_changes)
    return await write_cover_letter(
        ModelSession(CREDENTIAL, model), prepare_job(job or SNAPSHOT), read, corpus, _NOW
    )


def _paragraphs(*paragraphs: str) -> str:
    return json.dumps({"paragraphs": list(paragraphs)})


_GOOD = json.loads(COVER_ANSWER)["paragraphs"]


async def test_a_faithful_letter_is_used_as_written() -> None:
    model = ScriptedModel.faithful()

    result = await _letter(model)

    assert result.content.opening == _GOOD
    assert not result.fallback and result.claim_warnings == [] and result.notes == []
    assert model.count("cover") == 1
    assert result.content.words() >= MIN_WORDS


async def test_a_sentence_that_invents_a_fact_is_removed_after_one_repair() -> None:
    lie = "I led the Kubernetes migration at Google, saving $5M."
    bad = _paragraphs(_GOOD[0], _GOOD[1] + " " + lie, _GOOD[2])
    model = ScriptedModel.faithful(cover=[bad, bad])

    result = await _letter(model)

    text = " ".join(result.content.opening)
    assert model.count("cover") == 2
    assert "Google" not in text and "$5M" not in text
    assert _GOOD[0] in text  # the sentences that were fine survive
    assert [w.split("):")[0] for w in result.claim_warnings] == [
        "unsupported claim (removed before output",
        "unsupported claim (removed",
    ]
    assert all("[cover letter]" in w for w in result.claim_warnings)
    assert any("removed" in note for note in result.notes)


async def test_a_letter_the_repair_fixes_is_used() -> None:
    bad = _paragraphs(_GOOD[0], _GOOD[1] + " I led the Kubernetes migration at Google.", _GOOD[2])
    model = ScriptedModel.faithful(cover=[bad, COVER_ANSWER])

    result = await _letter(model)

    assert result.content.opening == _GOOD
    assert len(result.claim_warnings) == 1 and model.count("cover") == 2


_POLICY_SENTENCES = [
    "I am authorized to work in the United States without sponsorship.",
    "I will not require visa sponsorship now or in the future.",
    "I am a U.S. citizen.",
    "I hold a green card.",
    "I am on OPT and eligible to work.",
    "I am happy to relocate to Austin.",
    "I can start immediately.",
    "My notice period is two weeks.",
    "My salary expectations are flexible.",
    # the same things in other words
    "I am legally entitled to work in the US.",
    "I hold full work rights in the US.",
    "I am a green-card holder.",
    "I do not require employer support to work in the US.",
    "I will not require immigration support.",
    "I can start in two weeks.",
    "I am available to join immediately.",
    "I am immediately available.",
    "I am eligible for employment in the US.",
    "I am a US national.",
    "I am a Canadian resident.",
    "My employer-backed immigration filing is complete.",
    "I could begin within a month.",
    "I would be free to start on June 1.",
    "I am an immediate joiner.",
]


@pytest.mark.parametrize("sentence", _POLICY_SENTENCES)
async def test_a_letter_never_states_work_authorization_relocation_or_availability(
    sentence: str,
) -> None:
    bad = _paragraphs(_GOOD[0], _GOOD[1] + " " + sentence, _GOOD[2])
    model = ScriptedModel.faithful(cover=[bad, bad])

    result = await _letter(model)

    assert sentence not in " ".join(result.content.opening)
    assert any("never states" in w for w in result.claim_warnings)
    assert model.count("cover") == 2


@pytest.mark.parametrize(
    "sentence",
    [
        "I would welcome the chance to join your team.",
        "I can start by helping improve data quality.",
        "I am available for a conversation at your convenience.",
        "Soon after joining Northwind Labs I shipped the first pipeline.",
        "My national-scale rollout work taught me a lot.",
        "I joined the on-call rotation and learned the platform quickly.",
        "I enjoy the start of a project, when the requirements are still open.",
    ],
)
async def test_the_net_for_those_topics_does_not_remove_ordinary_sentences(sentence: str) -> None:
    fine = _paragraphs(_GOOD[0], _GOOD[1] + " " + sentence, _GOOD[2])
    model = ScriptedModel.faithful(cover=[fine])

    result = await _letter(model)

    assert not [w for w in result.claim_warnings if "never states" in w], sentence


# One sentence per alternative, each caught by that alternative and no other: take any one
# alternative away and its sentence is let through. (A new alternative needs a sentence of its
# own, or the test below says so.)
_AUTHORIZATION_SENTENCES = [
    "I hold authorization for this role.",
    "This role needs sponsored status.",
    "I need a visa for this role.",
    "I am a citizen of Examplia.",
    "I hold a green card.",
    "My status is permanent resident.",
    "I hold a work permit.",
    "I hold full work rights in the US.",
    "My work status is settled.",
    "I have the right to remain here.",
    "I am cleared to work here.",
    "I am legally permitted to take this role.",
    "My employment status is fine.",
    "Immigration matters are settled.",
    "I am a US national.",
    "I do not need company help here.",
    "I want support to work remotely.",
]
_AVAILABILITY_SENTENCES = [
    "I am happy to relocate.",
    "My salary is flexible.",
    "My compensation expectations are open.",
    "My notice period is short.",
    "My start date is flexible.",
    "I am an immediate joiner.",
    "I am immediately available.",
    "I am available from May.",
    "I can start immediately.",
    "I need three weeks to join.",
]


@pytest.mark.parametrize(
    ("alternatives", "sentences"),
    [
        (cover_module._AUTHORIZATION_ALTERNATIVES, _AUTHORIZATION_SENTENCES),
        (cover_module._AVAILABILITY_ALTERNATIVES, _AVAILABILITY_SENTENCES),
    ],
    ids=["work authorization", "relocation or availability"],
)
def test_every_alternative_of_the_net_is_the_only_thing_stopping_some_sentence(
    alternatives: tuple[str, ...], sentences: list[str]
) -> None:
    for index, alternative in enumerate(alternatives):
        others = re.compile(
            "|".join(a for i, a in enumerate(alternatives) if i != index), re.IGNORECASE
        )
        mine = re.compile(alternative, re.IGNORECASE)
        only_this = [s for s in sentences if mine.search(s) and not others.search(s)]
        assert only_this, f"no sentence needs the alternative {alternative!r}"


@pytest.mark.parametrize("sentence", [*_AUTHORIZATION_SENTENCES, *_AVAILABILITY_SENTENCES])
def test_each_of_those_sentences_is_found(sentence: str) -> None:
    assert [f.token for f in cover_module._policy_findings(sentence)], sentence


@pytest.mark.parametrize("code", ["H-1B", "H1B", "OPT", "CPT", "L-1", "L1", "TN", "EAD"])
def test_a_visa_or_work_permit_code_is_found_by_itself(code: str) -> None:
    assert cover_module._policy_findings(f"I work under {code} status.")
    # the codes are upper case: the same letters inside an ordinary word are not one
    assert not cover_module._policy_findings(f"I work under {code.lower()} conditions.")


@pytest.mark.parametrize(
    "word", ["eligible", "entitled", "permitted", "allowed", "able", "cleared"]
)
def test_each_word_that_says_a_person_may_work_is_found(word: str) -> None:
    assert cover_module._policy_findings(f"I am {word} to work here.")
    assert cover_module._policy_findings(f"I am {word} for employment here.")


@pytest.mark.parametrize("word", ["authorized", "eligible", "able", "permitted", "allowed"])
def test_each_word_after_legally_is_found(word: str) -> None:
    assert cover_module._policy_findings(f"I am legally {word} to take this role.")


async def test_the_work_authorization_text_never_reaches_the_prompt_or_the_letter() -> None:
    model = ScriptedModel.faithful()

    result = await _letter(model)

    prompt = model.last("cover").user + model.last("cover").system
    assert "Authorized to work" not in prompt and "sponsorship" not in prompt.replace(
        "sponsorship, citizenship", ""
    )
    assert "authorized" not in " ".join(result.content.opening).lower()


@pytest.mark.parametrize(
    "placeholder",
    [
        "Dear [Hiring Manager],",
        "I would join [Company Name] gladly.",
        "Insert a closing line.",
        "Deal with the details, tbd.",
    ],
)
async def test_a_placeholder_is_a_problem_to_repair(placeholder: str) -> None:
    bad = _paragraphs(_GOOD[0], placeholder, _GOOD[1], _GOOD[2])
    model = ScriptedModel.faithful(cover=[bad, COVER_ANSWER])

    result = await _letter(model)

    assert model.count("cover") == 2
    assert result.content.opening == _GOOD
    assert "placeholder" in model.calls[-1].user


async def test_a_letter_with_too_little_left_falls_back_to_a_plain_letter_of_own_bullets() -> None:
    lie = "I led the Kubernetes migration at Google and saved $5M for the company this year."
    model = ScriptedModel.faithful(cover=[_paragraphs(lie, lie), _paragraphs(lie, lie)])

    result = await _letter(model)

    assert result.fallback
    assert model.count("cover") == 2
    content = result.content
    assert (
        content.opening[0]
        == "I am writing to apply for the Staff Data Engineer position at Globex Corporation."
    )
    assert content.highlights and len(content.highlights) <= 3
    # every highlight is one of the candidate's own bullets, verbatim
    own = {b for e in profile()["experience"] for b in e["bullets"]}
    own |= {b for p in profile()["projects"] for b in p["bullets"]}
    assert set(content.highlights) <= own
    assert any("short plain letter" in note for note in result.notes)


@pytest.mark.parametrize(
    "garbage", ["", "no json", '{"paragraphs": []}', '{"paragraphs": "text"}', "{"]
)
async def test_an_unreadable_letter_answer_ends_in_the_plain_letter_after_one_repair(
    garbage: str,
) -> None:
    model = ScriptedModel.faithful(cover=[garbage, garbage])

    result = await _letter(model)

    assert result.fallback and model.count("cover") == 2


async def test_the_jobs_company_and_title_are_allowed_in_the_letter_and_nothing_else() -> None:
    mentions_company = _paragraphs(
        _GOOD[0],
        _GOOD[1],
        _GOOD[2] + " I am drawn to Globex Corporation's work in Remote (US) teams.",
    )
    invents_product = _paragraphs(
        _GOOD[0], _GOOD[1], _GOOD[2] + " I especially admire your Atlas platform."
    )

    fine = await _letter(ScriptedModel.faithful(cover=[mentions_company]))
    flagged_model = ScriptedModel.faithful(cover=[invents_product, invents_product])
    flagged = await _letter(flagged_model)

    assert fine.claim_warnings == []
    assert any("Atlas" in w for w in flagged.claim_warnings)
    assert "Atlas" not in " ".join(flagged.content.opening)


async def test_the_cover_prompt_carries_the_job_and_the_candidates_words_only() -> None:
    model = ScriptedModel.faithful()

    await _letter(model)

    prompt = model.last("cover").user
    assert "Staff Data Engineer" in prompt and "Globex Corporation" in prompt
    assert "Reduced p95 API latency by 40%" in prompt
    for private in ("avery.quill@example.com", "555 010 0199", "Examplian", "1990-01-01"):
        assert private not in prompt


async def test_a_job_with_no_company_or_title_still_gets_a_plain_letter_when_the_model_fails() -> (
    None
):
    model = ScriptedModel.faithful(cover=["", ""])

    result = await _letter(model, snapshot(title="", company_name=""))

    assert result.content.opening[0] == "I am writing to apply for this position."


async def test_a_short_letter_that_lost_nothing_is_kept_not_replaced() -> None:
    short = _paragraphs(
        "I am applying for the Staff Data Engineer role at Globex Corporation. My Kafka work at "
        "Northwind Labs is a close match for the streaming platforms your team builds.",
        "I would welcome the chance to talk about it.",
    )
    model = ScriptedModel.faithful(cover=[short])

    result = await _letter(model)

    assert not result.fallback and model.count("cover") == 1
    assert result.content.words() < 70


async def test_a_short_letter_that_had_a_sentence_removed_becomes_the_plain_letter() -> None:
    lie = "I led the Kubernetes migration at Google."
    short = _paragraphs(
        "I am applying for the Staff Data Engineer role at Globex Corporation. " + lie,
        "I would welcome the chance to talk about it.",
    )
    model = ScriptedModel.faithful(cover=[short, short])

    result = await _letter(model)

    assert result.fallback and "Google" not in " ".join(result.content.opening)


# -- years of experience, the job's own words, and what a repair does not rescue -----------------


def _letter_with(*sentences: str) -> str:
    """The faithful letter with `sentences` added to its second paragraph."""
    return _paragraphs(_GOOD[0], _GOOD[1] + " " + " ".join(sentences), _GOOD[2])


def _text(result: CoverResult) -> str:
    return " ".join(result.content.opening)


@pytest.mark.parametrize(
    "claim",
    [
        "I bring 15 years of experience building data platforms.",  # 15 is in the profile
        "I bring 12 years of experience building data platforms.",  # so is 12
        "I bring twelve years of experience building data platforms.",
        "I bring 30+ years of experience building data platforms.",
        "I bring over a decade of experience building data platforms.",
        "I bring decades of experience building data platforms.",
    ],
)
async def test_a_stated_length_of_experience_beyond_the_dated_experience_is_removed(
    claim: str,
) -> None:
    model = ScriptedModel.faithful(cover=[_letter_with(claim), _letter_with(claim)])

    result = await _letter(model)

    assert claim not in _text(result) and model.count("cover") == 2
    assert any(
        ("[cover letter]" in w and "years" in w) or "decade" in w for w in result.claim_warnings
    )
    assert _GOOD[0] in _text(result)  # the rest of the letter is kept


@pytest.mark.parametrize(
    "claim",
    [
        "I bring 8 years of experience building data platforms.",  # the dates add up to 8 and a bit
        "I bring eight years of experience building data platforms.",
        "In the last 3 years I mentored junior engineers on the team.",
        "I cut latency 40% across 12 services.",  # 12 services are not 12 years
    ],
)
async def test_a_length_of_experience_the_dates_support_is_kept(claim: str) -> None:
    model = ScriptedModel.faithful(cover=[_letter_with(claim)])

    result = await _letter(model)

    assert model.count("cover") == 1 and result.claim_warnings == []
    assert claim in _text(result)


async def test_experience_is_counted_from_the_dates_not_from_digits_found_anywhere() -> None:
    """A profile whose jobs all fall in the last two years: the months in its dates ("2025-10")
    used to make "10 years" look supported, and "12 services" made "12 years" look supported."""
    base = profile()["experience"]
    short = [
        {**base[0], "start_date": "2025-10", "end_date": "present"},
        {**base[1], "start_date": "2025-03", "end_date": "2025-09", "is_current": False},
        {**base[2], "start_date": "2025-01", "end_date": "2025-02", "is_current": False},
    ]
    claim = "I bring 12 years of experience building data platforms."
    model = ScriptedModel.faithful(cover=[_letter_with(claim)] * 2)

    result = await _letter(model, experience=short)

    assert claim not in _text(result) and model.count("cover") == 2


async def test_a_length_of_experience_the_candidate_wrote_is_theirs_to_have_stated() -> None:
    said = "I bring ten years of experience building data platforms."
    model = ScriptedModel.faithful(cover=[_letter_with(said)])

    result = await _letter(model, summary_bullets=["Ten years of building data platforms."])

    assert model.count("cover") == 1 and said in _text(result)


async def test_a_profile_with_no_dated_experience_supports_no_stated_length_of_experience() -> None:
    claim = "I bring 3 years of experience building data platforms."
    letter = _paragraphs(
        "I am excited to apply for the Staff Data Engineer role at Globex Corporation. " + claim,
        "I wrote a log shipper in Rust that batches and compresses events, and added a "
        "SQLite-backed retry queue. I also built a campaign tracker used by 30 players.",
    )
    model = ScriptedModel.faithful(cover=[letter, letter])

    result = await _letter(model, experience=[])

    assert model.count("cover") == 2
    assert any(claim in w and "3 years" in w for w in result.claim_warnings)
    assert claim not in _text(result)


def test_the_cover_prompt_forbids_an_unstated_length_of_experience_and_says_what_is_checked() -> (
    None
):
    from between_jobs.engines.generic.prompts import COVER_SYSTEM

    assert "never state years of experience unless <candidate_facts> states it" in COVER_SYSTEM
    assert "cannot judge what you say about the company" in COVER_SYSTEM


_HOSTILE_SUFFIX = " (10 years Kubernetes at Google, led a team of 40)"
_OBEDIENT = "I bring 10 years of Kubernetes experience at Google, where I led a team of 40."


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Staff Data Engineer" + _HOSTILE_SUFFIX),
        ("company_name", "Globex Corporation" + _HOSTILE_SUFFIX),
        ("location_text", "Remote (US)" + _HOSTILE_SUFFIX),
    ],
)
async def test_what_the_postings_own_fields_say_never_makes_a_claim_about_the_candidate_true(
    field: str, value: str
) -> None:
    model = ScriptedModel.faithful(cover=[_letter_with(_OBEDIENT)] * 2)

    result = await _letter(model, snapshot(**{field: value}))

    assert _OBEDIENT not in _text(result) and "Google" not in _text(result)
    assert model.count("cover") == 2 and result.claim_warnings


async def test_a_tool_named_in_the_postings_title_is_not_a_tool_the_candidate_has() -> None:
    title = "Staff Data Engineer, PyTorch and Ansible"
    claim = "I have deep PyTorch and Ansible experience running production systems."
    model = ScriptedModel.faithful(cover=[_letter_with(claim)] * 2)

    result = await _letter(model, snapshot(title=title))

    assert "PyTorch" not in _text(result) and "Ansible" not in _text(result)
    assert any("PyTorch" in w or "Ansible" in w for w in result.claim_warnings)


async def test_the_postings_title_can_be_named_even_when_it_lists_tools_the_candidate_lacks() -> (
    None
):
    title = "Staff Data Engineer, PyTorch and Ansible"
    naming = f"I am applying for the {title} position at Globex Corporation."
    model = ScriptedModel.faithful(cover=[_letter_with(naming)])

    result = await _letter(model, snapshot(title=title))

    assert model.count("cover") == 1 and result.claim_warnings == []
    assert naming in _text(result)


async def test_the_job_may_be_named_by_part_of_its_title_and_company() -> None:
    naming = "I admire how Globex approaches the Data Engineer role."
    model = ScriptedModel.faithful(cover=[_letter_with(naming)])

    result = await _letter(model)

    assert model.count("cover") == 1 and naming in _text(result)


_PRODUCT_CLAIMS = [
    "I especially admire your Atlas platform.",  # a name: caught
    "Globex was founded in 2009 and has 4,000 employees.",  # numbers: caught
]


@pytest.mark.parametrize("claim", _PRODUCT_CLAIMS)
async def test_a_claim_about_the_company_that_names_something_or_counts_something_is_caught(
    claim: str,
) -> None:
    model = ScriptedModel.faithful(cover=[_letter_with(claim)] * 2)

    result = await _letter(model)

    assert claim not in _text(result) and result.claim_warnings


@pytest.mark.parametrize(
    "claim",
    [
        "Globex is the industry leader in streaming analytics and has grown quickly lately.",
        "I admire how Globex serves large enterprise customers across the retail sector.",
    ],
)
async def test_a_claim_about_the_company_in_plain_words_is_not_checked_and_the_docs_say_so(
    claim: str,
) -> None:
    """The only company facts a letter has are the name, the job title and the place, and a
    check of tokens cannot tell a true sentence about a company from a false one. This pins the
    limit so that a change to it is a decision, and pins that the reader is told."""
    model = ScriptedModel.faithful(cover=[_letter_with(claim)])

    result = await _letter(model)

    assert claim in _text(result) and result.claim_warnings == [] and model.count("cover") == 1
    from pathlib import Path

    readme = " ".join(
        (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8").split()
    )
    assert "is not checked" in (cover_module.__doc__ or "") or "NOT check" in (
        cover_module.__doc__ or ""
    )
    assert "industry leader" in (cover_module.__doc__ or "")
    assert "not checked at all" in readme and "industry leader" in readme


@pytest.mark.parametrize(
    "placeholder",
    [
        "I am especially drawn to [insert specific product here] because of the data challenges.",
        "Insert a closing line here about the data challenges.",
        "Thank you, [your name].",
        "I would join [Company. Name] gladly.",
        "Please address it to your name.",
        "The start is TBD.",
        "Hello {company} team.",
        "Call <name> for details.",
    ],
)
async def test_a_placeholder_that_survives_the_repair_is_removed_and_reported(
    placeholder: str,
) -> None:
    bad = _letter_with(placeholder)
    model = ScriptedModel.faithful(cover=[bad, bad])

    result = await _letter(model)

    text = _text(result)
    for leftover in ("[", "]", "insert", "tbd", "your name", "{", "<"):
        assert leftover not in text.lower(), (placeholder, leftover)
    assert model.count("cover") == 2
    assert any("placeholder" in w for w in result.claim_warnings)
    assert any("removed" in note for note in result.notes)
    assert _GOOD[0] in text


def _long_letter(words_per_paragraph: int) -> str:
    sentence = "I enjoy building reliable data pipelines for Kafka and Snowflake. "  # 10 words
    return _paragraphs(*[sentence * (words_per_paragraph // 10)] * 6)


async def test_a_letter_that_is_still_too_long_after_the_repair_is_not_printed() -> None:
    too_long = _long_letter(120)  # six paragraphs of 120 words: 720 words, about two pages
    model = ScriptedModel.faithful(cover=[too_long, too_long])

    result = await _letter(model)

    assert model.count("cover") == 2
    assert result.fallback and result.content.highlights
    assert any(f"longer than {MAX_WORDS} words" in note for note in result.notes)
    assert result.content.words() < MAX_WORDS
    assert f"must be under {MAX_WORDS}" in model.calls[-1].user  # the repair was told


async def test_a_letter_the_repair_shortens_is_used() -> None:
    model = ScriptedModel.faithful(cover=[_long_letter(120), COVER_ANSWER])

    result = await _letter(model)

    assert result.content.opening == _GOOD and not result.fallback


async def test_a_letter_of_exactly_the_most_words_is_kept() -> None:
    sentence = "I enjoy building reliable data pipelines for Kafka and Snowflake. "
    paragraphs = [sentence * 7] * 6  # 6 x 70 words = exactly MAX_WORDS
    model = ScriptedModel.faithful(cover=[_paragraphs(*paragraphs)])

    result = await _letter(model)

    assert result.content.words() == MAX_WORDS == 420
    assert not result.fallback and model.count("cover") == 1


async def test_only_the_first_six_paragraphs_of_a_letter_are_read() -> None:
    sentence = "I enjoy building reliable data pipelines for Kafka and Snowflake. "
    lie = "I led the Kubernetes migration at Google."
    seven = _paragraphs(*[sentence * 3] * 6, lie)
    model = ScriptedModel.faithful(cover=[seven])

    result = await _letter(model)

    # the seventh paragraph is never read, so its lie is neither found nor printed
    assert len(result.content.opening) == 6 and "Google" not in _text(result)
    assert result.claim_warnings == [] and model.count("cover") == 1


def _words_of(count: int) -> str:
    """A sentence of exactly `count` words, all of them plain."""
    filler = [
        "I",
        "enjoy",
        "building",
        "reliable",
        "data",
        "pipelines",
        "for",
        "Kafka",
        "and",
        "Snowflake",
    ]
    return " ".join((filler * 20)[: count - 1]) + " too."


@pytest.mark.parametrize(("count", "kept"), [(24, False), (25, True), (69, True), (70, True)])
async def test_a_letter_that_lost_nothing_is_kept_from_twenty_five_words_up(
    count: int, kept: bool
) -> None:
    letter = _paragraphs(_words_of(count))
    model = ScriptedModel.faithful(cover=[letter])

    result = await _letter(model)

    assert (not result.fallback) is kept, count
    if kept:
        assert result.content.words() == count and model.count("cover") == 1


@pytest.mark.parametrize(("clean", "kept"), [(69, False), (70, True)])
async def test_a_letter_that_lost_a_sentence_is_kept_from_seventy_words_up(
    clean: int, kept: bool
) -> None:
    lie = "I led the Kubernetes migration at Google."
    letter = _paragraphs(_words_of(clean) + " " + lie)
    model = ScriptedModel.faithful(cover=[letter, letter])

    result = await _letter(model)

    assert (not result.fallback) is kept, clean
    assert "Google" not in _text(result)
    if kept:
        assert result.content.words() == clean


def test_the_word_counts_a_letter_is_judged_by_are_the_documented_ones() -> None:
    assert (cover_module.SHORT_OK_WORDS, MIN_WORDS, MAX_WORDS) == (25, 70, 420)


async def test_a_short_letter_that_lost_a_sentence_is_replaced_even_with_thirty_clean_words() -> (
    None
):
    thirty = (
        "I am applying for the Staff Data Engineer role at Globex Corporation. My Kafka work at "
        "Northwind Labs is a close match for the streaming platforms your team builds, and I "
        "enjoy that work a great deal."
    )
    lie = "I led the Kubernetes migration at Google."
    kept = ScriptedModel.faithful(cover=[_paragraphs(thirty)])
    replaced = ScriptedModel.faithful(cover=[_paragraphs(thirty + " " + lie)] * 2)

    fine = await _letter(kept)
    swapped = await _letter(replaced)

    assert 25 <= fine.content.words() < MIN_WORDS and not fine.fallback
    assert swapped.fallback and "Google" not in _text(swapped)
