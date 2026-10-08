"""`RichText` as Discord message content: markdown with every metacharacter escaped, cut into
messages that fit Discord's 2000-character limit.

Discord has no HTML mode. A message's `content` is markdown, and a name or a title that came from
outside -- a job's company, a line of a pasted job description -- would be read as formatting (or
as a mention) unless it is escaped. So the rule is the one `channel_envelope` states for every
renderer: a segment's text is TEXT, never markup, and escaping it is this module's job, once.

What gets escaped in plain, bold and italic text:

- `\\ * _ ~ | ` [ ]` and `<` wherever they stand (apart from directly after a web address, see
  below): emphasis, strikethrough, spoilers, code, masked links, and every `<...>` form (user,
  role and channel mentions, custom emoji, timestamps, suppressed links).
- A line's first characters when they would start a block: `#`, `##`, `###` and `-#` (headings
  and small text), `-` (a list), `>` (a quote) and a number followed by `.` or `)` (a numbered
  list, which also keeps "3. Title" reading as the number a person types into `/apply`).
- `@everyone` and `@here`: a zero-width space after the `@`, so even a client that draws them
  specially has nothing to draw. (The request also carries `allowed_mentions: {parse: []}`, which
  is what stops a ping; this stops the text from looking like one.)
- Web addresses are left as they are: a backslash inside one would end up IN the link. For the
  same reason the characters right after an address are not escaped with a backslash: Discord's
  address token (`https?://[^\\s<]+[^<.,:;"')\\]\\s]`, the pattern discord.py and the
  simple-markdown library under Discord's client both use; Discord does not publish its
  tokenizer, so this is an assumption taken from them) stops at `<` and takes a backslash as
  an ordinary address character. A `<` that follows an address gets a zero-width space after it
  instead (nothing that starts with `<` can survive that), and a `]` there is left bare (without
  its `[`, which is always escaped, it does nothing).
- Right-to-left and other direction-override characters are dropped, and a lone UTF-16
  surrogate (which cannot be sent as JSON) becomes U+FFFD.

Code and pre-formatted text are not escaped (their contents are literal in markdown); instead a
backtick run in the text is lengthened around, or broken up, so it cannot end the span or block
early.

Splitting. A message over the limit is cut into parts, each a whole message of its own: at a blank
line first, then at a line break, then (inside the part's own text only) at a space or, for a
single word longer than a message, anywhere. A pre-formatted block is cut at line breaks and
every piece is its own fenced block, so a code fence is never left open at the end of a part.
Lengths are counted in UTF-16 code units, which is never fewer than Discord's own count.
"""

from __future__ import annotations

import re
from collections import deque

from .channel_envelope import RichText, Segment

MESSAGE_LIMIT = 2000
MAX_PARTS = 5
"""The most messages one `RichText` becomes. Past that the rest is dropped and the last part ends
with `TRUNCATION_NOTE`: a reply of this size is a bug in whatever wrote it, and an unbounded
number of messages is what Discord's rate limits (and the five follow-ups an interaction may
make when it came from a person's own install) are not built for."""
TRUNCATION_NOTE = "\n…(shortened)"

_ZWSP = "\u200b"
_BIDI = re.compile("[\u202a-\u202e\u2066-\u2069]")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
# Discord's own address token (see the module docstring), so this module and Discord agree on where
# an address ends.
_URL = re.compile(r"https?://[^\s<]+[^<.,:;\"')\]\s]")
# What can follow an address's last character before the next space or `<`: the characters the
# pattern above refuses to end on, other than `<` and white space.
_AFTER_URL = frozenset(".,:;\"')]")
_TRIPLE_BACKTICK = re.compile(r"`(?=``)")  # zero-width: matches inside overlapping runs
_ESCAPE = re.compile(r"([\\*_~`|\[\]<])")
_EVERYONE = re.compile(r"@(?=everyone|here)")
_LINE_START = re.compile(
    r"^([ \t]*)(?:(#{1,3})(?=[ \t])|(-#)(?=[ \t])|(-)(?=[ \t])|(\d{1,9})([.)])(?=[ \t])|(>))"
)


def utf16_length(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def scrub(text: str) -> str:
    """`text` as it can be sent: direction overrides dropped, a lone surrogate replaced, line
    endings made `\\n`."""
    cleaned = _BIDI.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))
    return _LONE_SURROGATE.sub("\ufffd", cleaned)


def _escape_line_start(line: str) -> str:
    match = _LINE_START.match(line)
    if match is None:
        return line
    indent, heading, subtext, dash, digits, punct, quote = match.groups()
    if heading:
        return f"{indent}\\{line[len(indent) :]}"
    if subtext or dash or quote:
        return f"{indent}\\{line[len(indent) :]}"
    return f"{indent}{digits}\\{punct}{line[match.end() :]}"


def _escape_inline(text: str) -> str:
    """Escapes everything except addresses, which pass through whole. What directly follows an
    address (see the module docstring) is not escaped with a backslash, which Discord would read
    as the last character of the address."""
    parts: list[str] = []
    last = 0
    for url in _URL.finditer(text):
        parts.append(_escape_run(text[last : url.start()]))
        parts.append(url.group(0))
        # By the shape of `_URL`, what follows an address up to the next space or `<` is made of
        # `_AFTER_URL` characters only. Those are kept as written (a `]` is inert without its `[`).
        end = url.end()
        while end < len(text) and text[end] in _AFTER_URL:
            end += 1
        parts.append(text[url.end() : end])
        if text.startswith("<", end):
            parts.append("<" + _ZWSP)
            end += 1
        last = end
    parts.append(_escape_run(text[last:]))
    return "".join(parts)


def _escape_run(text: str) -> str:
    return _EVERYONE.sub("@" + _ZWSP, _ESCAPE.sub(r"\\\1", text))


def escape_markdown(text: str, *, at_line_start: bool = True) -> str:
    """`text` with every markdown metacharacter escaped (see the module docstring). With
    `at_line_start` False the first line is treated as the middle of one, so it is not checked
    for a block marker."""
    lines = scrub(text).split("\n")
    escaped = [_escape_inline(line) for line in lines]
    if at_line_start:
        escaped[0] = _escape_line_start(escaped[0])
    escaped[1:] = [_escape_line_start(line) for line in escaped[1:]]
    return "\n".join(escaped)


def _wrap(marker: str, text: str) -> str:
    """`text` between `marker`s, with its edge whitespace kept outside them (Discord does not
    emphasise `** x **`). Nothing but whitespace is not emphasised at all."""
    core = text.strip(" \t\n")
    if not core:
        return text
    start = text.index(core)
    return f"{text[:start]}{marker}{core}{marker}{text[start + len(core) :]}"


def _inline_code(text: str) -> str:
    text = scrub(text).replace("\n", " ")
    if not text:
        return "`" + _ZWSP + "`"
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _pre_block(text: str) -> str:
    """A fenced block. It starts with a line break after the fence (so the first word of the text
    is never read as a language name) and any run of three or more backticks inside it is broken
    with zero-width spaces, because Discord ends the block at the first three it meets. Every
    backtick that has two more right behind it gets a space after it, so no three stay together
    however long the run is (a plain replace of "```" does not overlap: a run of five would keep
    three adjacent)."""
    body = _TRIPLE_BACKTICK.sub("`" + _ZWSP, scrub(text))
    body = body.removesuffix("\n")
    return f"```\n{body}\n```"


def render_segment(segment: Segment, *, at_line_start: bool = True) -> str:
    if segment.style is None:
        return escape_markdown(segment.text, at_line_start=at_line_start)
    if segment.style == "bold":
        return _wrap("**", escape_markdown(segment.text, at_line_start=False))
    if segment.style == "italic":
        return _wrap("*", escape_markdown(segment.text, at_line_start=False))
    if segment.style == "code":
        return _inline_code(segment.text)
    return _pre_block(segment.text)


def render_markdown(text: RichText) -> str:
    """The whole text as one string, whatever its length (see `split_markdown` for what is
    sent)."""
    out = ""
    for segment in text.segments:
        out += render_segment(segment, at_line_start=out == "" or out.endswith("\n"))
    return out


# -- splitting -------------------------------------------------------------------------------


def _fits(segment: Segment, raw_length: int, room: int, at_line_start: bool) -> bool:
    head = Segment(segment.text[:raw_length], segment.style)
    return utf16_length(render_segment(head, at_line_start=at_line_start)) <= room


def _cut(
    segment: Segment, room: int, *, at_line_start: bool, breaks: tuple[str, ...]
) -> tuple[Segment | None, Segment | None]:
    """The longest head of `segment` that renders within `room` characters, cut at the best
    break in `breaks` (tried in order; "" means anywhere), and what is left. (None, segment) when
    no head fits or no allowed break exists."""
    text = scrub(segment.text)
    segment = Segment(text, segment.style)
    # Rendering never shortens text by more than the one line break a pre-formatted block drops,
    # so a prefix of more than `room + 1` characters cannot fit, and a probe never has to render
    # more than a message's worth however long the rest of the segment is.
    low, high = 0, min(len(text), room + 1)
    while low < high:  # the largest prefix that fits (rendering never shrinks as text grows)
        middle = (low + high + 1) // 2
        if _fits(segment, middle, room, at_line_start):
            low = middle
        else:
            high = middle - 1
    prefix = text[:low]
    if low == len(text):
        return segment, None
    for separator in breaks:
        if separator == "":
            cut_at = low
        else:
            position = prefix.rfind(separator)
            if position <= 0:
                continue
            cut_at = position + len(separator)
        if cut_at > 0:
            return Segment(text[:cut_at], segment.style), Segment(text[cut_at:], segment.style)
    return None, segment


def _clamp(text: RichText, budget: int) -> tuple[deque[Segment], bool]:
    """The segments of `text`, scrubbed and cut so that together they are at most `budget`
    characters, and whether anything but blank space was left out."""
    kept: deque[Segment] = deque()
    for segment in text.segments:
        cleaned = scrub(segment.text)
        if budget <= 0:
            if cleaned.strip():
                return kept, True
            continue
        piece = cleaned[:budget]
        kept.append(Segment(piece, segment.style))
        budget -= len(piece)
        if len(piece) < len(cleaned) and cleaned[len(piece) :].strip():
            return kept, True
    return kept, False


def split_markdown(
    text: RichText, *, limit: int = MESSAGE_LIMIT, max_parts: int = MAX_PARTS
) -> list[str]:
    """`text` as the contents of the messages that carry it, in order. Always at least one
    (an empty text is a single invisible character: Discord refuses an empty message).

    Only what can end up in `max_parts` messages is ever worked on. Cutting a long text costs
    time in proportion to its length for each message made, so a megabyte of text would hold up
    everything else the server does for as long as it took to cut up a reply that is thrown away
    but for its first few thousand characters. Rendering never makes text shorter (but for the
    one line break a pre-formatted block drops), so `(max_parts + 1) * limit` characters of input
    always fill more than `max_parts` messages and the note that the rest was left out still
    appears."""
    queue, left_out = _clamp(text, (max_parts + 1) * limit)
    parts: list[str] = []
    current = ""
    real = 0  # parts that are not blank
    while queue and real <= max_parts:  # past the cap nothing more is needed (see above)
        segment = queue.popleft()
        at_line_start = current == "" or current.endswith("\n")
        rendered = render_segment(segment, at_line_start=at_line_start)
        room = limit - utf16_length(current)
        if utf16_length(rendered) <= room:
            current += rendered
            continue
        if current:
            # Fill what is left of this part at a line break; or start the next part.
            line_breaks = ("\n\n", "\n") if segment.style != "pre" else ("\n",)
            head, tail = _cut(segment, room, at_line_start=at_line_start, breaks=line_breaks)
            if head is not None and tail is not None and head.text.strip():
                current += render_segment(head, at_line_start=at_line_start)
                queue.appendleft(tail)
            else:
                queue.appendleft(segment)
            parts.append(current)
            real += 1 if current.strip() else 0
            current = ""
            continue
        # A fresh part and the segment still does not fit: cut it, anywhere if it has to be.
        breaks = ("\n\n", "\n", " ", "") if segment.style != "pre" else ("\n", "")
        head, tail = _cut(segment, limit, at_line_start=True, breaks=breaks)
        if head is None or tail is None or not head.text:
            raise ValueError("a segment cannot be cut to fit a message")  # pragma: no cover
        current = render_segment(head, at_line_start=True)
        queue.appendleft(tail)
        parts.append(current)
        real += 1 if current.strip() else 0
        current = ""
    if current:
        parts.append(current)

    cleaned = [part.strip() for part in parts]
    cleaned = [part for part in cleaned if part.strip()]
    if not cleaned:
        return [_ZWSP]
    if len(cleaned) > max_parts or left_out:
        cleaned = _truncate(cleaned[:max_parts], limit)
    return cleaned


def _truncate(parts: list[str], limit: int) -> list[str]:
    last = parts[-1]
    room = limit - utf16_length(TRUNCATION_NOTE) - 4
    while utf16_length(last) > room:
        last = last[: max(1, len(last) - max(1, utf16_length(last) - room))]
    if last.count("```") % 2 == 1:
        last += "\n```"
    parts[-1] = last + TRUNCATION_NOTE
    return parts
