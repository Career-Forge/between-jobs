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
