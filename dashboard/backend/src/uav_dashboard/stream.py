"""Shared WebSocket telemetry polling and fan-out."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from uav_dashboard.models import TelemetryStreamData, TelemetryStreamError
from uav_dashboard.service import TelemetryService

TelemetryStreamEvent = TelemetryStreamData | TelemetryStreamError


class TelemetryBroadcaster:
    """Poll MSP once and deliver the latest event to every browser."""

    def __init__(self, service: TelemetryService, interval: float = 0.1) -> None:
        if interval < 0.05:
            raise ValueError("telemetry interval must be at least 0.05 seconds")

        self._service = service
        self._interval = interval
        self._subscribers: set[asyncio.Queue[TelemetryStreamEvent]] = set()
        self._subscribers_changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        """Start the shared polling task."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop polling and wait for the task to finish."""
        if self._task is None:
            return

        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def subscribe(self) -> asyncio.Queue[TelemetryStreamEvent]:
        """Register a browser and return its latest-value queue."""
        queue: asyncio.Queue[TelemetryStreamEvent] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        self._subscribers_changed.set()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TelemetryStreamEvent]) -> None:
        """Remove a browser from the fan-out set."""
        self._subscribers.discard(queue)
        self._subscribers_changed.set()

    async def _run(self) -> None:
        while True:
            if not self._subscribers:
                self._subscribers_changed.clear()
                await self._subscribers_changed.wait()
                continue

            started_at = asyncio.get_running_loop().time()
            try:
                snapshot = await asyncio.to_thread(self._service.read_snapshot)
                event: TelemetryStreamEvent = TelemetryStreamData(snapshot=snapshot)
            except Exception as error:
                event = TelemetryStreamError(message=str(error))

            self._publish(event)
            elapsed = asyncio.get_running_loop().time() - started_at
            await asyncio.sleep(max(0.0, self._interval - elapsed))

    def _publish(self, event: TelemetryStreamEvent) -> None:
        for queue in self._subscribers.copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
