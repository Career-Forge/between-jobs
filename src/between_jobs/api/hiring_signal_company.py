"""Hiring Signals P3 -- what a company is CALLED, in a query and in a post.
Pure and deterministic: no I/O, no clock, no LLM.

The provider query and the hard "is this post about the company" filter used
to read the name through the job registry's normalizer
(`company_tiers.normalize_company_name`: `&` becomes `and`, dots and
apostrophes are deleted, legal-form words are stripped from anywhere in the
string). That form is right for looking a company up in a table and wrong for
this feature, in two ways that real names exposed:

- **The two sides tokenized differently.** The query said `atandt`, `amazoncom
  services` and `procter and gamble`; a post tokenized as words says `AT&T` (a
  token `at`, a token `t`), `Amazon.com` and `Procter & Gamble` (no `and`
  token at all). Names with an ampersand, a dot or an apostrophe therefore
  never matched their own posts, and the provider was asked for a string real
  posts do not contain.
- **A name is not one string.** People write `Meta`, not `Meta Platforms, Inc.`;
  `ScaleAI` as often as `Scale AI`; `AMD` for `Advanced Micro Devices (AMD)`;
  `Nestle` for `Nestlé`, in either Unicode normalization form; and a name in a
  script written without spaces (`楽天`) is never a whole "word" of the sentence
  around it.

So this module gives the query and the matcher ONE reading of a name:

- `fold` -- the comparison form of any text: NFKC, case-folded, Latin
  diacritics removed (the combining block only, so Indic vowel signs survive),
  `&` read as `and`, a possessive `'s` dropped (`Stripe's` -> `stripe`, so
  "Stripe's team is hiring" is about Stripe), any other apostrophe inside a
  word removed (`L'Oreal` -> `loreal`), a dotted abbreviation run together
  (`S.A.` -> `sa`). Text and names both go through it, so the two can only
  differ in what they SAY. A name that itself ends in `'s` (`McDonald's`) also
  gets the spelling people write without the apostrophe (`mcdonalds`).
- `company_names` -- the query phrase (the display name minus legal forms and
  `Group`/`Holdings` tails, keeping `&`, dots and apostrophes) and the set of
  **variants** a post may use: the name without its legal form and a leading
  `the`, without a descriptor tail (`Meta Platforms` -> `Meta`), without a
  `.com` tail, run together (`Scale AI` -> `scaleai`), and a short
  parenthetical alias (`Advanced Micro Devices (AMD)` -> `AMD`).
- `CompanyNames.found_in` -- whether a text contains any variant as whole words
  (a variant of spaced-script words must not touch another word on either
  side; a variant in a script written without spaces is looked for as a
  substring, because there is no word boundary to respect).
- `CompanyNames.is_page_name` -- whether a display name or a vanity handle is
  the company's own page (the name, or the name plus `Careers`/`Jobs`/...),
  as opposed to a person or another firm whose name merely CONTAINS a word of
  it (`Jordan Block` is not Block).
- `CompanyNames.identity_keys` -- the run-together forms that mean "this
  company", for matching a LinkedIn page against a registry company that may be
  stored under a slug (`scaleai`) or a legal name.

What this does not do: it has no alias table, so `AMD` is not `Advanced Micro
Devices` unless the name says so; and it cannot tell a company that is also an
ordinary word (`Block`, `Apple`, `Target`) from the word -- that limit is
stated in `hiring_signal_relevance`, where the position rules that soften it
live.

(Source note: the character classes below are built from code points with
`chr()` on purpose. A source file should never carry literal combining marks,
bidi controls or invisible characters, and a `\\u` escape is the kind of thing
that gets silently decoded on its way through tooling.)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_MAX_COMPANY_CHARS = 200
_MAX_QUERY_TERM_CHARS = 60
_MAX_QUERY_WORDS = 5

LEGAL_FORMS = frozenset(
    {
        "inc",
        "incorporated",
        "llc",
        "llp",
        "lp",
        "ltd",
        "limited",
        "corp",
        "corporation",
        "co",
        "company",
        "plc",
        "gmbh",
        "ag",
        "sa",
        "sas",
        "srl",
        "spa",
        "bv",
        "nv",
        "pvt",
        "private",
        "pty",
        "oy",
        "ab",
        "kk",
        "pllc",
    }
)
"""Corporate-form words. Taken off the END of a name, one after another, but
never the last word left (a company literally called `Co` keeps it)."""

_QUERY_TAILS = LEGAL_FORMS | {"group", "holdings", "holding"}
"""What is taken off the end of the QUERY phrase. `normalize_company_name`
removed `group` and `holdings` too, and posts about `Goldman Sachs Group` say
`Goldman Sachs`."""

DESCRIPTORS = frozenset(
    {
        "platforms",
        "platform",
        "services",
        "technologies",
        "technology",
        "group",
        "holdings",
        "holding",
        "international",
        "solutions",
        "systems",
        "consulting",
        "software",
        "labs",
        "industries",
        "enterprises",
        "partners",
        "associates",
        "global",
        "worldwide",
        "laboratories",
        "pharmaceuticals",
        "investments",
    }
)
"""Words a name may end in that people leave off (`Meta Platforms`, `Cisco
Systems`). They yield an extra, SHORTER variant; the full name stays a variant
too."""

PAGE_SUFFIXES: tuple[tuple[str, ...], ...] = (
    ("careers",),
    ("jobs",),
    ("talent",),
    ("recruiting",),
    ("recruitment",),
    ("hiring",),
    ("talent", "acquisition"),
    ("early", "careers"),
    ("university", "recruiting"),
)
"""What a company's own recruiting page adds to the company's name."""

_ALIAS_NOISE = frozenset({"formerly", "previously", "fka", "aka", "now", "the", "a", "an", "and"})

# Scripts written without spaces between words. A run of them is one regex
# "word", so a name in one can neither be split out of its sentence by word
# boundaries nor be bounded by them. (Code point ranges: Thai and Lao,
# Myanmar, Khmer, Hiragana and Katakana, Han, Hangul.)
_UNSPACED_RANGES = (
    (0x0E00, 0x0EFF),
    (0x1000, 0x109F),
    (0x1780, 0x17FF),
    (0x3040, 0x30FF),
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0xAC00, 0xD7AF),
)
_UNSPACED = "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _UNSPACED_RANGES)
_UNSPACED_RX = re.compile(f"[{_UNSPACED}]")
_TOKEN_RX = re.compile(f"[{_UNSPACED}]+|[^\\W_{_UNSPACED}]+")
_SPACED_WORD_CHAR = f"[^\\W_{_UNSPACED}]"
_NON_WORD_RX = re.compile(r"[\W_]+")
_APOSTROPHES = "'" + chr(0x2019) + chr(0x02BC) + "`" + chr(0x00B4)
_APOSTROPHE_CLASS = f"[{re.escape(_APOSTROPHES)}]"
_POSSESSIVE_RX = re.compile(rf"(?<=\w){_APOSTROPHE_CLASS}s\b")
_INNER_APOSTROPHE_RX = re.compile(rf"(?<=\w){_APOSTROPHE_CLASS}(?=\w)")
_DOTTED_ABBREVIATION_RX = re.compile(r"(?<![^\W_])((?:[^\W_]\.){2,})")
_PARENTHETICAL_RX = re.compile(r"\(([^()]{1,40})\)")
_COMBINING_DIACRITICS = (0x0300, 0x036F)


def fold(text: str) -> str:
    """The comparison form of `text` (see the module docstring)."""
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    text = _POSSESSIVE_RX.sub("", text)
    text = _INNER_APOSTROPHE_RX.sub("", text)
    text = _DOTTED_ABBREVIATION_RX.sub(lambda m: m.group(1).replace(".", ""), text)
    lo, hi = _COMBINING_DIACRITICS
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not lo <= ord(ch) <= hi)
    return unicodedata.normalize("NFC", stripped)


def tokens_of(text: str) -> list[str]:
    """Word tokens of `fold(text)`: a run of an unspaced script is one token,
    and so is a run of any other letters and digits."""
    return _TOKEN_RX.findall(fold(text))


def has_unspaced_script(text: str) -> bool:
    return _UNSPACED_RX.search(text) is not None


def _strip_tail(tokens: list[str], words: frozenset[str]) -> list[str]:
    """`tokens` without trailing words in `words` and dangling `and`s, but
    never emptied."""
    out = list(tokens)
    while len(out) > 1 and (out[-1] in words or out[-1] == "and"):
        out.pop()
    return out


def _base_tokens(main: str) -> list[str]:
    tokens = tokens_of(main)
    if len(tokens) > 1 and tokens[0] == "the":
        tokens = tokens[1:]
    return _strip_tail(tokens, LEGAL_FORMS)


@dataclass(frozen=True)
class _Variant:
    tokens: tuple[str, ...]
    pattern: re.Pattern[str] | None
    """`None` for a variant in an unspaced script (matched as a substring of
    the whitespace-free text)."""

    @property
    def joined(self) -> str:
        return "".join(self.tokens)


def _variant(tokens: tuple[str, ...]) -> _Variant:
    if any(has_unspaced_script(t) for t in tokens):
        return _Variant(tokens, None)
    body = r"[\W_]+".join(re.escape(t) for t in tokens)
    pattern = re.compile(rf"(?<!{_SPACED_WORD_CHAR})(?:{body})(?!{_SPACED_WORD_CHAR})")
    return _Variant(tokens, pattern)


@dataclass(frozen=True)
class CompanyNames:
    query_name: str
    """The phrase the provider is asked for: the display name as written,
    minus legal forms and `Group`/`Holdings` tails."""
    variants: tuple[_Variant, ...]
    registry_words: tuple[str, ...]
    """Words a registry company's name must contain (a SQL `ilike` superset;
    identity is decided by `identity_keys`)."""

    @property
    def identity_keys(self) -> frozenset[str]:
        """Run-together forms that mean "this company"."""
        return frozenset(v.joined for v in self.variants)

    def found_in(self, text: str) -> bool:
        """Whether some variant of the name appears in `text` as whole words
        (or, for a name in an unspaced script, as a substring)."""
        folded = fold(text)
        squashed: str | None = None
        for variant in self.variants:
            if variant.pattern is None:
                if squashed is None:
                    squashed = _NON_WORD_RX.sub("", folded)
                if variant.joined in squashed:
                    return True
            elif variant.pattern.search(folded) is not None:
                return True
        return False

    def is_page_name(self, name: str) -> bool:
        """Whether `name` (a display name or a vanity handle) is this
        company's own page: exactly the name, or the name plus a recruiting
        suffix (`Acme Careers`). A person or firm whose name only CONTAINS a
        word of the company (`Jordan Block`, `Apple Valley Staffing`) is not."""
        tokens = tokens_of(name)
        if not tokens:
            return False
        candidates = {"".join(tokens)}
        for suffix in PAGE_SUFFIXES:
            if len(tokens) > len(suffix) and tuple(tokens[-len(suffix) :]) == suffix:
                candidates.add("".join(tokens[: -len(suffix)]))
        candidates.add("".join(_strip_tail(tokens, LEGAL_FORMS)))
        return bool(candidates & self.identity_keys)


def _split_aliases(raw: str) -> tuple[str, list[str]]:
    aliases = [m.group(1) for m in _PARENTHETICAL_RX.finditer(raw)]
    main = " ".join(_PARENTHETICAL_RX.sub(" ", raw).split())
    return main, aliases


def _alias_tokens(alias: str) -> tuple[str, ...] | None:
    tokens = tokens_of(alias)
    if not 1 <= len(tokens) <= 3 or tokens[0] in _ALIAS_NOISE:
        return None
    return tuple(tokens)


def _clip_words(text: str, *, max_words: int, max_chars: int) -> str:
    clipped = " ".join(text.split()[:max_words])
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars].rsplit(" ", 1)[0]
    return clipped


def _display_word_key(word: str) -> str:
    return re.sub(r"[.,;]", "", word).casefold()


def _query_phrase(main: str) -> str:
    """The display name without trailing legal-form/`Group` words and the
    `,`/`&` left dangling by them: `Stripe, Inc.` -> `Stripe`, `AT&T Inc.` ->
    `AT&T`, `JPMorgan Chase & Co.` -> `JPMorgan Chase`."""
    words = main.split()
    while len(words) > 1 and (
        _display_word_key(words[-1]) in _QUERY_TAILS or words[-1] in {"&", "and", "And", "AND"}
    ):
        words.pop()
    phrase = " ".join(words).strip(" ,;")
    return _clip_words(phrase, max_words=_MAX_QUERY_WORDS, max_chars=_MAX_QUERY_TERM_CHARS)


def company_names(company: object) -> CompanyNames | None:
    """The names of `company`, or `None` when there is no company to speak
    of: nothing was given, or what was given holds no letter or digit."""
    if not isinstance(company, str):
        return None
    raw = " ".join(company.split())[:_MAX_COMPANY_CHARS]
    main, aliases = _split_aliases(raw)
    base = _base_tokens(main)
    if not base:
        return None
    variants: list[tuple[str, ...]] = []

    def add(tokens: list[str] | tuple[str, ...] | None) -> None:
        if tokens and tuple(tokens) not in variants:
            variants.append(tuple(tokens))

    def add_spellings(tokens: list[str]) -> None:
        add(tokens)
        add(_strip_tail(tokens, DESCRIPTORS))
        for existing in list(variants):
            if len(existing) > 1 and existing[-1] == "com":
                add(existing[:-1])

    add_spellings(base)
    shortest = min(variants, key=len)
    # `McDonald's` folds to `mcdonald`; people also write `McDonalds`
    kept_s = _POSSESSIVE_RX.sub("s", main.casefold())
    if kept_s != main.casefold():
        add_spellings(_base_tokens(kept_s))
    for existing in list(variants):
        if len(existing) > 1:
            add(("".join(existing),))
    for alias in aliases:
        add(_alias_tokens(alias))

    query_name = _query_phrase(main) or main
    if not any(ch.isalnum() for ch in query_name):
        return None
    # The registry read is a SUPERSET fetch (identity is decided afterwards by
    # `identity_keys`), so it uses the shortest spelling of the name.
    registry_words = tuple(t for t in shortest if t != "and")
    return CompanyNames(
        query_name=query_name,
        variants=tuple(_variant(v) for v in variants),
        registry_words=registry_words,
    )


def identity_keys_of(name: str) -> frozenset[str]:
    """`CompanyNames.identity_keys` for an arbitrary company name -- and for
    the part before a ` - ` tagline as well (LinkedIn page names carry one:
    `Aquascape Engineers Pvt Ltd - Aerospace`). Empty when there is nothing to
    read."""
    keys: set[str] = set()
    for candidate in (name, name.split(" - ")[0]):
        names = company_names(candidate)
        if names is not None:
            keys |= names.identity_keys
    return frozenset(keys)
