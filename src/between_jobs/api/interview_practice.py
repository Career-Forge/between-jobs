"""InterviewForge R2 (interviewforge-v1.md) -- the practice-session engine:
question generation and per-answer scoring.

Lives directly in `between_jobs/api/`, not forge-engines -- same reasoning
`company_intel_pipeline.py`'s own module docstring already states: this
grounds against external company research and scores conversational
answers, neither of which is a resume-generation concern (D5).

Structured, fixed-question-set sessions (D6), not freeform chat -- matches
this codebase's "deterministic code owns structure, LLM chooses words"
convention, and it's the shape that actually benefits from registry
grounding: a question can cite a specific real round/topic from the
registry; a freeform chat has nowhere clean to inject that.

Company-blind vs. company-true (MASTER_PLAN §2.2/§5.6): when
`PracticeContext.registry_entry` is `None`, question generation falls back
to resume+JD only, honestly labeling every question `grounded_in: None` --
the real "company-blind" state the docs describe, not an error. When a
registry entry exists, questions may cite its actual rounds/topics
verbatim, never inventing a round or topic the registry doesn't contain.

Named, honest limitation: unlike `interview_registry.py`'s claim-citation
check (a deterministic backstop -- a claim's `source_url` either was or
wasn't actually retrieved), there is no equivalent deterministic guard here.
"Never introduce a fact not already in the candidate's own answer or resume"
is a prompt-level rule only, the same class of guarantee `cover_pass1.py`/
`cover_pass2.py`'s biographical-grounding rule already is elsewhere in this
project -- not weaker by accident, just not the kind of claim a citation
check can mechanically verify.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Literal, NotRequired, TypedDict, cast

from .interview_registry import InterviewProcessModel
from .llm_client import LLMResponse
from .llm_client import generate as llm_generate

LlmGenerate = Callable[..., Awaitable[LLMResponse]]

QuestionType = Literal["behavioral", "technical", "situational"]
_VALID_QUESTION_TYPES: frozenset[str] = frozenset(("behavioral", "technical", "situational"))

_QUESTION_COUNT = 5
_QUESTION_GENERATION_MAX_TOKENS = 2000
_ANSWER_SCORING_MAX_TOKENS = 800


class PracticeContext(TypedDict):
    company_name: str
    job_title: str
    job_description: str
    resume_evidence: str
    registry_entry: InterviewProcessModel | None


def build_practice_context(
    *,
    company_name: str,
    job_title: str,
    job_description: str,
    resume_evidence: str,
    registry_entry: InterviewProcessModel | None,
) -> PracticeContext:
    return PracticeContext(
        company_name=company_name,
        job_title=job_title,
        job_description=job_description,
        resume_evidence=resume_evidence,
        registry_entry=registry_entry,
    )


class PracticeQuestion(TypedDict):
    question: str
    type: QuestionType
    target_skill: str
    grounded_in: str | None
    """The exact registry round/topic string this question is based on, or
    `None` when generated company-blind (no registry entry) or when nothing
    in a real registry entry actually supported it -- never a paraphrase or
    an invented citation."""


_QUESTION_GENERATION_SYSTEM_PROMPT = f"""You generate mock-interview practice questions for a candidate preparing for a specific role. Return ONLY valid JSON (no markdown, no explanations).

You will be given the job's company, title, and description, the candidate's own resume evidence, and -- if available -- REAL, cited facts about that company's actual interview process (a registry entry). Use the registry entry when given; when it's absent or empty, generate strong general questions from the job and resume alone.

Return exactly this schema:
{{
  "questions": [
    {{ "question": "<the interview question, addressed to the candidate>", "type": "<behavioral|technical|situational>", "target_skill": "<short label for what this question assesses>", "grounded_in": "<the EXACT round name or topic string from the registry entry this question is based on, verbatim, OR null if this question is not based on any real registry data>" }}
  ]
}}

Generate exactly {_QUESTION_COUNT} questions, covering a mix of behavioral, technical, and situational types appropriate to the role.

CRITICAL RULES:
- If a registry entry is given and has real rounds/typical_topics, weight your questions toward what it actually says the company evaluates -- and set "grounded_in" to that EXACT string from the registry. NEVER invent a round, topic, or difficulty claim the registry entry doesn't actually contain.
- If no registry entry is given, or it has no rounds and no typical_topics, EVERY question must have "grounded_in": null -- do not claim knowledge of this company's real interview process when none was given to you.
- Technical questions should be genuinely answerable given the candidate's own resume evidence -- do not ask about a technology or domain nowhere in their background.
- Never fabricate a fact about the company beyond what's in the job description or registry entry given to you."""  # noqa: E501


def _build_question_generation_user(context: PracticeContext) -> str:
    registry = context["registry_entry"]
    payload = {
        "company": context["company_name"],
        "job_title": context["job_title"],
        "job_description": context["job_description"],
        "resume_evidence": context["resume_evidence"],
        "registry_entry": dict(registry) if registry else None,
    }
    return json.dumps(payload)


async def generate_practice_questions(
    context: PracticeContext,
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate | None = None,
) -> list[PracticeQuestion]:
    """Returns an empty list -- never raises -- when nothing usable comes
    back. A session with zero questions is a real failure state; deciding
    what to do about it (retry, surface an error) belongs to the caller,
    same as `company_intel_pipeline.synthesize_dossier`'s own empty-list
    contract."""
    call = generate or llm_generate
    response = await call(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_QUESTION_GENERATION_SYSTEM_PROMPT,
        user_prompt=_build_question_generation_user(context),
        max_tokens=_QUESTION_GENERATION_MAX_TOKENS,
    )
    return _parse_questions(response.content)


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]
    return stripped


def _parse_questions(raw: str) -> list[PracticeQuestion]:
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, dict):
        return []

    questions: list[PracticeQuestion] = []
    for item in parsed.get("questions") or []:
        if not isinstance(item, dict):
            continue
        question_text = item.get("question")
        if not question_text or not isinstance(question_text, str):
            continue
        target_skill = item.get("target_skill")
        grounded_in = item.get("grounded_in")

        questions.append(
            PracticeQuestion(
                question=question_text,
                type=coerce_question_type(item.get("type")),
                target_skill=target_skill if isinstance(target_skill, str) else "",
                grounded_in=grounded_in if isinstance(grounded_in, str) and grounded_in else None,
            )
        )
    return questions


def coerce_question_type(value: object) -> QuestionType:
    """Defaults anything unrecognized to "behavioral" -- shared by fresh LLM
    parsing (`_parse_questions`) and by `interview_practice_routes.py`
    reconstructing a `PracticeQuestion` from a previously-persisted row, so
    the one validation rule lives in one place."""
    return cast("QuestionType", value if value in _VALID_QUESTION_TYPES else "behavioral")


class StarCoverage(TypedDict):
    situation: bool
    task: bool
    action: bool
    result: bool


class AnswerFeedback(TypedDict):
    score: int
    """0-10, clamped -- never trusted verbatim from the model's own output."""
    structure_feedback: str
    specificity_feedback: str
    star_coverage: StarCoverage
    improved_answer: NotRequired[str]


_ANSWER_SCORING_SYSTEM_PROMPT = """You score a candidate's answer to a mock-interview question. Return ONLY valid JSON (no markdown, no explanations).

You will be given the question, the candidate's answer, and the candidate's own resume evidence (for context only -- never introduce a fact from it that the candidate's ANSWER didn't itself draw on).

Return exactly this schema:
{
  "score": <integer 0-10>,
  "structure_feedback": "<one or two sentences on how well-organized the answer was>",
  "specificity_feedback": "<one or two sentences on whether the answer used concrete details vs vague generalities>",
  "star_coverage": { "situation": <boolean>, "task": <boolean>, "action": <boolean>, "result": <boolean> },
  "improved_answer": "<a rewritten, more effective version of the SAME answer>"
}

CRITICAL RULES:
- Score and feedback must be grounded ONLY in what the candidate's answer actually said -- never reward or penalize based on assumptions about what they "probably" meant.
- "improved_answer" may restructure, tighten, or better-frame what the candidate already said -- it must NEVER introduce a new achievement, metric, technology, or fact that wasn't already present in the candidate's own answer or resume evidence. A more articulate answer, not a more impressive fictional one.
- star_coverage reflects only what's actually present in the answer -- do not mark a component true because the candidate could plausibly have meant it."""  # noqa: E501


def _build_answer_scoring_user(
    question: PracticeQuestion, answer_text: str, resume_evidence: str
) -> str:
    payload = {
        "question": question["question"],
        "question_type": question["type"],
        "target_skill": question["target_skill"],
        "answer": answer_text,
        "resume_evidence": resume_evidence,
    }
    return json.dumps(payload)


async def score_answer(
    question: PracticeQuestion,
    answer_text: str,
    resume_evidence: str,
    *,
    llm_api_key: str,
    llm_model: str,
    llm_base_url: str | None,
    generate: LlmGenerate | None = None,
) -> AnswerFeedback | None:
    """Returns `None` -- not a guessed/default score -- when the response
    can't be parsed into a real score. A fabricated 5/10 would mislead the
    candidate exactly as much as a fabricated claim would; the caller
    decides what "couldn't score this" means for the session (retry,
    surface an honest error), same reasoning as `generate_practice_
    questions`' own empty-list contract."""
    call = generate or llm_generate
    response = await call(
        api_key=llm_api_key,
        model=llm_model,
        base_url=llm_base_url,
        system_prompt=_ANSWER_SCORING_SYSTEM_PROMPT,
        user_prompt=_build_answer_scoring_user(question, answer_text, resume_evidence),
        max_tokens=_ANSWER_SCORING_MAX_TOKENS,
    )
    return _parse_feedback(response.content)


def _parse_feedback(raw: str) -> AnswerFeedback | None:
    try:
        parsed = json.loads(_strip_code_fence(raw))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None

    raw_score = parsed.get("score")
    if not isinstance(raw_score, int | float) or isinstance(raw_score, bool):
        return None
    score = max(0, min(10, round(raw_score)))

    star_raw = parsed.get("star_coverage")
    star_raw = star_raw if isinstance(star_raw, dict) else {}
    star_coverage = StarCoverage(
        situation=bool(star_raw.get("situation")),
        task=bool(star_raw.get("task")),
        action=bool(star_raw.get("action")),
        result=bool(star_raw.get("result")),
    )

    structure_feedback = parsed.get("structure_feedback")
    specificity_feedback = parsed.get("specificity_feedback")
    improved_answer = parsed.get("improved_answer")

    feedback = AnswerFeedback(
        score=score,
        structure_feedback=structure_feedback if isinstance(structure_feedback, str) else "",
        specificity_feedback=(
            specificity_feedback if isinstance(specificity_feedback, str) else ""
        ),
        star_coverage=star_coverage,
    )
    if isinstance(improved_answer, str) and improved_answer:
        feedback["improved_answer"] = improved_answer
    return feedback


class ScoredAnswer(TypedDict):
    question: PracticeQuestion
    answer_text: str
    feedback: AnswerFeedback


class SessionReport(TypedDict):
    question_count: int
    answered_count: int
    average_score: float | None
    star_coverage_rate: float | None
    """Fraction of scored answers with all four STAR components present --
    `None`, not `0.0`, when nothing has been scored yet."""


def build_session_report(total_questions: int, scored_answers: list[ScoredAnswer]) -> SessionReport:
    """Deterministic aggregation -- no LLM call. The per-answer scores are
    already real numbers by the time this runs; averaging them is
    arithmetic, not something worth spending a model invocation on."""
    answered_count = len(scored_answers)
    if answered_count == 0:
        return SessionReport(
            question_count=total_questions,
            answered_count=0,
            average_score=None,
            star_coverage_rate=None,
        )

    scores = [a["feedback"]["score"] for a in scored_answers]
    full_star_count = sum(1 for a in scored_answers if all(a["feedback"]["star_coverage"].values()))
    return SessionReport(
        question_count=total_questions,
        answered_count=answered_count,
        average_score=round(sum(scores) / answered_count, 2),
        star_coverage_rate=round(full_star_count / answered_count, 2),
    )
