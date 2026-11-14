"""Shared WebSocket telemetry polling and fan-out."""

from __future__ import annotations

import asyncio
from contextlib import suppress

from uav_dashboard.models import TelemetryStreamData, TelemetryStreamError
from uav_dashboard.service import TelemetryService

TelemetryStreamEvent = TelemetryStreamData | TelemetryStreamError


class TelemetryBroadcaster:
    """Poll MSP once and deliver the latest event to every browser."""

    def __init__(
        self,
        service: TelemetryService,
        interval: float = 0.1,
        slow_interval: float = 1.0,
        reconnect_interval: float = 1.0,
    ) -> None:
        if interval < 0.05:
            raise ValueError("telemetry interval must be at least 0.05 seconds")
        if slow_interval < interval:
            raise ValueError("slow telemetry interval must not be shorter than attitude interval")
        if reconnect_interval < 0.1:
            raise ValueError("reconnect interval must be at least 0.1 seconds")

        self._service = service
        self._interval = interval
        self._slow_interval = slow_interval
        self._reconnect_interval = reconnect_interval
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
        stream_started = False
        next_slow_refresh = 0.0
        try:
            while True:
                if not self._subscribers:
                    if stream_started:
                        await asyncio.to_thread(self._service.stop_stream)
                        stream_started = False
                    self._subscribers_changed.clear()
                    await self._subscribers_changed.wait()
                    continue

                if not stream_started:
                    await asyncio.to_thread(self._service.start_stream)
                    stream_started = True
                    next_slow_refresh = 0.0

                started_at = asyncio.get_running_loop().time()
                refresh_slow = started_at >= next_slow_refresh
                try:
                    snapshot = await asyncio.to_thread(
                        self._service.read_stream_snapshot,
                        refresh_slow=refresh_slow,
                    )
                    event: TelemetryStreamEvent = TelemetryStreamData(snapshot=snapshot)
                    if refresh_slow:
                        next_slow_refresh = started_at + self._slow_interval
                    delay = self._interval
                except Exception as error:
                    event = TelemetryStreamError(message=str(error))
                    next_slow_refresh = 0.0
                    delay = self._reconnect_interval

                self._publish(event)
                elapsed = asyncio.get_running_loop().time() - started_at
                await asyncio.sleep(max(0.0, delay - elapsed))
        finally:
            if stream_started:
                await asyncio.to_thread(self._service.stop_stream)

    def _publish(self, event: TelemetryStreamEvent) -> None:
        for queue in self._subscribers.copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)
