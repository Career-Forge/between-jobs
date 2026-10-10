"""Closed word lists the fabrication check (`provenance.py`) reads. All lower case.

Three lists, each short enough to review by eye and each a judgement about English, not data:

- `OPENER_WORDS`: words that may start a sentence without being a name. A capitalized word that
  starts a sentence is ambiguous (a verb, or an employer?), so the check lets through only the
  ones it can vouch for: function words, the verbs a resume opens with, and the ordinary nouns
  and adjectives a summary or a letter opens with. Anything else that starts a sentence has to
  be in the text the sentence was written from.
- `PERIOD_UNITS`: the words that say how long or how often, and the unit they belong to, so a
  rewrite cannot turn "per year" into "per month" or "40 minutes" into "40 hours".
- `SENIORITY_WORDS`: the words that claim a level.

A word is missing from `OPENER_WORDS` on purpose when it is also a well-known employer or
product ("Target", "Square", "Slack", "Visa"): those are the names the check exists to catch.
"""

from __future__ import annotations

_FUNCTION_WORDS = """
a an the this that these those i my me myself we our us you your he she it its they their them his
her who whom whose what when where why how which while whereas although though because since if
unless until after before during over under with without within across through throughout between
among beyond behind for from to of on in at by as into onto about above below around along against
toward towards per via and but or nor so yet then thus also both each every either neither all any
some most many much more less few several other another such same only just even still here there
now today currently recently previously formerly early late once often always never together
additionally moreover furthermore however therefore meanwhile overall finally lastly first second
third next last one two three four five six seven eight nine ten thank thanks grateful please yes no
not being having is are was were be been am do does did can could will would should may might must
let lets especially particularly specifically ideally naturally ultimately eventually already again
instead otherwise likewise similarly regardless despite except besides beside beneath inside outside
near past up down off out upon unlike like plus minus versus nearly almost roughly approximately
whether whenever wherever whatever own given considering regarding concerning according everyone
everything someone something nothing anyone anything hello dear kind best regards sincerely
respectfully truly rather quite very really simply clearly directly mainly largely primarily
partly fully highly deeply closely broadly typically usually generally frequently occasionally
sometimes whereby wherein thereby hence soon later earlier afterward afterwards twice anyway
else otherwise further additional another yesterday tomorrow tonight lately
"""

_DESCRIBING_WORDS = """
senior junior lead principal staff full fullstack backend frontend data software machine cloud
platform product products systems system infrastructure engineering engineer developer scientist
analyst architect manager experienced skilled accomplished seasoned results result strong proven
hands deep broad solid practical pragmatic collaborative curious driven motivated detail customer
customers user users security reliability performance quality cross open real large end multi self
high low long short mid big small new recent prior previous former current good great excellent key
core main major primary various numerous multiple single joint daily weekly monthly annual
experience experiences background expertise knowledge focus goal goals mission work projects project
role roles team teams company companies tools tool services service applications application
pipelines pipeline models model reports report dashboards dashboard research education skills skill
responsibilities highlights achievements achievement impact outcomes outcome technologies technical
technology business analytics insights insight engineers developers scientists analysts managers
production scalable distributed reliable robust clean fast efficient effective simple complex
modern legacy internal external public private global local remote hybrid agile lean mobile web
versatile
dedicated creative analytical adaptable innovative ambitious passionate resourceful enthusiastic
hardworking proficient thoughtful methodical entry graduate intern junior mid early late
organized organised flexible adept capable committed confident diligent dynamic energetic
focused friendly genuine honest humble inquisitive meticulous patient personable precise
proactive productive professional prolific rigorous self-taught systematic tenacious trustworthy
user-focused well-rounded bilingual multilingual certified licensed tech-savvy
collaboration communication leadership mentorship ownership delivery architecture design
development operations automation observability testing monitoring deployment integration
migration reporting modelling modeling optimization optimisation planning management
engineering-minded learning growth craft craftsmanship curiosity passion interest interests
motivation problem problems solutions solution challenge challenges opportunity opportunities
career decade decades year years month months week weeks day days time period
""".strip()

_VERBS = """
accelerate accomplish achieve acquire act adapt add address administer adopt advance advise advocate
align allocate amplify analyse analyze announce answer apply appoint approach approve architect
arrange assemble assess assign assist attain audit augment author automate avoid balance benchmark
boost bring broaden budget build calculate capture carry catalog centralize champion change chart
check choose clarify classify clean coach code collaborate collect combine communicate compare
compile complete compose compute conceive conduct configure connect consolidate construct consult
contain contribute control convert coordinate correct create cultivate curate customize cut debug
decide decompose decrease define deliver demonstrate deploy derive describe design detect determine
develop devise diagnose direct discover dispatch distribute document double draft drive earn edit
educate eliminate embed emphasize enable encode encourage enforce engage enhance ensure establish
estimate evaluate examine exceed execute expand expedite experiment explain explore expose extend
extract facilitate field finalize find fix focus forecast forge formalize formulate foster found
generate govern grow guide handle harden harness help identify implement improve incorporate
increase influence inform initiate innovate inspect install instrument integrate interview
introduce invent investigate iterate keep launch lead learn leverage lift link maintain manage map
master maximize measure mentor merge migrate mine minimize mitigate model modernize modify monitor
motivate move navigate negotiate normalize observe onboard operate optimize orchestrate organize
oversee own package parse partner patch perform pilot pioneer plan port present prevent prioritize
process produce profile program promote propose prototype provide publish pull push put qualify
query raise rank rationalize rebuild receive recommend reconcile record recruit redesign reduce
refactor refine reimagine release remediate remove rename renew reorganize replace replicate report
represent research reshape resolve respond restructure retire retrieve review revise revamp revive
rewrite roll route run safeguard sample save scale schedule scope screen script search secure select
serve set settle shape share ship shorten simplify simulate solve source spearhead specify speed
standardize steer stream streamline strengthen structure study submit supervise support surface
sustain synthesize tailor take teach team test theorize track train transform translate triage
troubleshoot tune turn tutor uncover unify update upgrade use utilize validate verify visualize win
wire work write
abide absorb accept access adjust aggregate aid aim alert alleviate alter anchor annotate anticipate
appear arbitrate ascertain ask attend attract authenticate authorize back backfill bake batch become
begin bind bootstrap bridge browse bundle bypass cache call cascade catch certify chair clone close
cluster collapse commission commit compress comprise concentrate confirm confront contract convene
convince copy count cover craft crunch debate decouple dedupe deepen delegate delete depict
differentiate digitize discuss display dissect divide download drop dump elevate emit empower enact
enlist enrich enter equip escalate evolve exchange export fetch file fill filter finish flag flatten
flow fold follow fork form frame fund gain gather get give go grant group halve hand hash host hunt
ideate illustrate imagine import impose index ingest inherit inject insert inspire instruct
interact interpret intervene invest invoke involve isolate join judge jump justify kick kickstart
label land layer lease leave lend license list listen live load locate lock log look loop lower mail
make manufacture mask match materialize mediate meet mock mount multiply name narrow notify nurture
obtain offer offload open orient originate outline outperform output overhaul override pair
paginate participate pass pay persist personalize pick pipe pitch place polish pool populate
position post power practice predict prepare prescribe preserve preside price print probe procure
project prompt prove provision prune purchase pursue quantify quarantine queue quote reach read
realize reassess rebalance reclaim reconstruct recover rectify recycle redirect reengineer
reevaluate reference reflect refresh register regulate reinforce reinvent relay relocate rely
remodel render rent repair repeat reposition republish request require rescue reserve reset resize
restore resume retain retrain return reuse reveal rework ride rotate sanitize satisfy scan scrape
seal seek segment sell send sequence sharpen shift shrink shut sign sketch slash sort spawn speak
sponsor spot stabilize stage stand start state stitch stop store strategize stress stretch strive
subscribe succeed suggest summarize supply surpass survey swap switch sync synchronize systematize
tag tackle taxonomize tell template terminate think thrive tie time tokenize top touch trace trade
transfer transition transmit trim triple trust type unblock undertake unlock unpack uphold upload
urge usher value vet view volunteer walk want warn watch weigh welcome widen wield withstand
witness wrap yield
"""

_IRREGULAR_PAST = """
built led ran wrote cut drove won made set put took taught grew began sold spent got held kept
brought chose found saw gave went came did had met paid sent stood understood undertook oversaw
became rose spoke left lost hit knew shot sat read slid
rebuilt redrew reran rewrote shrank split spun swept thought told wove withdrew fed fought drew
dealt caught bought sought
"""


def _words(block: str) -> frozenset[str]:
    return frozenset(block.split())


OPENER_WORDS: frozenset[str] = (
    _words(_FUNCTION_WORDS) | _words(_DESCRIBING_WORDS) | _words(_VERBS) | _words(_IRREGULAR_PAST)
)
"""Words a sentence may open with unchecked. Verbs are listed in the base form; `-ed`, `-ing`, `-s`
and `-es` forms are recognised by `ordinary_opener`."""

_OPENER_SUFFIXES = ("ed", "ing", "ly", "tion", "sion", "ment", "ness", "ive", "ous", "ful", "able")
"""Endings of words that are, in this position, verbs, adverbs, adjectives or abstract nouns --
not names."""


def ordinary_opener(word: str) -> bool:
    """True if `word` (lower case) may start a sentence without being found in the text the
    sentence came from: a listed word, a listed verb in another form, or a word with one of the
    endings above. A name that merely looks like one of these ("Boeing" ends in "-ing") is not
    checked at the start of a sentence; `provenance.py` says so among the things it cannot see."""
    if word in OPENER_WORDS or word.endswith(_OPENER_SUFFIXES):
        return True
    if word.endswith("ies") and word[:-3] + "y" in OPENER_WORDS:
        return True
    if word.endswith("es") and word[:-2] in OPENER_WORDS:
        return True
    return word.endswith("s") and word[:-1] in OPENER_WORDS


PERIOD_UNITS: dict[str, str] = {
    spelling: unit
    for unit, spellings in {
        "millisecond": ("ms", "millisecond", "milliseconds"),
        "second": ("seconds", "sec", "secs"),
        "minute": ("minute", "minutes", "mins"),
        "hour": ("hour", "hours", "hr", "hrs", "hourly"),
        "day": ("day", "days", "daily"),
        "week": ("week", "weeks", "weekly"),
        "month": ("month", "months", "monthly"),
        "quarter": ("quarter", "quarters", "quarterly"),
        "year": ("year", "years", "yr", "yrs", "yearly", "annual", "annually"),
        "kilobyte": ("kb",),
        "megabyte": ("mb",),
        "gigabyte": ("gb",),
        "terabyte": ("tb",),
        "petabyte": ("pb",),
    }.items()
    for spelling in spellings
}
"""Spelling -> the unit it names. Not listed because they are ambiguous: a bare `s`, `h`, `m`,
`min`, and the singular `second` (as in "the second iteration")."""

STANDALONE_UNITS = frozenset(
    {"hourly", "daily", "weekly", "monthly", "quarterly", "yearly", "annual", "annually"}
)
"""The spellings in `PERIOD_UNITS` that say how often wherever they appear ("a daily job", "paid
annually"). The others ("hours", "year", "gb") are read only after a number, or after "per",
"each" or "every": "in recent years" and "this week" say nothing about a metric."""

SENIORITY_WORDS = frozenset(
    {"senior", "principal", "expert", "experts", "veteran", "seasoned", "distinguished"}
)
"""Words that claim a level wherever they appear. `staff`, `lead` and `architect` also name a
level, but they are ordinary nouns and verbs too, so `provenance.py` reads them only as the head
of a title ("staff engineer", "lead-level")."""
