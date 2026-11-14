"""Shared Raspberry Pi Camera Module MJPEG stream."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, Protocol, cast

import cv2
import numpy as np
from uav.square_detection import (
    DEFAULT_MIN_AREA,
    DEFAULT_THRESHOLD,
    Image,
    annotate_squares,
    detect_squares,
)

from uav_dashboard.models import CameraStatusData

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


@dataclass(frozen=True)
class CameraStreamError:
    message: str


CameraStreamItem = bytes | CameraStreamError


def detect_and_annotate_frame(
    frame: bytes,
    *,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
    jpeg_quality: int = 70,
) -> bytes:
    """Decode one JPEG and draw the reusable square detector's results."""
    if not 1 <= jpeg_quality <= 100:
        raise ValueError("jpeg_quality must be between 1 and 100")

    encoded = np.frombuffer(frame, dtype=np.uint8)
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if decoded is None:
        raise ValueError("camera emitted an invalid JPEG frame")

    image = cast(Image, decoded)
    detections = detect_squares(image, threshold=threshold, min_area=min_area)
    if not detections:
        return frame

    annotated = annotate_squares(image, detections)
    encoded_ok, encoded_frame = cv2.imencode(
        ".jpg",
        annotated,
        [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality],
    )
    if not encoded_ok:
        raise RuntimeError("failed to encode annotated camera frame")
    return encoded_frame.tobytes()


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
        self._frame_processor = frame_processor or (
            lambda frame: detect_and_annotate_frame(frame, jpeg_quality=quality)
        )
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

    async def stop(self) -> None:
        """Stop camera capture and notify all connected consumers."""
        task = self._task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._task = None
        self._subscribers.clear()
        self._state = "idle"

    def _subscribe(self) -> asyncio.Queue[CameraStreamItem]:
        queue: asyncio.Queue[CameraStreamItem] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        if self._task is None:
            self._state = "starting"
            self._error = None
            self._task = asyncio.create_task(self._run())
        return queue

    async def _unsubscribe(self, queue: asyncio.Queue[CameraStreamItem]) -> None:
        self._subscribers.discard(queue)
        if self._subscribers or self._task is None:
            return

        task = self._task
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        stderr_task: asyncio.Task[bytes] | None = None
        try:
            process = await self._process_factory(self._command)
            self._process = process
            if process.stdout is None:
                raise RuntimeError("rpicam process did not expose stdout")
            if process.stderr is not None:
                stderr_task = asyncio.create_task(process.stderr.read())

            self._state = "streaming"
            buffer = bytearray()
            while self._subscribers:
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
                    processed_frame = await asyncio.to_thread(self._frame_processor, frame)
                    self._publish(processed_frame)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._state = "error"
            self._error = str(error)
            self._publish(CameraStreamError(self._error))
        finally:
            if stderr_task is not None and not stderr_task.done():
                stderr_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stderr_task
            await self._stop_process()
            self._task = None
            if self._state != "error":
                self._state = "idle"

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
