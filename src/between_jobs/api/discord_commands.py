"""The slash commands the Discord app offers: one definition, read by the adapter and by the
registration script.

Discord delivers a person's words to an HTTP-interactions app only as a slash command or a
button tap; plain text typed in a direct message is never sent to us. So every thing the Telegram
bot understands as a sentence is a command here, and each command's options are what turns into
that sentence (`discord_adapter.parse_interaction` builds the text, and the channel-neutral
logic's own intent parser stays the one place that decides what a text means):

    /link code:ABCD2345             -> "/link ABCD2345"
    /unlink                         -> "/unlink"
    /list                           -> "list"
    /apply number:3                 -> "apply to 3"
    /job title company description  -> "Title: ...\\nCompany: ...\\n...\\n\\n<description>"
    /import file:<resume.json>      -> a document attachment
    /setup, /resume, /privacy, /learn -> "/setup", "check my resume", "/privacy", "/learn"

The commands are registered for a direct message with the app only (`contexts: [BOT_DM]`), for
both ways of installing it (a server, or a person's own account), so a command never shows up in
a server channel. What follows is data in the shape Discord's bulk-overwrite endpoint takes; the
numbers are Discord's own and `tests/test_discord_commands.py` holds every definition to the
limits Discord publishes (name pattern and length, description length, option counts and order,
string and integer bounds).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import MAX_URL_CHARS

# -- Discord's own numbers -----------------------------------------------------------------

COMMAND_TYPE_CHAT_INPUT = 1
OPTION_TYPE_STRING = 3
OPTION_TYPE_INTEGER = 4
OPTION_TYPE_ATTACHMENT = 11

INTERACTION_CONTEXT_BOT_DM = 1
"""Interaction context "BOT_DM": the direct message between a person and the app's bot user."""

INTEGRATION_TYPE_GUILD_INSTALL = 0
INTEGRATION_TYPE_USER_INSTALL = 1

DEFAULT_INTEGRATION_TYPES: tuple[int, ...] = (
    INTEGRATION_TYPE_GUILD_INSTALL,
    INTEGRATION_TYPE_USER_INSTALL,
)
"""Both ways of installing the app (to a server, or to a person's own account), so the commands are
there for a person whichever way they added it. Which of them each person can reach the app's
direct message through is Discord's rule, not ours; the README's Discord section (step 2) says what
Discord's documentation leaves open, and tests/golden/README.md lists what was not verified against
the real service."""

MAX_COMMANDS = 100
"""Discord's cap on global slash commands (the registration script refuses more)."""
MAX_OPTIONS_PER_COMMAND = 25
MAX_NAME_LENGTH = 32
MAX_DESCRIPTION_LENGTH = 100
MAX_STRING_OPTION_LENGTH = 6000
"""The largest `max_length` Discord accepts for a string option, and so the longest a person can
fill one in with."""

# -- the options' own bounds ----------------------------------------------------------------

LINK_CODE_MAX_LENGTH = 64
JOB_TITLE_MAX_LENGTH = 300
JOB_COMPANY_MAX_LENGTH = 300
JOB_LOCATION_MAX_LENGTH = 300
JOB_URL_MAX_LENGTH = MAX_URL_CHARS
JOB_DESCRIPTION_MAX_LENGTH = MAX_STRING_OPTION_LENGTH
APPLY_NUMBER_MAX = 9999

OptionKind = Literal["string", "integer", "attachment"]


@dataclass(frozen=True)
class OptionSpec:
    name: str
    description: str
    kind: OptionKind
    required: bool = False
    max_length: int | None = None
    """For a string. Always set for one: Discord's client then stops the person at the limit
    instead of the server finding out."""
    min_value: int | None = None
    max_value: int | None = None

    def to_discord(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "type": _OPTION_TYPES[self.kind],
            "required": self.required,
        }
        if self.max_length is not None:
            body["max_length"] = self.max_length
        if self.min_value is not None:
            body["min_value"] = self.min_value
        if self.max_value is not None:
            body["max_value"] = self.max_value
        return body


_OPTION_TYPES: dict[OptionKind, int] = {
    "string": OPTION_TYPE_STRING,
    "integer": OPTION_TYPE_INTEGER,
    "attachment": OPTION_TYPE_ATTACHMENT,
}


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    options: tuple[OptionSpec, ...] = ()

    def to_discord(
        self, *, integration_types: tuple[int, ...] = DEFAULT_INTEGRATION_TYPES
    ) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": COMMAND_TYPE_CHAT_INPUT,
            "description": self.description,
            "options": [option.to_discord() for option in self.options],
            "contexts": [INTERACTION_CONTEXT_BOT_DM],
            "integration_types": list(integration_types),
        }


# -- the commands ---------------------------------------------------------------------------

LINK = "link"
UNLINK = "unlink"
LIST = "list"
APPLY = "apply"
JOB = "job"
IMPORT = "import"
SETUP = "setup"
RESUME = "resume"
PRIVACY = "privacy"
LEARN = "learn"

OPT_CODE = "code"
OPT_NUMBER = "number"
OPT_TITLE = "title"
OPT_COMPANY = "company"
OPT_DESCRIPTION = "description"
OPT_LOCATION = "location"
OPT_URL = "url"
OPT_FILE = "file"

COMMANDS: tuple[CommandSpec, ...] = (
    CommandSpec(
        LINK,
        "Link this chat to your Between Jobs account with a code from the website",
        (
            OptionSpec(
                OPT_CODE,
                "The one-time code from the Integrations page on the website",
                "string",
                required=True,
                max_length=LINK_CODE_MAX_LENGTH,
            ),
        ),
    ),
    CommandSpec(UNLINK, "Disconnect this chat from your Between Jobs account"),
    CommandSpec(LIST, "Number the applications you are tracking"),
    CommandSpec(
        APPLY,
        "Generate a resume for one application from your last list",
        (
            OptionSpec(
                OPT_NUMBER,
                "The number it had in your last /list",
                "integer",
                required=True,
                min_value=1,
                max_value=APPLY_NUMBER_MAX,
            ),
        ),
    ),
    CommandSpec(
        JOB,
        "Track a job by pasting its details",
        (
            # Discord wants every required option before the optional ones.
            OptionSpec(
                OPT_TITLE,
                "The job title",
                "string",
                required=True,
                max_length=JOB_TITLE_MAX_LENGTH,
            ),
            OptionSpec(
                OPT_COMPANY,
                "The company",
                "string",
                required=True,
                max_length=JOB_COMPANY_MAX_LENGTH,
            ),
            OptionSpec(
                OPT_DESCRIPTION,
                "The job description (up to 6,000 characters)",
                "string",
                required=True,
                max_length=JOB_DESCRIPTION_MAX_LENGTH,
            ),
            OptionSpec(
                OPT_LOCATION,
                "Where the job is, if you know",
                "string",
                max_length=JOB_LOCATION_MAX_LENGTH,
            ),
            OptionSpec(
                OPT_URL,
                "The posting's address, kept with the job (it is not fetched)",
                "string",
                max_length=JOB_URL_MAX_LENGTH,
            ),
        ),
    ),
    CommandSpec(
        IMPORT,
        "Import your resume from a filled-in JSON file",
        (
            OptionSpec(
                OPT_FILE,
                "The .json file made from the template /setup gives you",
                "attachment",
                required=True,
            ),
        ),
    ),
    CommandSpec(SETUP, "Get the resume template to fill in"),
    CommandSpec(RESUME, "Show the resume I have on file"),
    CommandSpec(PRIVACY, "What I keep and who handles it"),
    CommandSpec(LEARN, "Your getting-started checklist"),
)


def command_names() -> frozenset[str]:
    return frozenset(command.name for command in COMMANDS)


def command_definitions(
    *, integration_types: tuple[int, ...] = DEFAULT_INTEGRATION_TYPES
) -> list[dict[str, Any]]:
    """The body of Discord's bulk-overwrite call: every command the app owns."""
    return [command.to_discord(integration_types=integration_types) for command in COMMANDS]
