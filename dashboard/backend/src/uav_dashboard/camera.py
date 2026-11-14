"""Shared Raspberry Pi Camera Module MJPEG stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Protocol, cast

import cv2
import numpy as np
from uav.square_detection import (
    DEFAULT_MIN_AREA,
    DEFAULT_THRESHOLD,
    Image,
    SquareDetection,
    annotate_squares,
    detect_squares,
)

from uav_dashboard.models import (
    CameraBoundingBoxData,
    CameraDetectionData,
    CameraPointData,
    CameraStatusData,
)

JPEG_START = b"\xff\xd8"
JPEG_END = b"\xff\xd9"
MJPEG_BOUNDARY = b"frame"


class CameraProcess(Protocol):
    """Async subprocess surface used by the camera service and tests."""

    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    @property
    def returncode(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    async def wait(self) -> int: ...


CameraProcessFactory = Callable[[tuple[str, ...]], Awaitable[CameraProcess]]
CameraFrameProcessor = Callable[[bytes], bytes]
Quadrant = Literal["top_left", "top_right", "bottom_left", "bottom_right"]
SimulatedMotor = Literal["M1", "M2", "M3", "M4"]

SIMULATED_MOTOR_BY_QUADRANT: dict[Quadrant, SimulatedMotor] = {
    "top_left": "M1",
    "top_right": "M2",
    "bottom_right": "M3",
    "bottom_left": "M4",
}


@dataclass(frozen=True)
class CameraStreamError:
    message: str


CameraStreamItem = bytes | CameraStreamError


@dataclass(frozen=True)
class CameraFrameAnalysis:
    frame: bytes
    width: int
    height: int
    detection: SquareDetection | None


def classify_quadrant(detection: SquareDetection, width: int, height: int) -> Quadrant:
    """Classify a detection center into one of four image quadrants."""
    horizontal = "left" if detection.center.x < width / 2 else "right"
    vertical = "top" if detection.center.y < height / 2 else "bottom"
    return cast(Quadrant, f"{vertical}_{horizontal}")


class CameraDetectionTracker:
    """Keep a brief visual decision through dropped detections, then mark it stale."""

    def __init__(self, stale_timeout: float = 0.4) -> None:
        if stale_timeout <= 0:
            raise ValueError("detection stale timeout must be positive")
        self._stale_timeout = stale_timeout
        self._last_analysis: CameraFrameAnalysis | None = None
        self._last_detected_at: datetime | None = None
        self._last_detected_monotonic: float | None = None
        self._current_status: Literal["detected", "holding", "stale", "none"] = "none"

    def update(
        self,
        analysis: CameraFrameAnalysis,
        *,
        captured_at: datetime,
        monotonic_time: float,
    ) -> CameraDetectionData:
        """Build the current decision state from one processed camera frame."""
        if analysis.detection is not None:
            self._last_analysis = analysis
            self._last_detected_at = captured_at
            self._last_detected_monotonic = monotonic_time
            event = self._event(
                status="detected",
                analysis=analysis,
                captured_at=captured_at,
                age_ms=0.0,
            )
            self._current_status = "detected"
            return event

        if (
            self._last_analysis is None
            or self._last_detected_at is None
            or self._last_detected_monotonic is None
        ):
            event = self._empty_event("none", analysis, captured_at)
            self._current_status = "none"
            return event

        age_seconds = max(0.0, monotonic_time - self._last_detected_monotonic)
        age_ms = round(age_seconds * 1000, 1)
        if age_seconds <= self._stale_timeout:
            event = self._event(
                status="holding",
                analysis=self._last_analysis,
                captured_at=captured_at,
                age_ms=age_ms,
            )
            self._current_status = "holding"
            return event
        event = self._empty_event("stale", analysis, captured_at, age_ms=age_ms)
        self._current_status = "stale"
        return event

    def expire(
        self,
        *,
        captured_at: datetime,
        monotonic_time: float,
    ) -> CameraDetectionData | None:
        """Clear an active decision when frames stop arriving past the timeout."""
        if (
            self._current_status not in ("detected", "holding")
            or self._last_analysis is None
            or self._last_detected_monotonic is None
        ):
            return None
        age_seconds = max(0.0, monotonic_time - self._last_detected_monotonic)
        if age_seconds <= self._stale_timeout:
            return None
        event = self._empty_event(
            "stale",
            self._last_analysis,
            captured_at,
            age_ms=round(age_seconds * 1000, 1),
        )
        self._current_status = "stale"
        return event

    def reset(self) -> None:
        self._last_analysis = None
        self._last_detected_at = None
        self._last_detected_monotonic = None
        self._current_status = "none"

    def _event(
        self,
        *,
        status: Literal["detected", "holding"],
        analysis: CameraFrameAnalysis,
        captured_at: datetime,
        age_ms: float,
    ) -> CameraDetectionData:
        detection = analysis.detection
        assert detection is not None
        quadrant = classify_quadrant(detection, analysis.width, analysis.height)
        return CameraDetectionData(
            status=status,
            captured_at=captured_at,
            last_detected_at=self._last_detected_at,
            age_ms=age_ms,
            frame_width=analysis.width,
            frame_height=analysis.height,
            center=CameraPointData(x=detection.center.x, y=detection.center.y),
            bounding_box=CameraBoundingBoxData(
                x=detection.bounding_box.x,
                y=detection.bounding_box.y,
                width=detection.bounding_box.width,
                height=detection.bounding_box.height,
            ),
            area=detection.area,
            quadrant=quadrant,
            simulated_motor=SIMULATED_MOTOR_BY_QUADRANT[quadrant],
        )

    def _empty_event(
        self,
        status: Literal["none", "stale"],
        analysis: CameraFrameAnalysis,
        captured_at: datetime,
        *,
        age_ms: float | None = None,
    ) -> CameraDetectionData:
        return CameraDetectionData(
            status=status,
            captured_at=captured_at,
            last_detected_at=self._last_detected_at,
            age_ms=age_ms,
            frame_width=analysis.width,
            frame_height=analysis.height,
        )


def analyze_and_annotate_frame(
    frame: bytes,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
    jpeg_quality: int = 70,
) -> CameraFrameAnalysis:
    """Decode one JPEG, detect its largest square, and annotate all detections."""
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100")

    encoded = np.frombuffer(frame, dtype=np.uint8)
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("camera emitted an invalid JPEG frame")

    image = cast(Image, decoded)
    detections = detect_squares(image, threshold=threshold, min_area=min_area)
    if not detections:
        return CameraFrameAnalysis(
            frame=frame,
            width=int(image.shape[1]),
            height=int(image.shape[0]),
            detection=None,
        )

    annotated = annotate_squares(image, detections)
    encoded_ok, encoded_frame = cv2.imencode(
        ".jpg",
        annotated,
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
    )
    if not encoded_ok:
        raise RuntimeError("failed to encode annotated camera frame")
    return CameraFrameAnalysis(
        frame=encoded_frame.tobytes(),
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        detection=detections[0],
    )


def detect_and_annotate_frame(
    frame: bytes,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
    jpeg_quality: int = 70,
) -> bytes:
    """Return an annotated JPEG while preserving the original public helper."""
    return analyze_and_annotate_frame(
        frame,
        threshold=threshold,
        min_area=min_area,
        jpeg_quality=jpeg_quality,
    ).frame


def extract_jpeg_frames(buffer: bytearray) -> list[bytes]:
    """Remove and return every complete JPEG frame currently in a byte buffer."""
    frames: list[bytes] = []
    while True:
        start = buffer.find(JPEG_START)
        if start < 0:
            if buffer[-1:] == JPEG_START[:1]:
                del buffer[:-1]
            else:
                buffer.clear()
            return frames

        end = buffer.find(JPEG_END, start + len(JPEG_START))
        if end < 0:
            if start > 0:
                del buffer[:start]
            return frames

        frame_end = end + len(JPEG_END)
        frames.append(bytes(buffer[start:frame_end]))
        del buffer[:frame_end]


async def create_camera_process(command: tuple[str, ...]) -> CameraProcess:
    """Start the system rpicam process without involving a shell."""
    return await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )


class CameraSource(Protocol):
    """Camera surface used by the FastAPI application and test doubles."""

    def status(self) -> CameraStatusData: ...

    def stream(self) -> AsyncIterator[bytes]: ...

    def subscribe_detections(self) -> asyncio.Queue[CameraDetectionData]: ...

    async def unsubscribe_detections(self, queue: asyncio.Queue[CameraDetectionData]) -> None: ...

    async def stop(self) -> None: ...


class CameraService:
    """Run one rpicam process and fan its latest MJPEG frames out to browsers."""

    def __init__(
        self,
        *,
        executable: str = "rpicam-vid",
        camera_index: int = 0,
        width: int = 1280,
        height: int = 720,
        framerate: int = 15,
        quality: int = 70,
        rotation: int = 180,
        detection_stale_timeout: float = 0.4,
        process_factory: CameraProcessFactory = create_camera_process,
        frame_processor: CameraFrameProcessor | None = None,
    ) -> None:
        if camera_index < 0:
            raise ValueError("camera_index must not be negative")
        if width < 1 or height < 1:
            raise ValueError("camera dimensions must be positive")
        if framerate < 1:
            raise ValueError("camera framerate must be positive")
        if not 1 <= quality <= 100:
            raise ValueError("camera quality must be between 1 and 100")
        if rotation not in (0, 180):
            raise ValueError("camera rotation must be 0 or 180 degrees")

        self.width = width
        self.height = height
        self.framerate = framerate
        self.rotation = rotation
        self._process_factory = process_factory
        self._frame_processor = frame_processor
        self._jpeg_quality = quality
        self._detection_tracker = CameraDetectionTracker(detection_stale_timeout)
        self._detection_watch_interval = min(detection_stale_timeout / 2, 0.1)
        self._command = (
            executable,
            "--timeout",
            "0",
            "--nopreview",
            "--camera",
            str(camera_index),
            "--codec",
            "mjpeg",
            "--width",
            str(width),
            "--height",
            str(height),
            "--framerate",
            str(framerate),
            "--quality",
            str(quality),
            "--rotation",
            str(rotation),
            "--autofocus-mode",
            "continuous",
            "--output",
            "-",
        )
        self._subscribers: set[asyncio.Queue[CameraStreamItem]] = set()
        self._detection_subscribers: set[asyncio.Queue[CameraDetectionData]] = set()
        self._task: asyncio.Task[None] | None = None
        self._process: CameraProcess | None = None
        self._state: Literal["idle", "starting", "streaming", "error"] = "idle"
        self._error: str | None = None

    def status(self) -> CameraStatusData:
        """Return current camera process state and configured stream settings."""
        return CameraStatusData(
            state=self._state,
            subscribers=len(self._subscribers),
            width=self.width,
            height=self.height,
            framerate=self.framerate,
            rotation=self.rotation,
            error=self._error,
        )

    async def stream(self) -> AsyncIterator[bytes]:
        """Yield multipart MJPEG chunks until the browser disconnects."""
        queue = self._subscribe()
        try:
            while True:
                item = await queue.get()
                if isinstance(item, CameraStreamError):
                    raise RuntimeError(item.message)
                yield self._multipart_frame(item)
        finally:
            await self._unsubscribe(queue)

    def subscribe_detections(self) -> asyncio.Queue[CameraDetectionData]:
        """Subscribe to latest-value square decisions from the shared camera stream."""
        queue: asyncio.Queue[CameraDetectionData] = asyncio.Queue(maxsize=1)
        self._detection_subscribers.add(queue)
        self._ensure_started()
        return queue

    async def unsubscribe_detections(
        self,
        queue: asyncio.Queue[CameraDetectionData],
    ) -> None:
        """Remove a decision subscriber and stop capture if nobody remains."""
        self._detection_subscribers.discard(queue)
        await self._stop_if_unused()

    async def stop(self) -> None:
        """Stop camera capture and notify all connected consumers."""
        task = self._task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._subscribers.clear()
        self._detection_subscribers.clear()
        self._detection_tracker.reset()
        self._state = "idle"

    def _subscribe(self) -> asyncio.Queue[CameraStreamItem]:
        queue: asyncio.Queue[CameraStreamItem] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        self._ensure_started()
        return queue

    async def _unsubscribe(self, queue: asyncio.Queue[CameraStreamItem]) -> None:
        self._subscribers.discard(queue)
        await self._stop_if_unused()

    def _ensure_started(self) -> None:
        if self._task is None:
            self._state = "starting"
            self._error = None
            self._task = asyncio.create_task(self._run())

    async def _stop_if_unused(self) -> None:
        if self._subscribers or self._detection_subscribers or self._task is None:
            return

        task = self._task
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _has_subscribers(self) -> bool:
        return bool(self._subscribers or self._detection_subscribers)

    async def _run(self) -> None:
        stderr_task: asyncio.Task[bytes] | None = None
        detection_watchdog: asyncio.Task[None] | None = None
        try:
            process = await self._process_factory(self._command)
            self._process = process
            if process.stdout is None:
                raise RuntimeError("rpicam process did not expose stdout")
            if process.stderr is not None:
                stderr_task = asyncio.create_task(process.stderr.read())

            self._state = "streaming"
            detection_watchdog = asyncio.create_task(self._watch_detection_staleness())
            buffer = bytearray()
            while self._has_subscribers():
                chunk = await process.stdout.read(64 * 1024)
                if not chunk:
                    return_code = await process.wait()
                    stderr = await stderr_task if stderr_task is not None else b""
                    detail = stderr.decode(errors="replace").strip()
                    message = f"rpicam-vid exited with status {return_code}"
                    if detail:
                        message = f"{message}: {detail[-1000:]}"
                    raise RuntimeError(message)

                buffer.extend(chunk)
                for frame in extract_jpeg_frames(buffer):
                    analysis = await asyncio.to_thread(self._analyze_frame, frame)
                    self._publish(analysis.frame)
                    detection_event = self._detection_tracker.update(
                        analysis,
                        captured_at=datetime.now(timezone.utc),
                        monotonic_time=asyncio.get_running_loop().time(),
                    )
                    self._publish_detection(detection_event)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._state = "error"
            self._error = str(error)
            self._publish(CameraStreamError(self._error))
            self._publish_detection(
                CameraDetectionData(
                    status="error",
                    captured_at=datetime.now(timezone.utc),
                    frame_width=self.width,
                    frame_height=self.height,
                    error=self._error,
                )
            )
        finally:
            if detection_watchdog is not None:
                detection_watchdog.cancel()
                with suppress(asyncio.CancelledError):
                    await detection_watchdog
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task
            await self._stop_process()
            self._task = None
            if self._state != "error":
                self._state = "idle"

    async def _watch_detection_staleness(self) -> None:
        while True:
            await asyncio.sleep(self._detection_watch_interval)
            event = self._detection_tracker.expire(
                captured_at=datetime.now(timezone.utc),
                monotonic_time=asyncio.get_running_loop().time(),
            )
            if event is not None:
                self._publish_detection(event)

    def _analyze_frame(self, frame: bytes) -> CameraFrameAnalysis:
        if self._frame_processor is not None:
            return CameraFrameAnalysis(
                frame=self._frame_processor(frame),
                width=self.width,
                height=self.height,
                detection=None,
            )
        return analyze_and_annotate_frame(frame, jpeg_quality=self._jpeg_quality)

    async def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is None or process.returncode is not None:
            return

        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2.0)
        except TimeoutError:
            process.kill()
            await process.wait()

    def _publish(self, item: CameraStreamItem) -> None:
        for queue in self._subscribers.copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(item)

    def _publish_detection(self, event: CameraDetectionData) -> None:
        for queue in self._detection_subscribers.copy():
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @staticmethod
    def _multipart_frame(frame: bytes) -> bytes:
        return (
            b"--"
            + MJPEG_BOUNDARY
            + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(frame)).encode()
            + b"\r\n\r\n"
            + frame
            + b"\r\n"
        )
