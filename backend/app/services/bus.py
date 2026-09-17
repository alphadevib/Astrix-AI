"""Event bus for live dashboard updates.

The pipeline runs in a worker thread (via `asyncio.to_thread`, so a blocking LLM
call never stalls the event loop) while WebSocket subscribers live on the loop.
Publishing therefore has to cross that boundary, which is what
`call_soon_threadsafe` is for.

Subscriber queues are bounded and drop the oldest event when full. A slow or
dead browser tab must never apply backpressure to the decision loop.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

QUEUE_SIZE = 256

# High-rate streams. They are already visible in the charts and the 2D view, and
# keeping them out of the replay buffer keeps the agent-activity feed readable.
HIGH_RATE_EVENTS = frozenset({"telemetry", "launch"})


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._recent: list[dict[str, Any]] = []

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    # -- subscription ------------------------------------------------------- #

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return self._recent[-limit:]

    # -- publication -------------------------------------------------------- #

    def publish(self, event_type: str, payload: Any) -> None:
        event = {
            "type": event_type,
            "at": datetime.now(timezone.utc).isoformat(),
            "payload": payload,
        }
        if event_type not in HIGH_RATE_EVENTS:
            self._recent.append(event)
            del self._recent[:-200]

        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._dispatch, event)
                return
            except RuntimeError:
                pass  # loop closed mid-shutdown
        self._dispatch(event)

    def _dispatch(self, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop oldest, keep the newest
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    log.debug("dropping event for a saturated subscriber")
