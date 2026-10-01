"""A virtual clock for tests of loops that sleep for minutes.

`sleep` parks the caller until the clock is advanced past its wake time;
`advance(seconds)` then runs the event loop forward through every wake-up in
order, letting each woken task run to its next await before the next one fires.
Nothing waits in real time, and the order things happen in is exactly the order
virtual time says."""

from __future__ import annotations

import asyncio
import heapq


class VirtualTime:
    def __init__(self) -> None:
        self.now = 0.0
        self._waiters: list[tuple[float, int, asyncio.Future[None]]] = []
        self._seq = 0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._waiters, (self.now + max(delay, 0.0), self._seq, future))
        self._seq += 1
        await future

    @staticmethod
    async def settle(rounds: int = 40) -> None:
        """Lets every runnable task run until it blocks again."""
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def advance(self, seconds: float) -> None:
        target = self.now + seconds
        await self.settle()
        while self._waiters and self._waiters[0][0] <= target:
            when, _, future = heapq.heappop(self._waiters)
            self.now = max(self.now, when)
            if not future.done():  # a cancelled sleeper's future is already done
                future.set_result(None)
            await self.settle()
        self.now = target
        await self.settle()
