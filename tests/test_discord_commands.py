"""The slash commands the Discord app registers (api/discord_commands.py): every definition is
held to the limits Discord publishes, and the single source of truth is what the adapter reads.

The limits are Discord's: a command or option name is 1-32 characters from a letter, number,
underscore or hyphen set and lower case where a lower case exists; a description is 1-100
characters; a command has at most 25 options and the required ones come first; a string option
takes a `max_length` of 1-6000; an integer option's bounds are whole numbers; there are at most 100
global commands. The registered body also carries the contexts and installation types the app
wants (a direct message with the bot only; a server install and a person's own).
"""

from __future__ import annotations

import re

from between_jobs.api import discord_commands as commands
from between_jobs.api.discord_adapter import parse_interaction

# Discord's pattern is `^[-_\p{L}\p{N}\p{sc=Deva}\p{sc=Thai}]{1,32}$` with the unicode flag; the
# app's own names are ASCII, so this stricter pattern is what they are held to.
_NAME = re.compile(r"[a-z0-9_-]{1,32}")


def _all_options() -> list[tuple[str, commands.OptionSpec]]:
    return [(c.name, o) for c in commands.COMMANDS for o in c.options]


def test_there_is_a_definition_for_every_command_the_adapter_understands() -> None:
    assert commands.command_names() == {
        "link",
        "unlink",
        "list",
        "apply",
        "job",
        "import",
        "setup",
        "resume",
        "privacy",
        "learn",
    }


def test_the_definitions_fit_the_published_limits() -> None:
    definitions = commands.command_definitions()
    assert 1 <= len(definitions) <= commands.MAX_COMMANDS
    names = [d["name"] for d in definitions]
    assert len(names) == len(set(names))  # unique per type and scope
    for definition in definitions:
        assert _NAME.fullmatch(definition["name"]), definition["name"]
        assert definition["name"] == definition["name"].lower()
        assert 1 <= len(definition["description"]) <= commands.MAX_DESCRIPTION_LENGTH
        assert definition["type"] == commands.COMMAND_TYPE_CHAT_INPUT
        options = definition["options"]
        assert len(options) <= commands.MAX_OPTIONS_PER_COMMAND
        # Required options come before optional ones.
        flags = [bool(option["required"]) for option in options]
        assert flags == sorted(flags, reverse=True), definition["name"]
        option_names = [option["name"] for option in options]
        assert len(option_names) == len(set(option_names))
        for option in options:
            assert _NAME.fullmatch(option["name"]), option["name"]
            assert 1 <= len(option["description"]) <= commands.MAX_DESCRIPTION_LENGTH
            assert option["type"] in {
                commands.OPTION_TYPE_STRING,
                commands.OPTION_TYPE_INTEGER,
                commands.OPTION_TYPE_ATTACHMENT,
            }


def test_discords_numbers_are_written_out_here_and_not_read_from_the_module() -> None:
    """The limits test above reads its limits from the module it checks, so a wrong constant would
    change the module and the check together. These are Discord's published values."""
    assert commands.COMMAND_TYPE_CHAT_INPUT == 1
    assert commands.OPTION_TYPE_STRING == 3
    assert commands.OPTION_TYPE_INTEGER == 4
    assert commands.OPTION_TYPE_ATTACHMENT == 11
    assert commands.MAX_COMMANDS == 100
    assert commands.MAX_OPTIONS_PER_COMMAND == 25
    assert commands.MAX_NAME_LENGTH == 32
    assert commands.MAX_DESCRIPTION_LENGTH == 100
    assert commands.MAX_STRING_OPTION_LENGTH == 6000
    definitions = commands.command_definitions()
    assert {d["type"] for d in definitions} == {1}
    assert {o["type"] for d in definitions for o in d["options"]} == {3, 4, 11}


def test_the_code_and_the_file_are_required_so_discord_asks_for_them_before_sending() -> None:
    sent = {d["name"]: {o["name"]: o for o in d["options"]} for d in commands.command_definitions()}
    assert sent["link"]["code"]["required"] is True
    assert sent["import"]["file"]["required"] is True
    assert sent["apply"]["number"]["required"] is True
    assert sent["job"]["location"]["required"] is False


def test_every_string_option_has_a_max_length_within_discords_range() -> None:
    for command, option in _all_options():
        if option.kind == "string":
            assert option.max_length is not None, f"{command}.{option.name}"
            assert 1 <= option.max_length <= commands.MAX_STRING_OPTION_LENGTH
        else:
            assert option.max_length is None, f"{command}.{option.name}"


def test_integer_bounds_are_whole_numbers_in_a_sensible_order() -> None:
    integers = [o for _c, o in _all_options() if o.kind == "integer"]
    assert integers
    for option in integers:
        assert option.min_value is not None and option.max_value is not None
        assert 1 <= option.min_value <= option.max_value < 2**53


def test_the_longest_job_description_fits_the_documented_ceiling() -> None:
    assert commands.JOB_DESCRIPTION_MAX_LENGTH == 6000


def test_every_command_is_for_the_direct_message_with_the_bot_only() -> None:
    for definition in commands.command_definitions():
        assert definition["contexts"] == [commands.INTERACTION_CONTEXT_BOT_DM]


def test_both_ways_of_installing_the_app_are_registered_by_default_and_can_be_narrowed() -> None:
    both = commands.command_definitions()
    assert {tuple(d["integration_types"]) for d in both} == {(0, 1)}
    guild_only = commands.command_definitions(integration_types=(0,))
    assert {tuple(d["integration_types"]) for d in guild_only} == {(0,)}


def test_a_definition_is_plain_json_data() -> None:
    import json

    assert json.loads(json.dumps(commands.command_definitions())) == commands.command_definitions()


def test_the_adapter_turns_each_definition_into_a_message() -> None:
    """The adapter is written against these names: a command that is defined here but that the
    adapter does not understand would register and then answer "I can't do anything with that"."""
    from discord_fakes import command

    for definition in commands.command_definitions():
        options: dict[str, object] = {}
        for option in definition["options"]:
            if option["required"] and option["type"] == commands.OPTION_TYPE_STRING:
                options[option["name"]] = "x"
            elif option["required"] and option["type"] == commands.OPTION_TYPE_INTEGER:
                options[option["name"]] = 1
        message = parse_interaction(command(definition["name"], options))
        assert message is not None, definition["name"]
