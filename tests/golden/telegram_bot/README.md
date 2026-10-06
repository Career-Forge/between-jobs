# Telegram bot golden parity

`expected/telegram_calls.json` records what the Telegram bot sent, for each update in the
corpus, **before** its logic was extracted into `channel_core` and `telegram_adapter`: the
HTTP answer and the exact sequence of Telegram API calls (method, chat, the rendered HTML
text, the inline keyboard, a document's name and bytes).

## Where it came from

Captured, not written by hand: the pre-extraction `telegram_webhook.py`, `telegram_client.py`
and `telegram_identity.py` (commit `c85a428`) were run over the corpus in
`tests/telegram_golden.py` with the same fakes `test_telegram_golden_parity.py` uses, and what
the fake network saw was written out. The corpus (more than forty updates) is the inputs: synthetic
messages and the same sample data the other Telegram tests use -- no real accounts, resumes
or credentials. `test_telegram_golden_parity.py` runs the current code over the same corpus
and requires an identical result.

Message text is stored as a short digest of the rendered HTML, not as text, so this directory
holds no copy of the bot's copy. When a comparison fails, the test prints the new text in
full; compare it with the old one through `git show c85a428:src/between_jobs/api/telegram_webhook.py`.

## When the copy changes on purpose

Run `REGENERATE_TELEGRAM_GOLDEN=1 pytest tests/test_telegram_golden_parity.py -k regenerate`
and review the diff: every changed digest is a message that now reads differently. That
rewrites the file from the current code, so from then on it records the current behavior, not
the pre-extraction one.

## Not covered here

A resume generation runs after the webhook has answered, so the fixture records the calls
once it has finished (the app's shutdown drains it). That it answers promptly, and what
happens to a task that is still running, is in `tests/test_telegram_deferred_prepare.py`.

## Deliberate changes since the capture

The file was regenerated twice-over for changes that were meant, each reviewed message by message:

- **One progress message.** A resume generation used to send an acknowledgement, the document and
  a separate "Applying with this one?" prompt. It now sends the acknowledgement once, edits that
  message as the work advances (`editMessageText`: "compiling the PDF", then the final state with
  the "Mark as applied" button, or the reason there is no resume), and sends the document. In
  the seven scenarios that start a generation, the opening message and the document are
  byte-identical to the capture, and an error or declined-resume text has the same digest as
  before: it is edited into the progress message instead of sent as a new one. The two scenarios
  whose names end in `progress_message_was_deleted` / `cannot_be_edited_and_the_engine_declines`
  cover the fallback: an edit Telegram refuses is followed by a new message.
- **`/privacy` and `/learn`.** New scenarios only; no existing message changed. The `/privacy`
  text was then changed on purpose, after a review of what it says against the web policy: it
  qualifies the deletion promise the way the policy does, says that the resume engine receives
  the AI key, and no longer claims to be a complete list of what is kept. The four `privacy_*`
  scenarios changed with it (their bodies are shared). `privacy_for_a_telegram_only_user` also
  changed in kind: it used to be the 208-character "link this chat first" prompt, and is now the
  summary, with the last line written for a chat that has no web account (`/learn` still sends
  the link prompt, in `learn_for_a_telegram_only_user`).
