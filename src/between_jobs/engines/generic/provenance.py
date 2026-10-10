"""The fabrication check: does a sentence the model wrote say anything its source does not?

Pure code, no model, run on every answer before it reaches a document. A model is asked to
reword what a candidate wrote about themselves; this module is what makes "reword" more than a
request. It pulls the *fact tokens* out of the new text and requires every one to appear,
whole-word, in the text it was written from (the cited source bullets plus the entry's own
skills, tools, metrics, title and company -- or, for a summary or cover letter, the whole
profile).

Fact tokens are the things a reader would take as a claim of record:

- numbers, with their unit and any "+": `40%`, `$1.2M`, `3x`, `10k+`, `2019`. A model that
  writes "40%" where the source says "40%" is fine; "45%", "4.0%", "40x" or "40%+" is not, and
  neither is a number the source does not have at all. Spelled-out numbers in the SOURCE count
  ("twelve" supports "12"); a number written out in the new text is not checked, so a model
  cannot be caught writing "fifty percent" for "40%";
- units of time and size (`ms`, `40 hours`, `daily`, `per year`, `GB`) and the dollar sign: a
  unit or period word that follows a number, or "per", "each" or "every" (or says how often by
  itself: "daily", "annual"), must be one the source also uses, under any spelling ("yr" for
  "years", "daily" for "per day"), so "$120K per year" cannot become "$120K per month". "In
  recent years" and "this week" follow no number and say nothing about a metric: they are not read;
- acronyms (`AWS`, `ETL`), CamelCase names (`PyTorch`, `GitHub`), names with digits (`k8s`,
  `p95`) and the languages whose names are a letter or a symbol (`R`, `C`, `C#`, `C++`);
- capitalized words: products, employers, places (`Kubernetes`, `Acme`, `Berlin`). A word that
  starts a sentence could equally be a verb, so there it is checked only if it is not an ordinary
  opener (`lexicon.ordinary_opener`: function words, the verbs a resume opens with, and a few
  hundred ordinary nouns and adjectives). "Stripe adopted the approach" is found; "Delivered the
  importer" and "Senior backend engineer" are not;
- skills the curated table in `api/skills.py` knows, under any spelling (`k8s` supports
  `Kubernetes` and the other way round; the table's product names -- `LangChain`, `Airflow`,
  `Terraform` -- are supported only by themselves, never by the broader concept they sit under);
- terms the job posting asked for (its key terms), because stuffing the posting's keywords into
  a bullet is the most likely way to make one false;
- the verbs and nouns of leadership, ownership, mentoring and founding ("led", "manage",
  "oversee", "owned", "mentor", "founded"), in the past and in the present tense a current role
  uses: new text may use one only if the source already claims that kind of role. Words that
  are also ordinary nouns ("lead", "direct", "head", "own") are read only where they act as a
  verb (opening a sentence, or after "to" or "and");
- words that claim a level ("senior", "principal", "expert", "veteran", and "staff" or "lead"
  heading a title): new text may use one only if the source does;
- characters that are markup, not text (backslash, braces, angle brackets, caret, tilde,
  backtick, pipe, asterisk, underscore), and links, e-mail addresses and bare domains
  ("example.com/apply"), unless the source has that very link.

A summary or a letter is also held to what it says about years: a stated number of years (or "a
decade") must be one the candidate wrote, or, in a letter, no more than the dated experience
adds up to (`tenure_claims`).

Both sides are compared after the same folding to ASCII (`latex.fold_to_ascii`), so a name
cannot slip past the check by an accent ("R\u00e9dis" for "Redis"). The document prints the
letters its renderer can set as they are spelled (`latex.DRAWN_LETTERS`); the comparison ignores
the accent either way.

What this CANNOT catch, and the reader of the output should know it:

- an overstated verb that is not in the groups above ("architected" for "helped with");
- an inflated scale in words ("global", "enterprise-scale", "dozens", "unicorn"), and any
  qualitative claim about an employer or a product in plain lower-case words ("the industry
  leader in streaming"); only the summary stage reads "decade", "dozens", "hundreds" and
  "thousands";
- a causal claim ("which led to growth"), a changed tense or subject;
- a tool written in lower case that is neither in the skills table nor one of the posting's key
  terms ("redis" where the source has none);
- a unit or period the source also uses somewhere else: the check asks whether the source has the
  unit at all, not whether it sits next to the number it was moved to ("40 minutes" and "10
  hours" in the source let "40 hours" through);
- "over", "more than", "nearly" and the like in front of a number the source has, and a number
  written out in the new text ("fifty percent");
- a name that starts a sentence and looks like an ordinary opener (it ends in "-ed", "-ing",
  "-ly", "-tion" and the like, or is a word on the opener list), and a link or tool name written
  without any of the patterns above;
- in a summary or a letter, a fact that is true of a different entry.

Containment shows that the new sentence introduces no NEW name, number, unit or tool. It does
not show that the sentence is a faithful paraphrase of the old one. That is why the output keeps
its source pointers, every replaced sentence is reported, and the person reads the document
before it goes anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Literal

from between_jobs.api.skills import AMBIGUOUS_TERMS, SKILL_ALIASES

from .latex import fold_to_ascii
from .lexicon import PERIOD_UNITS, SENIORITY_WORDS, STANDALONE_UNITS, ordinary_opener

FindingKind = Literal[
    "number",
    "name",
    "skill",
    "job_term",
    "scope",
    "level",
    "unit",
    "markup",
    "link",
    "policy",
    "placeholder",
]

_NUMBER_UNITS = {
    "%": "%",
    "percent": "%",
    "per cent": "%",
    "percentage": "%",
    "pct": "%",
    "k": "k",
    "thousand": "k",
    "m": "m",
    "mm": "m",
    "million": "m",
    "b": "b",
    "bn": "b",
    "billion": "b",
    "x": "x",
    "\u00d7": "x",
}

_NUMBER = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:"
    r"(?P<unit>\s?%|\s?(?:percentage|percent|per\s?cent|pct|million|billion|thousand|bn|mm)\b"
    r"|(?:k|m|b|x|\u00d7)(?![A-Za-z0-9]))(?P<plus>\+)?"
    r"|(?P<ordinal>(?:st|nd|rd|th)\b)"
    r"|(?P<bare_plus>\+)"
    r"|(?![A-Za-z0-9])"
    r")",
    re.IGNORECASE,
)
_RUN = re.compile(r"[A-Za-z0-9]+")

_WORD_NUMBERS = {
    word: str(value)
    for value, word in enumerate(
        [
            "zero",
            "one",
            "two",
            "three",
            "four",
            "five",
            "six",
            "seven",
            "eight",
            "nine",
            "ten",
            "eleven",
            "twelve",
            "thirteen",
            "fourteen",
            "fifteen",
            "sixteen",
            "seventeen",
            "eighteen",
            "nineteen",
            "twenty",
        ]
    )
}
_WORD_NUMBERS.update(
    {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70"}
    | {"eighty": "80", "ninety": "90", "hundred": "100", "dozen": "12"}
)

_LEADERSHIP_NEW = frozenset(
    {
        "led",
        "leading",
        "manage",
        "manages",
        "managed",
        "managing",
        "directs",
        "directed",
        "directing",
        "headed",
        "heading",
        "oversee",
        "oversees",
        "oversaw",
        "overseeing",
        "supervise",
        "supervises",
        "supervised",
        "supervising",
        "spearhead",
        "spearheads",
        "spearheaded",
        "spearheading",
        "owns",
        "owned",
        "owning",
        "ownership",
        "owner",
    }
)
_LEADERSHIP_SOURCE = _LEADERSHIP_NEW | {
    "lead",
    "leads",
    "leader",
    "leaders",
    "leadership",
    "manager",
    "managers",
    "management",
    "direct",
    "director",
    "directors",
    "head",
    "heads",
    "supervisor",
    "supervisors",
    "principal",
    "own",
    "owners",
}
_MENTORING_NEW = frozenset(
    {"mentored", "mentoring", "mentor", "mentors", "coached", "coaching", "coach", "coaches"}
)
_MENTORING_SOURCE = _MENTORING_NEW | {"mentorship"}
_FOUNDING_NEW = frozenset({"founded", "cofounded", "founder", "cofounder"})
_FOUNDING_SOURCE = _FOUNDING_NEW | {"found", "founding", "founders", "cofounders"}

_SCOPE_GROUPS: tuple[tuple[str, frozenset[str], frozenset[str]], ...] = (
    ("leadership", _LEADERSHIP_NEW, _LEADERSHIP_SOURCE),
    ("mentoring", _MENTORING_NEW, _MENTORING_SOURCE),
    ("founding", _FOUNDING_NEW, _FOUNDING_SOURCE),
)

_VERB_ONLY_LEADERSHIP = frozenset({"lead", "leads", "direct", "head", "heads", "own"})
"""Leadership words that are also ordinary nouns and adjectives ("lead time", "a direct API",
"their own", "head of"). They are read as a claim only where they act as a verb."""

_VERB_POSITION = re.compile(r"\b(?:and|to)[\s,]+$")

_TITLE_LEVEL = re.compile(
    r"\b(?P<word>staff|lead)(?:-level|[- ]\w+(?:er|or|ist|ant|ect))\b|\b(?P<arch>architect)\b"
)
"""A level named by the head of a title: "staff engineer", "lead-level", "lead developer",
"architect". Bare "staff" and "lead" are ordinary words ("support staff", "lead time")."""

_PRODUCT_ALIASES = frozenset(
    {"langchain", "llamaindex", "airflow", "terraform", "embeddings", "vector database"}
)
"""Aliases in `SKILL_ALIASES` that name one product (or one technique) filed under a broader
concept: "LangChain" under "LLM Orchestration", "Airflow" under "Data Pipelines". A candidate who
wrote "data pipelines" has not said "Airflow", so these are supported only by their own spelling.
(tests/test_generic_engine_provenance.py checks every name here is still in the table.)"""

_GENERIC_JOB_TERMS = frozenset(
    {
        "experience",
        "team",
        "teams",
        "work",
        "working",
        "skills",
        "skill",
        "ability",
        "strong",
        "knowledge",
        "years",
        "year",
        "role",
        "company",
        "environment",
        "fast",
        "paced",
        "good",
        "great",
        "excellent",
        "required",
        "preferred",
        "plus",
        "senior",
        "junior",
    }
)
"""Words so ordinary that seeing one in a bullet says nothing about the job posting."""

_MARKUP_CHARACTERS = "\\{}<>^~`|*_"
_TLDS = (
    "com|net|org|io|ai|dev|app|co|ly|xyz|me|info|biz|edu|gov|tech|cloud|site|online|page|link|us|uk"
)
_LINK = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|[\w.+-]{1,64}@[\w-]{1,255}\.[\w.-]{1,255}"
    r"|(?<![\w@.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.){1,8}(?:" + _TLDS + r")\b(?:/\S*)?",
    re.IGNORECASE,
)
"""A web address, an e-mail address, or a bare domain ("example.com/apply") whose ending is a
common top-level domain. The ending list is closed on purpose: "Node.js", "main.py" and
"README.md" are file and framework names, not addresses. Every part is length-bounded, so a long
unbroken token cannot make the search slow."""

_SYMBOL_NAME = re.compile(r"(?<![A-Za-z0-9])[A-Za-z](?:\+\+|#)")
_ONE_LETTER = re.compile(r"(?<![A-Za-z0-9+#./'-])[B-HJ-Z](?![A-Za-z0-9+#/'-])")
"""A one-letter name ("R", "C"). `A` and `I` are left out (they are words), and a letter joined
to its neighbours by "/" or "-" ("I/O", "X-ray", "A/B") is part of a longer token."""

_DAY_TO_DAY = re.compile(r"\bday[- ]to[- ]day\b")
_COUNT_WORDS = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
_UNIT_FOLLOWS = re.compile(
    r"(?:\d[\d.,]*[kmb]?\+?\s+an?|\d[\d.,]*[kmb]?\+?|\b(?:per|each|every)|/|\b(?:"
    + _COUNT_WORDS
    + r"))[\s-]*$"
)
"""What may stand just before a unit word for it to count as one: a number ("40 hours", "2M+
events", "$120K a year"), a number word ("two weeks"), "per", "each", "every" or a slash."""
_SECOND_UNIT = re.compile(r"\b(?:per|each|every|one|\d+)\s+second\b")
"""The singular "second" is a unit only after "per", "each", "every" or a count: "the second
iteration" is an ordinal."""
_DOLLAR_WORDS = frozenset({"usd", "dollar", "dollars"})


# Skill phrases the curated table knows, as one pattern. Two-letter aliases ("cv", "ts", "py")
# are left to the acronym rule (and "c#" to the symbol rule below), and the aliases that are also
# ordinary words ("react", "prefect") are only checked in their capitalized form, by the name rule.
_CANONICAL_BY_PHRASE: dict[str, str] = {
    alias: name for alias, (name, _) in SKILL_ALIASES.items() if len(alias) > 2
} | {name.lower(): name for name, _ in SKILL_ALIASES.values() if len(name) > 2}
_PHRASES = sorted(
    (phrase for phrase in _CANONICAL_BY_PHRASE if phrase not in AMBIGUOUS_TERMS),
    key=len,
    reverse=True,
)
_SKILL_PHRASE = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(p) for p in _PHRASES) + r")(?![a-z0-9])"
)


@dataclass(frozen=True, slots=True)
class Evidence:
    """What a piece of new text may lean on, indexed once so each check is a set lookup."""

    numbers: frozenset[tuple[str, str]]
    words: frozenset[str]
    stems: frozenset[str]
    phrases: frozenset[str]
    groups: frozenset[str]
    lowered: str
    characters: frozenset[str]
    units: frozenset[str]
    """The units of time and size the text uses (`year`, `day`, `gigabyte`...)."""
    dollar: bool
    """Whether the text says dollars (a `$`, `USD`, `dollar`)."""
    links: frozenset[str]
    """Every address in the text, normalised (`link_key`)."""
    symbols: frozenset[str]
    """The `C++`, `C#`, `F#` style names in the text, lower case."""


@dataclass(frozen=True, slots=True)
class Finding:
    kind: FindingKind
    token: str

    def describe(self, where: str) -> str:
        """Why this token is a problem, for a warning and for a repair prompt. `where` names the
        text it should have come from ("the source bullet(s)", "your profile")."""
        match self.kind:
            case "number":
                return f'the number "{self.token}" is not in {where}'
            case "skill":
                return f'the skill "{self.token}" is not in {where}'
            case "job_term":
                return f'"{self.token}" is a term from the job posting that {where} does not have'
            case "scope":
                return f'"{self.token}" claims a role that {where} does not state'
            case "level":
                return f'"{self.token}" claims a level of seniority that {where} does not state'
            case "unit":
                return f'the unit "{self.token}" is not in {where}'
            case "markup":
                return f"it contains the character {self.token!r}, which {where} does not have"
            case "link":
                return f"it contains a link or address that {where} does not have"
            case "policy":
                return f"it says something about {self.token}, which a letter never states"
            case "placeholder":
                return "it contains a placeholder or bracketed text"
            case _:
                return f'"{self.token}" is not in {where}'


def _lower(text: str) -> str:
    """`text.lower()` that never changes its length (a few characters lower-case to two), so a
    position found in the lowered text is the same position in the original."""
    return "".join(char.lower() if len(char.lower()) == 1 else char for char in text)


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "xes", "ches", "shes", "uses")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def link_key(raw: str) -> str:
    """An address in the form two spellings of it share: lower case, no scheme, no `www.`, no
    closing punctuation or slash."""
    key = raw.lower().rstrip(".,;:!?)\"'")
    key = re.sub(r"^https?://", "", key)
    return re.sub(r"^www\.", "", key).rstrip("/")


def _number_token(match: re.Match[str]) -> tuple[str, str]:
    raw = match.group("num").replace(",", "")
    try:
        core = format(Decimal(raw).normalize(), "f")
    except InvalidOperation:
        core = raw
    if match.group("ordinal"):
        return core, "ord"
    unit = (match.group("unit") or "").strip().lower()
    plus = "+" if match.group("plus") or match.group("bare_plus") else ""
    return core, _NUMBER_UNITS.get(re.sub(r"\s+", " ", unit), "") + plus


def _numbers_in(text: str) -> tuple[list[tuple[str, str]], list[tuple[int, int]]]:
    tokens: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []
    for match in _NUMBER.finditer(text):
        tokens.append(_number_token(match))
        spans.append(match.span())
    return tokens, spans


def evidence_of(*texts: str) -> Evidence:
    """The union of what `texts` say, as the lookups `check` needs."""
    # Compared with every accent folded away: "Z\u00fcrich" and "Zurich" are one word, and
    # "R\u00e9dis" is not a way to write "Redis" past the check.
    joined = fold_to_ascii("\n".join(texts))
    lowered = _lower(joined)
    numbers, _ = _numbers_in(joined)
    exact = {run.lower() for run in _RUN.findall(joined)}
    numbers.extend((value, "") for word, value in _WORD_NUMBERS.items() if word in exact)
    phrases = {match.group(0) for match in _SKILL_PHRASE.finditer(lowered)}
    groups = {
        name
        for name, _new, source_words in _SCOPE_GROUPS
        if any(word in exact for word in source_words)
    }
    return Evidence(
        numbers=frozenset(numbers),
        words=frozenset(exact),
        stems=frozenset(_stem(word) for word in exact),
        phrases=frozenset(phrases),
        groups=frozenset(groups),
        lowered=lowered,
        characters=frozenset(joined),
        units=frozenset(
            {PERIOD_UNITS[word] for word in exact if word in PERIOD_UNITS}
            | ({"second"} if _SECOND_UNIT.search(lowered) else set())
        ),
        dollar="$" in joined or bool(exact & _DOLLAR_WORDS),
        links=frozenset(link_key(match.group(0)) for match in _LINK.finditer(lowered)),
        symbols=frozenset(match.group(0).lower() for match in _SYMBOL_NAME.finditer(joined)),
    )


@lru_cache(maxsize=2048)
def _phrase_pattern(term: str) -> re.Pattern[str]:
    """Whole-word pattern for a term, as the plural or the singular of its last word."""
    parts = term.split()
    last = parts[-1]
    forms = {last, _stem(last)}
    ending = "|".join(
        re.escape(form) + "(?:s|es)?" for form in sorted(forms, key=len, reverse=True)
    )
    head = "".join(re.escape(part) + r"\s+" for part in parts[:-1])
    return re.compile(rf"(?<![a-z0-9]){head}(?:{ending})(?![a-z0-9])")


def mentions(term: str, lowered_text: str) -> bool:
    """True if `term` (lowercase) is in `lowered_text` as whole words, plural allowed."""
    return term != "" and _phrase_pattern(term).search(lowered_text) is not None


def usable_job_terms(terms: list[str], limit: int = 40) -> tuple[str, ...]:
    """The job's key terms worth guarding: lowercase, deduplicated, three characters or more,
    not an ordinary word, at most `limit` of them."""
    kept: dict[str, None] = {}
    for term in terms:
        value = " ".join(term.lower().split())
        if len(value) < 3 or len(value) > 40 or value in _GENERIC_JOB_TERMS:
            continue
        kept.setdefault(value)
        if len(kept) >= limit:
            break
    return tuple(kept)


def _skill_supported(phrase: str, evidence: Evidence) -> bool:
    if phrase in evidence.phrases:
        return True
    canonical = _CANONICAL_BY_PHRASE[phrase].lower()
    # The new text says the concept; the source names something filed under it.
    if any(_CANONICAL_BY_PHRASE[other].lower() == phrase for other in evidence.phrases):
        return True
    # The new text uses a spelling of a skill the source spells another way ("k8s" for
    # "Kubernetes") -- but not a product the source only implies by its concept.
    return phrase not in _PRODUCT_ALIASES and canonical in evidence.phrases


def _starts_sentence(text: str, index: int) -> bool:
    before = text[:index].rstrip(" \"'([")
    return before == "" or before[-1] in ".!?:;"


def _word_findings(text: str, blanked: str, evidence: Evidence) -> list[Finding]:
    findings: list[Finding] = []
    for match in _RUN.finditer(blanked):
        run = match.group(0)
        has_digit = any(char.isdigit() for char in run)
        has_letter = any(char.isalpha() for char in run)
        if has_digit and not has_letter:
            continue  # a plain number: the number rule's business
        token = ""
        if has_digit:
            token = run.lower()  # k8s, p95
        elif len(run) >= 3 and run.endswith("s") and run[:-1].isupper():
            token = run[:-1].lower()  # "APIs"
        elif any(char.isupper() for char in run[1:]) or (
            run[0].islower() and any(char.isupper() for char in run)
        ):
            token = run.lower()  # AWS, PyTorch, iOS
        elif run[0].isupper() and len(run) >= 2:
            # A capital that opens a sentence could be a verb or a name; there only a word that
            # is plainly neither a name nor new (an ordinary opener) is let through.
            opens = _starts_sentence(text, match.start())
            if not opens or not ordinary_opener(run.lower()):
                token = run.lower()
        if token and token not in evidence.words and _stem(token) not in evidence.stems:
            findings.append(Finding("name", run))
    return findings


def _symbol_findings(blanked: str, evidence: Evidence) -> list[Finding]:
    """Languages whose names a word rule cannot see: `C++`, `C#`, `F#`, and the one-letter `R`
    and `C`."""
    findings = [
        Finding("name", match.group(0))
        for match in _SYMBOL_NAME.finditer(blanked)
        if match.group(0).lower() not in evidence.symbols
    ]
    findings.extend(
        Finding("name", match.group(0))
        for match in _ONE_LETTER.finditer(blanked)
        if match.group(0).lower() not in evidence.words
    )
    return findings


def _unit_findings(text: str, lowered: str, evidence: Evidence) -> list[Finding]:
    findings: list[Finding] = []
    if "$" in text and not evidence.dollar:
        findings.append(Finding("unit", "$"))
    scanned = _DAY_TO_DAY.sub(" ", lowered)
    if _SECOND_UNIT.search(scanned) and "second" not in evidence.units:
        findings.append(Finding("unit", "second"))
    for match in _RUN.finditer(scanned):
        word = match.group(0)
        unit = PERIOD_UNITS.get(word)
        read = unit is not None and (
            word in STANDALONE_UNITS or _UNIT_FOLLOWS.search(scanned[: match.start()]) is not None
        )
        new_unit = read and unit not in evidence.units
        new_currency = word in _DOLLAR_WORDS and not evidence.dollar
        if new_unit or new_currency:
            findings.append(Finding("unit", word))
    return findings


def _scope_findings(text: str, words: set[str], evidence: Evidence) -> list[Finding]:
    findings: list[Finding] = []
    for name, new_words, _source_words in _SCOPE_GROUPS:
        used = sorted(words & new_words)
        if name == "leadership" and "leadership" not in evidence.groups:
            used += _verb_position_words(text)
        if used and name not in evidence.groups:
            findings.append(Finding("scope", used[0]))
    return findings


def _verb_position_words(text: str) -> list[str]:
    """The ambiguous leadership words in `text` that act as verbs: the first word of a sentence,
    or the one after "to" or "and"."""
    lowered = _lower(text)
    found: list[str] = []
    for match in _RUN.finditer(text):
        word = match.group(0).lower()
        if word not in _VERB_ONLY_LEADERSHIP:
            continue
        if _starts_sentence(text, match.start()) or _VERB_POSITION.search(lowered[: match.start()]):
            found.append(word)
    return found


def _level_findings(lowered: str, words: set[str], evidence: Evidence) -> list[Finding]:
    claimed = sorted(words & SENIORITY_WORDS)
    claimed += [m.group(0) for m in _TITLE_LEVEL.finditer(lowered)]
    findings: list[Finding] = []
    for claim in claimed:
        head = claim.split("-")[0].split(" ")[0]
        if head not in evidence.words and _stem(head) not in evidence.stems:
            findings.append(Finding("level", claim))
    return findings


def check(text: str, evidence: Evidence, job_terms: tuple[str, ...] = ()) -> list[Finding]:
    """Every fact token in `text` that `evidence` does not support, without repeats."""
    text = fold_to_ascii(text)
    findings: list[Finding] = []

    numbers, spans = _numbers_in(text)
    for token, span in zip(numbers, spans, strict=True):
        # a "+" claims more than the bare number; the source's "10+" supports a plain "10"
        supported = token in evidence.numbers or (
            not token[1].endswith("+") and (token[0], token[1] + "+") in evidence.numbers
        )
        if not supported:
            findings.append(Finding("number", text[span[0] : span[1]].strip()))

    lowered = _lower(text)
    taken = list(spans)
    for match in _SKILL_PHRASE.finditer(lowered):
        taken.append(match.span())
        if not _skill_supported(match.group(0), evidence):
            findings.append(Finding("skill", text[match.start() : match.end()]))

    blanked_chars = list(text)
    for start, end in taken:
        for index in range(start, end):
            blanked_chars[index] = " "
    blanked = "".join(blanked_chars)
    findings.extend(_word_findings(text, blanked, evidence))
    findings.extend(_symbol_findings(blanked, evidence))
    findings.extend(_unit_findings(text, lowered, evidence))

    for term in job_terms:
        if mentions(term, lowered) and not mentions(term, evidence.lowered):
            findings.append(Finding("job_term", term))

    words = {run.lower() for run in _RUN.findall(text)}
    findings.extend(_scope_findings(text, words, evidence))
    findings.extend(_level_findings(lowered, words, evidence))

    for char in _MARKUP_CHARACTERS:
        if char in text and char not in evidence.characters:
            findings.append(Finding("markup", char))
    if any(link_key(match.group(0)) not in evidence.links for match in _LINK.finditer(text)):
        findings.append(Finding("link", "link"))

    unique: dict[tuple[str, str], Finding] = {}
    for finding in findings:
        unique.setdefault((finding.kind, finding.token.lower()), finding)
    return list(unique.values())


# -- years of experience ---------------------------------------------------------------------

_TENS = ("twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
_UNITS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
_NUMBER_WORDS = "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
_TENURE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<first>\d+(?:\.\d+)?|{_NUMBER_WORDS})"
    rf"(?:[\s-]+(?P<second>{'|'.join(_UNITS)}))?\s?(?P<plus>\+)?[\s-]*(?:years?|yrs?)\b",
    re.IGNORECASE,
)
_DECADE = re.compile(r"\b(?:(?P<count>a|an|one|two|three|four|five)\s+)?decades?\b", re.IGNORECASE)
MAGNITUDE_WORDS = frozenset({"decade", "decades", "dozens", "hundreds", "thousands"})
"""Words that state a size or a span without a digit. The summary stage reads them."""


@dataclass(frozen=True, slots=True)
class Tenure:
    """A stated length of time in years: `value` years, written as `text`."""

    value: float
    text: str
    spelled: bool
    """Written in words ("ten years", "a decade") rather than digits."""


def tenure_claims(text: str) -> list[Tenure]:
    """Every "N years" ("12 years", "ten years", "twenty-five years", "10+ yrs") and "decade" in
    `text`. A plain count of years is a claim about a career, which a number check that accepts
    any digit found anywhere in a profile cannot judge."""
    text = fold_to_ascii(text)
    claims: list[Tenure] = []
    for match in _TENURE.finditer(text):
        first = match.group("first").lower()
        spelled = not first[0].isdigit()
        value = float(_WORD_NUMBERS[first] if spelled else first)
        if match.group("second"):
            if first not in _TENS:
                continue  # "five one years" is not a number
            value += float(_WORD_NUMBERS[match.group("second").lower()])
        claims.append(Tenure(value, match.group(0).strip(), spelled))
    for match in _DECADE.finditer(text):
        count = (match.group("count") or "").lower()
        if count in {"a", "an"}:
            decades = 1
        elif count:
            decades = int(_WORD_NUMBERS[count])
        else:  # "decades of experience" says at least two; a bare "decade" says one
            decades = 2 if match.group(0).lower().endswith("s") else 1
        claims.append(Tenure(10.0 * decades, match.group(0).strip(), spelled=True))
    return claims
