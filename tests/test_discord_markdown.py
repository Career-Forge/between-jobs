"""`RichText` as Discord message content (api/discord_markdown.py): everything a person or a job
posting wrote is escaped, and a long message is cut into whole messages of at most 2000
characters. Synthetic inputs throughout (see tests/discord_fakes.py): the strings are the
characters Discord's markdown gives a meaning to, not captured messages."""

from __future__ import annotations

import random
import re
import time

import pytest

from between_jobs.api import discord_markdown
from between_jobs.api.channel_envelope import RichText, Segment, bold, code, italic, pre, rich
from between_jobs.api.discord_markdown import (
    MAX_PARTS,
    MESSAGE_LIMIT,
    TRUNCATION_NOTE,
    escape_markdown,
    render_markdown,
    scrub,
    split_markdown,
    utf16_length,
)

_ZWSP = "\u200b"


def _unescaped(rendered: str) -> str:
    """What is left once every backslash-escaped character is removed: if a metacharacter is
    still there, Discord would read it as markup."""
    return re.sub(r"\\.", "", rendered, flags=re.DOTALL)


# -- escaping ----------------------------------------------------------------------------------


@pytest.mark.parametrize("char", list("\\*_~`|[]<"))
def test_every_inline_metacharacter_is_escaped(char: str) -> None:
    assert escape_markdown(f"a{char}b") == f"a\\{char}b"


def test_a_name_that_is_markup_is_drawn_as_text() -> None:
    rendered = render_markdown(
        rich("Tracking ", bold("**Acme** _Corp_ ~~Ltd~~ ||secret|| `code` [x](https://e.com)"))
    )
    assert _unescaped(rendered).replace("**", "").count("*") == 0
    for char in "_~|`[":
        assert char not in _unescaped(rendered), char


@pytest.mark.parametrize(
    "hostile",
    [
        "<@123456789>",
        "<@!123456789>",
        "<@&987654321>",
        "<#555>",
        "<:smile:111>",
        "<t:1700000000:R>",
    ],
)
def test_a_mention_or_emoji_or_timestamp_is_never_left_as_one(hostile: str) -> None:
    rendered = escape_markdown(f"hi {hostile} there")
    assert "<" not in _unescaped(rendered)
    assert rendered.startswith("hi \\<")


@pytest.mark.parametrize("word", ["@everyone", "@here"])
def test_everyone_and_here_are_broken_with_a_zero_width_space(word: str) -> None:
    rendered = escape_markdown(f"ping {word} now")
    assert word not in rendered
    assert word[0] + _ZWSP + word[1:] in rendered


def test_an_email_address_is_left_readable() -> None:
    assert escape_markdown("write to jane@example.com") == "write to jane@example.com"


@pytest.mark.parametrize(
    ("line", "escaped"),
    [
        ("# heading", "\\# heading"),
        ("## heading", "\\## heading"),
        ("### heading", "\\### heading"),
        ("-# small", "\\-# small"),
        ("- item", "\\- item"),
        ("> quote", "\\> quote"),
        (">>> quote", "\\>>> quote"),
        ("1. first", "1\\. first"),
        ("12) twelfth", "12\\) twelfth"),
        ("  - indented", "  \\- indented"),
    ],
)
def test_a_block_marker_at_the_start_of_a_line_is_escaped(line: str, escaped: str) -> None:
    assert escape_markdown(line) == escaped


def test_text_that_only_looks_like_a_block_marker_is_left_alone() -> None:
    for text in ("C# developer", "a - b", "3.5 stars", "#hashtag", "-5 degrees", "x > y", "e-mail"):
        assert escape_markdown(text) == text


def test_every_line_of_a_multi_line_text_is_checked() -> None:
    assert escape_markdown("fine\n- a\n> b\n2. c") == "fine\n\\- a\n\\> b\n2\\. c"


def test_a_marker_in_the_middle_of_a_message_is_not_at_a_line_start() -> None:
    rendered = render_markdown(rich("Skills: ", "- python"))
    assert rendered == "Skills: - python"
    assert render_markdown(rich("Skills:\n", "- python")) == "Skills:\n\\- python"


def test_a_web_address_passes_through_unescaped_so_it_stays_a_link() -> None:
    url = "https://example.com/jobs/staff_engineer/view~1?a=b*c"
    assert escape_markdown(f"see {url} now") == f"see {url} now"
    assert escape_markdown(f"a_b {url} c_d") == f"a\\_b {url} c\\_d"


# Discord's own address token, as discord.py and the simple-markdown library under Discord's client
# both write it. Discord does not publish its tokenizer, so this is the assumption the escaping is
# held to.
_DISCORD_ADDRESS = re.compile(r"https?://[^\s<]+[^<.,:;\"')\]\s]")


@pytest.mark.parametrize(
    "follower",
    [
        "<@123456789012345678>",
        "<@&123456789012345678>",
        "<#123456789012345678>",
        "</cmd:123456789012345678>",
        "<:e:123456789012345678>",
        "<a:e:123456789012345678>",
        "<t:1618953630>",
        "<https://evil.test>",
    ],
)
@pytest.mark.parametrize("between", ["", ".", "),", ".]"])
def test_nothing_that_follows_a_web_address_can_turn_into_a_mention_or_emoji(
    follower: str, between: str
) -> None:
    rendered = escape_markdown("https://a.test/x" + between + follower)
    token = _DISCORD_ADDRESS.match(rendered)
    assert token is not None
    assert "\\" not in token.group(0)  # no backslash left for the address to swallow
    rest = rendered[token.end() :]
    assert rest.lstrip(".,:;\"')]").startswith("<" + _ZWSP)  # nothing starting with `<` is left
    assert not re.search(r"<(?!" + _ZWSP + ")", rest)


@pytest.mark.parametrize(
    "text", ["https://a.test/x]more", "https://a.test/x]", "see https://a.test/x]."]
)
def test_a_closing_bracket_next_to_a_web_address_leaves_no_backslash_in_the_link(text: str) -> None:
    rendered = escape_markdown(text)
    token = _DISCORD_ADDRESS.search(rendered)
    assert token is not None
    assert "\\" not in token.group(0)
    assert "\\" not in rendered  # nothing here needs one: the `]` has no `[` to close


def test_an_address_runs_to_the_next_space_or_angle_bracket_as_discord_reads_it() -> None:
    # Everything up to a space is in the link, so nothing in it is escaped (a backslash would
    # land in the address) -- including a `>` or `*` that is further on than it looks.
    assert escape_markdown("https://a.test/x>*b* c") == "https://a.test/x>*b* c"
    assert (
        escape_markdown("https://a.test/x_y_z, then _it_") == "https://a.test/x_y_z, then \\_it\\_"
    )


def test_the_escaping_after_a_web_address_ends_where_the_address_does() -> None:
    # A `<` further on, and any other metacharacter, are escaped as ever.
    assert escape_markdown("https://a.test/x *b* <@1>") == "https://a.test/x \\*b\\* \\<@1>"
    assert escape_markdown("[https://a.test/x] <@1>") == "\\[https://a.test/x] \\<@1>"
    assert escape_markdown("https://a.test/x<https://b.test/y<@1>") == (
        "https://a.test/x<" + _ZWSP + "https://b.test/y<" + _ZWSP + "@1>"
    )


def test_direction_overrides_are_dropped_and_a_lone_surrogate_cannot_reach_json() -> None:
    assert scrub("ab\u202ecd\u2067ef") == "abcdef"
    cleaned = scrub("x\ud800y")
    assert cleaned == "x\ufffdy"
    cleaned.encode("utf-8")  # does not raise
    assert scrub("a\r\nb\rc") == "a\nb\nc"


def test_random_hostile_text_never_leaves_a_metacharacter_unescaped() -> None:
    rng = random.Random(20261006)
    alphabet = list("ab *_~`|[]()<>@#-.1:\\\n\t")
    for _ in range(500):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
        rendered = escape_markdown(text)
        left = _unescaped(rendered)
        for char in "*_~`|[]<":
            assert char not in left, (text, rendered)
        for line in rendered.split("\n"):
            assert not re.match(r"[ \t]*(#{1,3}|-#|-|>)[ \t]", line), (text, rendered)
            assert not re.match(r"[ \t]*\d+[.)][ \t]", line), (text, rendered)


# -- styles ------------------------------------------------------------------------------------


def test_styles_are_drawn_with_their_markers() -> None:
    assert render_markdown(rich("a ", bold("b"), " ", italic("c"), " ", code("d"))) == (
        "a **b** *c* `d`"
    )
    assert render_markdown(rich(pre("one\ntwo"))) == "```\none\ntwo\n```"


def test_emphasis_keeps_its_edge_whitespace_outside_the_markers() -> None:
    assert render_markdown(rich(bold(" a "))) == " **a** "
    assert render_markdown(rich(bold("   "))) == "   "


def test_code_is_literal_and_a_backtick_run_cannot_end_it() -> None:
    assert render_markdown(rich(code("*not bold* <@1>"))) == "`*not bold* <@1>`"
    assert render_markdown(rich(code("a`b"))) == "``a`b``"
    assert render_markdown(rich(code("`edge`"))) == "`` `edge` ``"
    assert render_markdown(rich(code(""))) == "`" + _ZWSP + "`"


def test_a_pre_block_cannot_be_ended_early_and_its_first_word_is_not_a_language() -> None:
    rendered = render_markdown(rich(pre("json\n```\nnot code```")))
    assert rendered.startswith("```\njson\n")
    assert rendered.count("```") == 2  # only the opening and closing fence
    assert rendered.endswith("\n```")


@pytest.mark.parametrize("run", range(3, 9))
@pytest.mark.parametrize("shape", ["{b}", "{b}\nx", "x\n{b}", "x\n{b}\ny", "a{b}b"])
def test_no_run_of_backticks_in_a_pre_block_can_end_it_early(run: int, shape: str) -> None:
    rendered = render_markdown(rich(pre(shape.format(b="`" * run))))
    assert rendered.startswith("```\n") and rendered.endswith("\n```")
    assert "```" not in rendered[4:-4]  # only the opening and the closing fence
    assert rendered[4:-4].replace(_ZWSP, "") == shape.format(b="`" * run)  # nothing else changed


def test_text_inside_a_pre_block_is_not_escaped() -> None:
    assert "<Your Name>" in render_markdown(rich(pre('{"name": "<Your Name>"}')))


# -- splitting ---------------------------------------------------------------------------------


def test_a_short_message_is_one_part() -> None:
    assert split_markdown(rich("hello")) == ["hello"]


def test_an_empty_message_is_one_invisible_character_because_discord_refuses_nothing() -> None:
    assert split_markdown(RichText()) == [_ZWSP]
    assert split_markdown(rich("\n\n  \n")) == [_ZWSP]


def _paragraphs(count: int, length: int) -> RichText:
    return rich("\n\n".join(f"{i:03d} " + "x" * (length - 4) for i in range(count)))


def test_a_long_message_is_cut_at_blank_lines_into_parts_within_the_limit() -> None:
    parts = split_markdown(_paragraphs(50, 100))
    assert 2 <= len(parts) <= MAX_PARTS
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    # Every part is whole paragraphs: nothing is cut inside one.
    rejoined = "\n\n".join(parts)
    assert rejoined == "\n\n".join(f"{i:03d} " + "x" * 96 for i in range(50))


def test_lines_are_the_next_best_place_to_cut() -> None:
    lines = "\n".join(f"line {i:03d} " + "y" * 40 for i in range(120))
    parts = split_markdown(rich(lines))
    assert len(parts) >= 3
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    assert "\n".join(parts) == lines


def test_a_word_longer_than_a_message_is_cut_anywhere_but_nothing_is_lost() -> None:
    word = "w" * 4500
    parts = split_markdown(rich(word))
    assert [utf16_length(p) for p in parts] == [2000, 2000, 500]
    assert "".join(parts) == word


def test_the_limit_is_counted_in_utf16_units_not_characters() -> None:
    text = "😀" * 1100  # 1100 characters, 2200 UTF-16 code units
    parts = split_markdown(rich(text))
    assert len(parts) == 2
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    assert "".join(parts) == text


def test_escaping_that_makes_text_longer_is_counted() -> None:
    text = "*" * 1500  # 1500 characters, 3000 once each is escaped
    parts = split_markdown(rich(text))
    assert len(parts) == 2
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    assert "".join(parts).replace("\\", "") == text


def test_a_code_block_is_never_cut_open_and_a_long_one_becomes_several_whole_blocks() -> None:
    block = "\n".join(f"row {i:04d} " + "z" * 60 for i in range(100))
    parts = split_markdown(rich("intro\n\n", pre(block), "\n\noutro"))
    assert len(parts) >= 3
    for part in parts:
        assert utf16_length(part) <= MESSAGE_LIMIT
        assert part.count("```") % 2 == 0, part[:80]
    # Nothing lost: every row is still in exactly one block.
    body = "\n".join(re.findall(r"row \d{4} z+", "\n".join(parts)))
    assert body == block


def test_a_bold_run_that_is_cut_is_bold_on_both_sides() -> None:
    text = " ".join(f"word{i}" for i in range(700))  # ~4400 characters
    parts = split_markdown(rich(bold(text)))
    assert len(parts) >= 3
    for part in parts:
        assert part.startswith("**") and part.endswith("**"), part[:20]
        assert utf16_length(part) <= MESSAGE_LIMIT


def test_content_that_would_need_more_than_the_maximum_parts_is_shortened_and_says_so() -> None:
    parts = split_markdown(_paragraphs(400, 100))
    assert len(parts) == MAX_PARTS
    assert parts[-1].endswith(TRUNCATION_NOTE)
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)


def test_a_truncation_inside_a_code_block_leaves_no_open_fence() -> None:
    parts = split_markdown(rich(pre("\n".join("r" * 90 for _ in range(500)))))
    assert len(parts) == MAX_PARTS
    assert all(part.count("```") % 2 == 0 for part in parts)


def test_a_part_never_starts_or_ends_with_blank_space() -> None:
    for part in split_markdown(_paragraphs(50, 100)):
        assert part == part.strip()


def test_a_message_of_exactly_the_limit_is_one_part_and_one_more_character_starts_another() -> None:
    assert split_markdown(rich("a" * 2000)) == ["a" * 2000]
    assert [len(part) for part in split_markdown(rich("a" * 2001))] == [2000, 1]


def test_the_most_parts_is_five() -> None:
    assert MAX_PARTS == 5  # spelled out: the cap is compared with itself everywhere else
    five = "\n\n".join(f"p{i} " + "x" * 1500 for i in range(5))
    six = "\n\n".join(f"p{i} " + "x" * 1500 for i in range(6))
    whole = split_markdown(rich(five))
    assert len(whole) == 5
    assert TRUNCATION_NOTE not in whole[-1]  # nothing was left out, so nothing is said to be
    shortened = split_markdown(rich(six))
    assert len(shortened) == 5
    assert shortened[-1].endswith(TRUNCATION_NOTE)


def test_a_code_block_shortened_to_the_most_parts_still_fits_and_is_closed() -> None:
    parts = split_markdown(rich(pre("\n".join("q" * 1980 for _ in range(7)))))
    assert len(parts) == 5
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    assert all(part.count("```") % 2 == 0 for part in parts)
    assert re.search(r"```\n.*\n```" + re.escape(TRUNCATION_NOTE) + r"$", parts[-1], re.DOTALL)


def test_a_blank_line_is_the_best_place_to_cut_and_a_line_break_the_next() -> None:
    text = "A" * 1000 + "\n\n" + "B" * 500 + "\n" + "C" * 400 + "\n" + "D" * 600
    assert split_markdown(rich(text))[0] == "A" * 1000
    lines = "A" * 1000 + "\n" + "B" * 500 + " " + "C" * 400 + "\n" + "D" * 600
    assert split_markdown(rich(lines))[0] == "A" * 1000 + "\n" + "B" * 500 + " " + "C" * 400


def test_a_space_is_a_better_place_to_cut_than_the_middle_of_a_word() -> None:
    parts = split_markdown(rich("wordy " * 400))
    assert len(parts) == 2
    assert all(word == "wordy" for part in parts for word in part.split())


# -- a text far longer than what is sent is not worked through ---------------------------------


@pytest.mark.parametrize("unit", ["*_", "a", "😀", "\n\n", "a b ", "```"])
def test_a_megabyte_is_cut_to_the_most_parts_without_holding_anything_up(unit: str) -> None:
    text = rich(unit * (1_000_000 // len(unit)))
    started = time.perf_counter()
    parts = split_markdown(text)
    elapsed = time.perf_counter() - started
    if unit == "\n\n":
        assert parts == [_ZWSP]
        return
    assert len(parts) == MAX_PARTS
    assert parts[-1].endswith(TRUNCATION_NOTE)
    assert all(utf16_length(part) <= MESSAGE_LIMIT for part in parts)
    assert elapsed < 1.0, elapsed  # it took a minute before the input was cut down


def test_a_megabyte_in_a_code_block_or_in_many_pieces_is_cut_down_as_well() -> None:
    started = time.perf_counter()
    in_a_block = split_markdown(rich(pre("row\n" * 250_000)))
    in_pieces = split_markdown(rich(*[bold("ab ") for _ in range(300_000)]))  # never merged
    empty_pieces = split_markdown(rich(*[code("") for _ in range(300_000)]))
    assert time.perf_counter() - started < 3.0
    for parts in (in_a_block, in_pieces, empty_pieces):
        assert len(parts) == MAX_PARTS and parts[-1].endswith(TRUNCATION_NOTE)
    assert all(part.count("```") % 2 == 0 for part in in_a_block)


def test_what_is_cut_down_first_gives_the_same_parts_as_what_is_not() -> None:
    # A small limit makes the unclamped reference cheap: `max_parts=10_000` leaves the text whole.
    words = " ".join(f"w{i}" for i in range(400)) + "\n\n" + "*_" * 300
    text = rich(words, bold(" and more of it " * 30), "\n", pre("code\n" * 80))
    whole = split_markdown(text, limit=100, max_parts=10_000)
    assert len(whole) > 12
    cut = split_markdown(text, limit=100, max_parts=5)
    assert len(cut) == 5
    assert cut[:4] == whole[:4]
    assert cut[4].endswith(TRUNCATION_NOTE)
    assert whole[4].startswith(cut[4].removesuffix(TRUNCATION_NOTE).rstrip("`\n"))


def test_text_left_out_is_always_said_to_be_even_when_the_parts_before_it_are_few() -> None:
    # Ten thousand blank characters between two paragraphs: the second is cut down away, so the
    # first part must say the rest was left out even though only one message was made.
    text = rich("first paragraph", " " * 20_000, "second paragraph")
    parts = split_markdown(text, limit=100, max_parts=2)
    assert parts[-1].endswith(TRUNCATION_NOTE)
    assert "second" not in "".join(parts)
    # The same when the room runs out exactly at the end of a segment and the next one has text.
    parts = split_markdown(rich("x", bold(" " * 299), "tail"), limit=100, max_parts=2)
    assert parts == ["x" + TRUNCATION_NOTE]


class _RenderSpy:
    """Records the length of every text `render_segment` is asked to draw."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.lengths: list[int] = []
        real = discord_markdown.render_segment

        def spy(segment: Segment, *, at_line_start: bool = True) -> str:
            self.lengths.append(len(segment.text))
            return real(segment, at_line_start=at_line_start)

        monkeypatch.setattr(discord_markdown, "render_segment", spy)


def test_no_more_text_is_drawn_than_could_be_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _RenderSpy(monkeypatch)
    split_markdown(rich("*_" * 500_000))
    assert max(spy.lengths) <= (MAX_PARTS + 1) * MESSAGE_LIMIT


def test_a_cut_draws_a_message_s_worth_of_a_long_text_per_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RenderSpy(monkeypatch)
    head, tail = discord_markdown._cut(
        Segment("*_" * 500_000), 2000, at_line_start=True, breaks=("\n", "")
    )
    assert head is not None and tail is not None
    assert max(spy.lengths) <= 2000 + 1  # not the 1,000,000 that are left
    assert 1000 <= len(head.text) <= 2000 and len(head.text) + len(tail.text) == 1_000_000


def test_the_loop_stops_at_the_most_parts_even_when_handed_more_than_it_cut_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from collections import deque

    monkeypatch.setattr(
        discord_markdown, "_clamp", lambda text, budget: (deque(text.segments), False)
    )
    spy = _RenderSpy(monkeypatch)
    parts = split_markdown(rich("lorem ipsum " * 20_000))
    assert len(parts) == MAX_PARTS and parts[-1].endswith(TRUNCATION_NOTE)
    assert len(spy.lengths) < 200, len(spy.lengths)  # 120 parts' worth of work would be ~1500
