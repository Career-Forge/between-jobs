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

from typing import Any

from .body_limit import describe_bytes
from .channel_envelope import Button, ButtonRows, RichText, bold, code, pre, rich

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
BUSY_TEXT = "⏳ I'm busy generating other resumes right now -- try again in a minute."
ALREADY_GENERATING_TEXT = "⏳ Your resume is still generating -- it'll arrive here when it's done."
PREPARE_FAILED_TEXT = "❌ Something went wrong while generating your resume. Try again in a minute."
ENROLLMENT_REQUIRED_TEXT = (
    "❌ Joining the tester programme comes first. On the website, accept the tester agreement, "
    "and link this chat from the Integrations page (it gives you a code to send me with /link). "
    "Then ask again."
)
PREPARE_DECLINED_TEXT = "The resume engine didn't produce a resume for this job.\n\n{warnings}"
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
MARK_APPLIED_PROMPT_TEXT = "Applying with this one?"


def document_too_large_text(limit: int) -> str:
    return (
        f"❌ That file is too large to import (the limit is {describe_bytes(limit)}). "
        "A resume JSON is far smaller than that -- check it is the right file."
    )


def not_a_real_stage_text(status: str) -> str:
    return f"❌ {status!r} isn't a real stage."


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
