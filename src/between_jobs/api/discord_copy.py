"""The bot's wording, put into words that are true on Discord.

`channel_messages` is written once, for every channel, in the words of the first one: it tells a
person to `Send "list"`, to paste a job in a `Title:` format, and it speaks of "your Telegram
account". On Discord a person cannot send a sentence at all (only run a slash command), and the
account is a Discord one, so the same copy would send them to do something that silently does
nothing. The neutral copy is not changed (the Telegram bot's words are pinned byte for byte by its
golden file); this module is the one place that says what each such sentence is on Discord, and
`localize` applies it to a message just before it is drawn.

How it works: a short, explicit table of exact phrases and what they become, applied to plain and
styled text (never to code or pre-formatted blocks). A whole message (the "track a job" help) can be
replaced outright. The table is deliberately literal rather than clever, and in particular it has no
rule for the bare word "Telegram": text that came from a person or a job posting (a company called
Telegram, a list of tracked jobs) passes through this module and must come out as it went in.
`tests/test_discord_copy.py` renders every message the bot can send and fails if one still tells a
Discord user to send a sentence, paste a format or use Telegram, and fails if a row of the table no
longer matches anything (a sentence reworded in `channel_messages` must be reworded here too).
"""

from __future__ import annotations

from .channel_envelope import RichText, Segment, bold, rich
from .channel_messages import TRACK_JOB_HELP_TEXT

_PHRASES: tuple[tuple[str, str], ...] = (
    (
        'Send "set up my resume" to get started, or "check my resume" to see what\'s on file.',
        "Use /setup to get started, or /resume to see what's on file.",
    ),
    ('Send "set up my resume" to get started.', "Use /setup to get started."),
    (
        'Send "set up my resume" here for the template, or import your resume on the website.',
        "Use /setup here for the template, or import your resume on the website.",
    ),
    ('Send "track a job" to get started.', "Use /job to get started."),
    (
        'Send "list" first to number your applications, then "apply to #N".',
        "Use /list first to number your applications, then /apply with a number.",
    ),
    ('send "list" again to get fresh numbers.', "use /list again to get fresh numbers."),
    ('send "list" to see the numbers.', "use /list to see the numbers."),
    (
        'Send me a job here ("track a job" shows the format), or paste one on the website.',
        "Use /job here to track a job, or paste one on the website.",
    ),
    ('or send "list" and then "apply to #N".', "or use /list and then /apply."),
    (
        'Send "apply to #N" to generate a resume for one.',
        "Use /apply with a number to generate a resume for one.",
    ),
    ('the numbered lists for "apply to #N"', "the numbered lists for /apply"),
    ("send /unlink first", "use /unlink first"),
    ("Please send that again.", "Please run that command again."),
    (
        "Send /link in a private chat with me, not a group",
        "Use /link in a direct message with me, not in a server",
    ),
    (
        "Send the same /link code again in a minute",
        "Use /link with the same code again in a minute",
    ),
    (
        "send the same /link code again in a minute",
        "use /link with the same code again in a minute",
    ),
    (
        "(it gives you a code to send me with /link)",
        "(it gives you a code for the /link command)",
    ),
    ("send it to me here as /link CODE", "use it here with the /link command"),
    ("(send it as /link CODE)", "(use it with the /link command)"),
    (
        "please resend your resume JSON",
        "please import your resume file again with /import",
    ),
    (
        "3️⃣ Send the filled JSON back to me -- as pasted text, or as a ",
        "3️⃣ Save the filled JSON as a ",
    ),
    # The account is a Discord one. Each row is a whole phrase of the shared copy, never the bare
    # word, so a job or company called "Telegram" in a list is not rewritten.
    ("This Telegram account", "This Discord account"),
    ("this Telegram account", "this Discord account"),
    ("Your Telegram account", "Your Discord account"),
    ("a different Telegram account", "a different Discord account"),
    ("Telegram carries this chat.", "Discord carries this chat."),
    # What Discord keeps from an interaction is the id Discord gives it (see /privacy on the
    # website): said here so the summary does not claim less than the policy does.
    (
        "From Telegram: only your numeric Telegram id and a count of failed link-code attempts "
        "-- no name, no username.",
        "From Discord: only your numeric Discord user id and a count of failed link-code attempts "
        "-- no name, no username -- and the id Discord gives each command, only so that none is "
        "run twice; it is removed once it is older than 7 days, as a side effect of the app "
        "receiving later ones.",
    ),
)
"""(the sentence as `channel_messages` and `channel_core` write it, what it is on Discord). Each
is a substring of one plain segment of a message."""

_WHOLE_SEGMENTS: dict[str, str] = {
    # The text that follows the bold ".json" in the resume template's last step.
    " file.\n\n": " file, then attach it with /import.\n\n",
}
"""A segment that is exactly this text becomes that text: for a phrase too short to be safe as a
substring."""

_TRACK_JOB_HELP = rich(
    "📋 ",
    bold("Track a job"),
    "\n\n"
    "Use /job with the job's title, company and description (up to 6,000 characters). Its "
    "location and the posting's address are optional. I don't fetch job postings from a link "
    "yet, so paste the description text; I'll keep the address with the job.",
)

_WHOLE_MESSAGES: dict[RichText, RichText] = {TRACK_JOB_HELP_TEXT: _TRACK_JOB_HELP}


def phrases_replaced() -> tuple[tuple[str, str], ...]:
    """The table, for the test that every row still matches something."""
    return _PHRASES


def _localize_text(text: str) -> str:
    replacement = _WHOLE_SEGMENTS.get(text)
    if replacement is not None:
        return replacement
    for old, new in _PHRASES:
        text = text.replace(old, new)
    return text


def localize(text: RichText) -> RichText:
    """`text` in Discord's words. Code and pre-formatted blocks are left exactly as they are."""
    whole = _WHOLE_MESSAGES.get(text)
    if whole is not None:
        return whole
    segments = [
        Segment(_localize_text(segment.text), segment.style)
        if segment.style in (None, "bold", "italic")
        else segment
        for segment in text.segments
    ]
    return rich(*segments)
