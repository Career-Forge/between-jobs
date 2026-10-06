"""An unprompted message to a user, on every channel they have linked.

`FanOutNotifier` is a `Notifier` made of other notifiers, one per channel that has an adapter
(`{"telegram": TelegramNotifier}` today, built where the app is wired). It looks up the channels
the user linked (`channel_identity.list_linked_channels`) and asks each one's notifier to send:
exactly once per linked channel, in alphabetical order.

Three rules, because a push is a bonus on top of work that already succeeded:

- A linked channel with no registered notifier is skipped, with a log line. The identity table
  also allows channels nothing sends to yet; a user linked there is not an error.
- One channel failing does not stop the others. A notifier that raises is logged (the channel,
  the user and the error, never the message) and the next channel is tried.
- Nothing is retried here. A second attempt on a channel that already took the message would
  be a duplicate; the caller decides whether there is a second chance at all (the digest
  listener has none: it only pushes right after the Today item was newly written).

What `notify` answers is the `Notifier` contract (`channel_envelope`), applied to the channels
together:

- True: at least one channel took the message, whatever the others did (a failing channel has
  already logged itself).
- False: nothing was sent and nothing refused: the user has no linked channel, or every channel
  they linked either has no registered notifier or found no chat to send to.
- Raises: nothing was sent and a channel refused. Every linked channel is still tried first;
  then the first refusal is raised, so the caller (which logs a failed push against its own
  event and user) is told, as it would be by a single-channel notifier. It also raises if the
  lookup of the user's channels itself fails, which is a database problem and not a channel's.

The caller treats any raise like any other failure of a best-effort push.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from supabase import AsyncClient

from .channel_envelope import Notifier, RichText
from .channel_identity import list_linked_channels

logger = logging.getLogger(__name__)


class FanOutNotifier:
    def __init__(self, supabase: AsyncClient, notifiers: Mapping[str, Notifier]) -> None:
        self._supabase = supabase
        # A copy: the registry is built once at wiring, and nothing may add to it afterwards.
        self._notifiers = dict(notifiers)

    @property
    def channels(self) -> frozenset[str]:
        """The channels this notifier can send to."""
        return frozenset(self._notifiers)

    async def notify(self, user_id: str, text: RichText) -> bool:
        sent_anywhere = False
        refusals: list[Exception] = []
        for channel in await list_linked_channels(self._supabase, user_id):
            notifier = self._notifiers.get(channel)
            if notifier is None:
                logger.info(
                    "no notifier is registered for a channel the user linked; skipped",
                    extra={"ctx": {"channel": channel}},
                )
                continue
            try:
                if await notifier.notify(user_id, text):
                    sent_anywhere = True
            except Exception as e:
                # Deliberately broad: whatever one channel's client raises, the others still
                # get their message. Logged with the exception's type and traceback, which the
                # app's formatter writes without its message (a channel's error can quote a
                # request), and never the text. The user is named so that a push that was lost
                # can be traced to a person.
                logger.warning(
                    "a push failed on one channel; the other channels were still tried",
                    exc_info=True,
                    extra={"ctx": {"channel": channel, "user_id": user_id}},
                )
                refusals.append(e)
        if not sent_anywhere and refusals:
            raise refusals[0]
        return sent_anywhere
