"""What the bot says: every message's copy, in channel-neutral form, defined once.

Plain messages are plain `str`. Messages with formatting are `RichText` (see
`channel_envelope`): bold for headings, `pre` and `code` for the blocks the user is meant
to copy -- Telegram draws those as tap-to-copy. A message with a value in it (a job title, a
stage) is a function that puts the value into a segment, so what came from outside is text
and can never be read as markup; each channel's renderer decides how a style is drawn and
does the escaping.

Buttons are built here too, because the data a button carries and the prefix the callback
handler parses it by (`channel_core`) are one contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .body_limit import describe_bytes
from .channel_envelope import Button, ButtonRows, RichText, Segment, bold, code, pre, rich
from .first_run import STEP_IDS, FirstRunFacts, FirstRunView, StepId

# -- callback data: the buttons below carry it, `channel_core` reads it --------------------

ACTIVATE_PREFIX = "profile:activate:"
CANCEL_PREFIX = "profile:cancel:"
PREPARE_PREFIX = "app:prepare:"
STAGE_PREFIX = "app:stage:"

STAGE_APPLIED_STATUS = "applied"

# -- plain messages ------------------------------------------------------------------------

FALLBACK_TEXT = (
    'Send "set up my resume" to get started, or "check my resume" to see what\'s on file.'
)
NO_RESUME_TEXT = 'I don\'t have a resume on file yet. Send "set up my resume" to get started.'

LINK_INVALID_TEXT = "❌ That code isn't valid. Double-check it and try again."
LINK_EXPIRED_TEXT = "❌ That code expired. Generate a new one from the website."
LINK_RATE_LIMITED_TEXT = "❌ Too many wrong codes -- try again in a few minutes."
LINK_CONFLICT_TEXT = (
    "❌ Couldn't link -- both accounts already have conflicting data "
    "(e.g. the same saved API key or tracked job). Nothing was changed."
)
LINK_ALREADY_LINKED_TEXT = "You're already linked to that account."
LINK_SOURCE_LINKED_TEXT = (
    "This Telegram account is already linked to a web account -- if that's the one "
    "you're linking, you're all set. To link a different account, send /unlink first, "
    "then generate a new code on the website."
)
LINK_TARGET_LINKED_TEXT = (
    "❌ That web account is already linked to a different Telegram account. Unlink it "
    "there first, then generate a new code."
)
LINK_RETRY_TEXT = "Please send that again."
LINK_PRIVATE_ONLY_TEXT = (
    "Send /link in a private chat with me, not a group -- and generate a fresh code, "
    "since anyone in this chat could have seen that one."
)
LINK_FAILED_TEXT = "❌ Couldn't link right now. Nothing was changed -- try again in a minute."
LINK_UNCONFIRMED_TEXT = (
    "❌ Couldn't confirm the link. Send the same /link code again in a minute -- if it "
    "says the code isn't valid, generate a new one on the website."
)
LINK_FINISHING_NOTE = (
    "\n\nStill moving your files over -- send the same /link code again in a minute "
    "and I'll finish it."
)
LINK_REFUSALS = {
    "rate_limited": LINK_RATE_LIMITED_TEXT,
    "expired_code": LINK_EXPIRED_TEXT,
    "source_mismatch": LINK_RETRY_TEXT,
    "source_already_linked": LINK_SOURCE_LINKED_TEXT,
    "target_linked_elsewhere": LINK_TARGET_LINKED_TEXT,
}
UNLINK_TEXT = "Unlinked. This Telegram account is no longer connected to any web account."
UNLINK_NOT_LINKED_TEXT = "This Telegram account isn't linked to a web account -- nothing to unlink."

MERGE_SUMMARY_LABELS = (
    ("profile_versions", "resume version"),
    ("applications", "tracked application"),
    ("provider_credentials", "saved API key"),
)
"""Only the tables a human would recognize -- career_facts,
application_events, event_outbox, artifact_versions, working_sets, and
link_codes move too, but naming them in a chat message would just be
noise."""

GENERATING_TEXT = "⏳ Generating your resume for this job -- this can take a minute..."
PREPARE_COMPILING_TEXT = "⏳ Resume written -- compiling the PDF..."
PREPARE_DONE_TEXT = "✅ Done -- your resume is in this chat. Applying with this one?"
BUSY_TEXT = "⏳ I'm busy generating other resumes right now -- try again in a minute."
ALREADY_GENERATING_TEXT = "⏳ Your resume is still generating -- it'll arrive here when it's done."
PREPARE_FAILED_TEXT = "❌ Something went wrong while generating your resume. Try again in a minute."
ENROLLMENT_REQUIRED_TEXT = (
    "❌ Joining the tester programme comes first. On the website, accept the tester agreement, "
    "and link this chat from the Integrations page (it gives you a code to send me with /link). "
    "Then ask again."
)
PREPARE_DECLINED_TEXT = "The resume engine didn't produce a resume for this job.\n\n{warnings}"
PREPARE_NO_DETAILS_TEXT = "No details given."
DECLINED_WARNINGS_LIMIT = 3000
"""How many characters of the engine's reasons a declined-resume message carries. A message can
hold 4096 on Telegram, and the engine's reasons are text this code does not write, so they are
bounded rather than trusted to be short."""
PREPARE_SUCCESS_CAPTION = "📄 Resume -- ATS score {score}/100{warnings}"

NO_APPLICATIONS_TEXT = 'Nothing tracked yet. Send "track a job" to get started.'
NO_WORKING_SET_TEXT = 'Send "list" first to number your applications, then "apply to #N".'
WORKING_SET_EXPIRED_TEXT = 'That list expired -- send "list" again to get fresh numbers.'
REFERENCE_OUT_OF_RANGE_TEXT = '#{index} isn\'t on your list -- send "list" to see the numbers.'

SAVED_TEXT = "✅ Saved! Your resume is now on file."
PREVIEW_GONE_TEXT = "❌ That preview is gone -- please resend your resume JSON."
CANCELLED_TEXT = "Cancelled. Nothing was saved."
APPLICATION_GONE_TEXT = "❌ Couldn't find that application anymore."

MARK_APPLIED_BUTTON_TEXT = "✅ Mark as applied"


def declined_warnings_text(warnings: Sequence[str], *, limit: int = DECLINED_WARNINGS_LIMIT) -> str:
    """The reasons the engine gave for not producing a resume, one bullet each, within `limit`
    characters. Whole bullets are kept while they fit; the rest are counted ("... and N more"),
    and a single reason that is longer than the limit by itself is cut, with an ellipsis. Text
    that fits is returned exactly as it would be written without a bound."""
    if not warnings:
        return PREPARE_NO_DETAILS_TEXT
    bullets = [f"• {warning}" for warning in warnings]
    whole = "\n".join(bullets)
    if len(whole) <= limit:
        return whole
    room = limit - len(_and_more(len(bullets))) - 1  # the marker, and the line break before it
    kept: list[str] = []
    used = 0
    for bullet in bullets:
        cost = len(bullet) + (1 if kept else 0)
        if used + cost > room:
            break
        kept.append(bullet)
        used += cost
    if not kept:
        kept = [bullets[0][: room - 1] + "…"]
    omitted = len(bullets) - len(kept)
    if omitted:
        kept.append(_and_more(omitted))
    return "\n".join(kept)


def _and_more(count: int) -> str:
    return f"• ... and {count} more"


def document_too_large_text(limit: int) -> str:
    return (
        f"❌ That file is too large to import (the limit is {describe_bytes(limit)}). "
        "A resume JSON is far smaller than that -- check it is the right file."
    )


def not_a_real_stage_text(status: str) -> str:
    return f"❌ {status!r} isn't a real stage."


# -- /privacy and /learn -----------------------------------------------------------------------

LINK_FIRST_TEXT = (
    "To use this, link this chat to your Between Jobs account first. On the website, open "
    "Integrations, generate a code, and send it to me here as /link CODE."
)
"""What `/learn` tells a chat that is not linked to a web account, instead of the answer: the
checklist is about the website account's data, which such a chat has none of to show."""

PRIVACY_NO_LINK_TEXT = "Full policy: on the website's Privacy Policy page."

_PRIVACY_CONTROL_LINKED = (
    '• Yours to control: /unlink detaches this chat. "Delete my account" on the website\'s '
    "Profile page removes your account and its data straight away, with exceptions (such as "
    "backups until they expire, drafts in your Gmail and what your AI provider kept) that the "
    "full policy lists.\n\n"
)
_PRIVACY_CONTROL_BOT_ONLY = (
    "• Yours to control: link this chat to a website account with a code from its Integrations "
    "page (send it as /link CODE). To have your account deleted, email the privacy address on "
    "the policy page: it is done within 7 days, apart from what the policy lists as not "
    "removed.\n\n"
)


def link_first_text(web_url: str | None) -> str:
    if web_url is None:
        return LINK_FIRST_TEXT
    return f"{LINK_FIRST_TEXT}\n\n{web_url}/profile/integrations"


def privacy_text(web_url: str | None, *, linked: bool) -> RichText:
    """A short summary of what is stored and who handles it, and where the whole policy is.

    A summary of the web app's Privacy Policy (`web/src/content/legal.ts`), which is the
    authority and is what the link leads to. It is held to the policy in BOTH directions:

    - It may not say more than the policy does. Where the policy qualifies a promise, so does
      this: deletion "removes your account and its data" only "with the exceptions" the policy
      lists, and the summary says so and names the ones a person is most likely to count as
      theirs (backups, drafts already in their Gmail, what the AI provider kept).
    - It may not say less than the policy about where a credential goes. The resume engine
      receives the person's AI key with each request (it uses the key for that one request and
      does not store it), so the summary says that, next to "stored encrypted". A list of what
      is kept is worded as a "mainly" list: it is not the policy's whole list, and the full
      policy is linked for the rest.

    Each line was checked against the code: the identity row holds only the Telegram user id
    and a count of failed link-code attempts; provider keys are stored encrypted
    (`provider_credentials_store`) and go to the engine in the request body
    (`forge_engines_client`); every real resume generation writes a usage record
    (`product_events`); "list" stores a numbered working set (`working_sets_store`); no code
    path submits an application or sends an email; `/unlink` and the Profile page's "Delete my
    account" exist.

    `linked` is whether this chat is attached to a website account. Only the "Yours to control"
    line depends on it: a chat the bot made an account for on first contact has no web account
    to delete from the Profile page and nothing to `/unlink`, so it is told how to link and how
    to ask for deletion instead. With no `web_url` the message says the policy is on the website
    and gives no link."""
    policy = f"Full policy: {web_url}/privacy" if web_url is not None else PRIVACY_NO_LINK_TEXT
    control = _PRIVACY_CONTROL_LINKED if linked else _PRIVACY_CONTROL_BOT_ONLY
    return rich(
        "🔒 ",
        bold("Privacy, in short"),
        "\n\n"
        "• What I keep: mainly your profile, tracked jobs, resumes, saved searches and AI key "
        "(stored encrypted), plus a short record each time you use a main feature (no resume or "
        'job text) and the numbered lists for "apply to #N". From Telegram: only your numeric '
        "Telegram id and a count of failed link-code attempts -- no name, no username.\n"
        "• Who handles it: Supabase (database and files) and Railway (runs the API). For a "
        "resume, an operator-run resume engine gets your profile, the job posting and the AI key "
        "and model you chose, uses the key for that one request and does not store it; a PDF "
        "renderer gets the finished resume. OpenRouter, your AI provider, gets the text a "
        "feature needs. Telegram carries this chat.\n"
        "• What I never do: submit an application or send an email for you -- that last step is "
        "yours. No ads, no third-party analytics.\n" + control,
        policy,
    )


_STEP_MARKS = {"done": "✅", "todo": "⬜", "unknown": "❓"}

_STEP_HINTS: dict[StepId, str] = {
    "profile": (
        'Send "set up my resume" here for the template, or import your resume on the website.'
    ),
    "model_key": "Paste your own model key on the website. This app never runs on a shared key.",
    "first_search": (
        "Search for a role on the website, or leave the filters empty to browse the latest "
        "postings."
    ),
    "track_job": (
        'Send me a job here ("track a job" shows the format), or paste one on the website.'
    ),
    "generate_resume": (
        'Tap "Generate resume" under a tracked job, or send "list" and then "apply to #N".'
    ),
}
_STEP_WEB_PAGES: dict[StepId, str] = {
    "profile": "the Profile page",
    "model_key": "the Integrations page (under Profile)",
    "first_search": "the Discover page",
    "track_job": "the Applications page",
    "generate_resume": "the Applications page",
}


def _unknown_note(step_id: StepId, facts: FirstRunFacts) -> str:
    """Why a step cannot be called done or todo. The first search is the one a chat can never
    settle for itself: a search run in the browser leaves nothing the chat can read."""
    if (
        step_id == "first_search"
        and facts.saved_searches is not None
        and facts.applications is not None
    ):
        return "I can't see searches you run in your browser"
    return "couldn't check just now"


def learn_text(view: FirstRunView, facts: FirstRunFacts, web_url: str | None) -> RichText:
    """The first-run checklist as a message: the five steps with where each stands (done, todo,
    or unknown, and why), then the next step to take and where to do it.

    Every word is ours: the only value that is not a constant is the website's address, put in
    as plain text. A step the chat cannot check is shown as unknown and never named as next."""
    lines: list[str] = []
    for step in view.steps:
        line = f"{_STEP_MARKS[step.status]} {step.label}"
        if step.status == "unknown":
            line += f" -- {_unknown_note(step.id, facts)}"
        lines.append(line)

    parts: list[str | Segment] = [
        "📋 ",
        bold(f"Getting started -- {view.done_count} of {len(STEP_IDS)} done"),
        "\n\n" + "\n".join(lines) + "\n\n",
    ]
    next_step = view.next_step
    if next_step is not None:
        where = (
            f"On the website: {web_url}{next_step.page}"
            if web_url is not None
            else f"On the website: {_STEP_WEB_PAGES[next_step.id]}."
        )
        parts += [bold("Next: "), next_step.label, "\n", _STEP_HINTS[next_step.id], "\n", where]
    elif view.done_count == len(STEP_IDS):
        parts.append("All five are done -- you're set up.")
    else:
        unchecked = ", ".join(s.label for s in view.steps if s.status == "unknown")
        parts.append(f"Everything I could check is done. I couldn't confirm: {unchecked}.")
    return rich(*parts)


# -- formatted messages --------------------------------------------------------------------

# The resume template people copy, as plain text: the renderer escapes it, so the "<...>"
# placeholders in it reach the user as "<...>" and not as markup.
RESUME_TEMPLATE_JSON = """{
  "personal": {
    "name": "<Your Name>",
    "headline": "<e.g. Software Engineer>",
    "emails": [{"address": "<you@example.com>", "primary": true}],
    "phones": [{"number": "<+1 555 0100>", "primary": true, "region": "US"}],
    "links": {"linkedin": "", "github": "", "portfolio": "", "scholar": ""},
    "location": {"city": "", "region": "", "country": "", "show_on_resume": false},
    "work_authorization": "<your own work-authorization situation, in your own words>"
  },
  "summary_bullets": ["<one line summarizing who you are>"],
  "experience": [
    {
      "title": "<Job Title>",
      "company": "<Company>",
      "location": "<City, ST>",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM or present",
      "is_current": false,
      "bullets": ["<what you did, with a number if you can>"],
      "skills": ["<Python>"],
      "metrics": [],
      "pin": null
    }
  ],
  "projects": [],
  "education": [
    {
      "degree": "<B.S. Computer Science>",
      "institution": "<University>",
      "start_date": "YYYY-MM",
      "end_date": "YYYY-MM"
    }
  ],
  "publications": [],
  "patents": [],
  "skills": {
    "programming": [], "ai_ml": [], "data_mlops": [],
    "cloud_devops": [], "tools": [], "other": []
  },
  "certifications": [],
  "achievements": [],
  "languages": [],
  "volunteering": []
}"""

SETUP_HELP_TEXT = rich(
    "🗂️ ",
    bold("Set up your resume (one-time)"),
    "\n\n"
    "I read resumes from a fixed JSON template -- deterministic, private, no AI guessing in "
    "the loop.\n\n",
    bold("How:"),
    "\n"
    "1️⃣ Copy the JSON below.\n"
    "2️⃣ Paste it into ChatGPT/Claude with your real resume, and ask it to fill the template "
    "in with your actual info.\n"
    "3️⃣ Send the filled JSON back to me -- as pasted text, or as a ",
    bold(".json"),
    " file.\n\n",
    pre(RESUME_TEMPLATE_JSON),
    "\n"
    "Sections shown empty above (publications, patents, certifications, languages, "
    "volunteering, and links.scholar) are optional -- fill in whichever apply to you and "
    "delete the rest. At least one of experience, projects, publications, patents, or "
    "volunteering needs a real entry.\n\n",
    code("work_authorization"),
    " wants your actual situation, not just your citizenship "
    "-- a citizenship or nationality alone isn't a complete answer, and what counts as "
    "complete looks different in every country.\n\n",
    code('"pin"'),
    " on an experience/project/education entry (shown above as ",
    code("null"),
    ") is a manual choice, not something to fill in from your resume: set it to ",
    code('{"mandatory": true, "min_bullets": 3}'),
    " (min_bullets 1-6, optional) to force that entry into every generated resume regardless "
    "of relevance -- up to 6 pinned entries total.",
)

TRACK_JOB_HELP_TEXT = rich(
    "📋 ",
    bold("Track a job"),
    "\n\n"
    "Send me the job in this format -- Title and Company are required, Location and URL are "
    "optional:\n\n",
    pre(
        "Title: Staff AI Engineer\n"
        "Company: Acme\n"
        "Location: Remote\n"
        "URL: https://example.com/jobs/123\n"
        "\n"
        "<paste the full job description here>"
    ),
    "\n\n"
    "I don't fetch job postings from a link yet -- paste the description text along with the "
    "URL, and I'll track both.",
)


def job_tracked_text(title: str, company: str) -> RichText:
    return rich("✅ Tracking ", bold(title), " at ", bold(company), ".")


def stage_changed_text(status: str) -> RichText:
    return rich("✅ Marked as ", bold(status), ".")


# -- messages built from stored data --------------------------------------------------------


def build_preview_message(
    version: dict[str, Any], stats: dict[str, int], warnings: tuple[str, ...]
) -> str:
    personal = version["canonical_json"]["personal"]
    name = personal.get("name", "")
    headline = personal.get("headline", "")
    primary_email = next((e["address"] for e in personal.get("emails", []) if e.get("primary")), "")

    lines = ["📄 Resume preview", "", f"Name: {name}"]
    if headline:
        lines.append(f"Headline: {headline}")
    if primary_email:
        lines.append(f"Email: {primary_email}")
    lines += [
        "",
        "Detected:",
        f"• Experience: {stats['experience']}",
        f"• Projects: {stats['projects']}",
        f"• Education: {stats['education']}",
        f"• Skills: {stats['skills']}",
    ]
    if warnings:
        lines.append("")
        lines.append("⚠️ Notes:")
        lines += [f"• {w}" for w in warnings]
    lines += ["", "Tap a button below to confirm."]
    return "\n".join(lines)


def format_merge_summary(summary: dict[str, int]) -> str:
    parts = [
        f"{summary[key]} {label}(s)" for key, label in MERGE_SUMMARY_LABELS if summary.get(key)
    ]
    if not parts:
        return "✅ Linked! Your Telegram account is now connected to your web account."
    return "✅ Linked! Brought over " + ", ".join(parts) + " from this Telegram account."


def format_active_profile(version: dict[str, Any]) -> str:
    personal = version["canonical_json"]["personal"]
    name = personal.get("name", "")
    headline = personal.get("headline", "")

    lines = ["✅ I have your resume on file.", "", f"Name: {name}"]
    if headline:
        lines.append(f"Headline: {headline}")
    lines += ["", f"Last updated: {version.get('activated_at', '')}", "", FALLBACK_TEXT]
    return "\n".join(lines)


# -- keyboards ------------------------------------------------------------------------------


def preview_keyboard(version_id: str) -> ButtonRows:
    return (
        (
            Button("✅ Looks good -- save it", f"{ACTIVATE_PREFIX}{version_id}"),
            Button("❌ Cancel", f"{CANCEL_PREFIX}{version_id}"),
        ),
    )


def prepare_keyboard(application_id: str) -> ButtonRows:
    return ((Button("📄 Generate resume", f"{PREPARE_PREFIX}{application_id}"),),)


def mark_applied_keyboard(application_id: str) -> ButtonRows:
    data = f"{STAGE_PREFIX}{application_id}:{STAGE_APPLIED_STATUS}"
    return ((Button(MARK_APPLIED_BUTTON_TEXT, data),),)
