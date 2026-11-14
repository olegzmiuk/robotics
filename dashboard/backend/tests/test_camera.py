from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np
from uav_dashboard.camera import (
    CameraDetectionTracker,
    CameraFrameAnalysis,
    CameraService,
    analyze_and_annotate_frame,
    classify_quadrant,
    detect_and_annotate_frame,
    extract_jpeg_frames,
)


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self._returncode: int | None = None
        self._finished = asyncio.Event()

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self._finish(0)

    def kill(self) -> None:
        self._finish(-9)

    async def wait(self) -> int:
        await self._finished.wait()
        assert self._returncode is not None
        return self._returncode

    def _finish(self, returncode: int) -> None:
        if self._returncode is not None:
            return
        self._returncode = returncode
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self._finished.set()


def test_extracts_complete_jpegs_and_preserves_partial_frame() -> None:
    first = b"\xff\xd8first\xff\xd9"
    second = b"\xff\xd8second\xff\xd9"
    buffer = bytearray(b"garbage" + first + second[:-2])

    frames = extract_jpeg_frames(buffer)

    assert frames == [first]
    assert buffer == bytearray(second[:-2])

    buffer.extend(second[-2:])
    assert extract_jpeg_frames(buffer) == [second]
    assert buffer == bytearray()


def test_camera_stream_uses_one_rpicam_process_and_emits_multipart_frame() -> None:
    async def scenario() -> None:
        process = FakeProcess()
        commands: list[tuple[str, ...]] = []

        async def create_process(command: tuple[str, ...]) -> FakeProcess:
            commands.append(command)
            return process

        service = CameraService(
            width=640,
            height=360,
            framerate=12,
            quality=65,
            rotation=180,
            process_factory=create_process,
            frame_processor=lambda frame: frame,
        )
        stream = service.stream()
        next_frame = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        jpeg = b"\xff\xd8camera-frame\xff\xd9"
        process.stdout.feed_data(jpeg)

        multipart = await asyncio.wait_for(next_frame, timeout=1.0)

        assert multipart.endswith(jpeg + b"\r\n")
        assert b"Content-Type: image/jpeg" in multipart
        assert service.status().state == "streaming"
        assert service.status().subscribers == 1
        assert len(commands) == 1
        assert commands[0][0] == "rpicam-vid"
        assert commands[0][commands[0].index("--width") + 1] == "640"
        assert commands[0][commands[0].index("--framerate") + 1] == "12"
        assert commands[0][commands[0].index("--rotation") + 1] == "180"

        await stream.aclose()

        assert process.returncode == 0
        assert service.status().state == "idle"
        assert service.status().subscribers == 0

    asyncio.run(scenario())


def test_detects_and_annotates_square_in_jpeg_frame() -> None:
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (50, 30), (110, 90), (0, 0, 0), -1)
    encoded_ok, encoded = cv2.imencode(".jpg", image)
    assert encoded_ok

    processed = detect_and_annotate_frame(encoded.tobytes(), min_area=500.0)
    decoded = cv2.imdecode(np.frombuffer(processed, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert decoded is not None
    green_pixels = (decoded[:, :, 1] > 140) & (decoded[:, :, 0] < 100) & (decoded[:, :, 2] < 100)
    assert int(green_pixels.sum()) > 20


def test_classifies_detection_into_each_image_quadrant() -> None:
    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    expected = {
        (25, 25): "top_left",
        (175, 25): "top_right",
        (25, 175): "bottom_left",
        (175, 175): "bottom_right",
    }

    for (center_x, center_y), quadrant in expected.items():
        frame = image.copy()
        cv2.rectangle(
            frame,
            (center_x - 15, center_y - 15),
            (center_x + 15, center_y + 15),
            (0, 0, 0),
            -1,
        )
        encoded_ok, encoded = cv2.imencode(".jpg", frame)
        assert encoded_ok
        analysis = analyze_and_annotate_frame(encoded.tobytes(), min_area=500)
        assert analysis.detection is not None
        assert classify_quadrant(analysis.detection, analysis.width, analysis.height) == quadrant


def test_detection_tracker_holds_then_clears_stale_decision() -> None:
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (70, 70), (0, 0, 0), -1)
    encoded_ok, encoded = cv2.imencode(".jpg", image)
    assert encoded_ok
    detected_analysis = analyze_and_annotate_frame(encoded.tobytes(), min_area=500)
    empty_analysis = CameraFrameAnalysis(
        frame=encoded.tobytes(),
        width=160,
        height=120,
        detection=None,
    )
    tracker = CameraDetectionTracker(stale_timeout=0.4)
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    detected = tracker.update(detected_analysis, captured_at=captured_at, monotonic_time=1.0)
    holding = tracker.update(
        empty_analysis,
        captured_at=captured_at + timedelta(milliseconds=200),
        monotonic_time=1.2,
    )
    stale = tracker.update(
        empty_analysis,
        captured_at=captured_at + timedelta(milliseconds=500),
        monotonic_time=1.5,
    )

    assert detected.status == "detected"
    assert detected.quadrant == "top_left"
    assert detected.simulated_motor == "M1"
    assert holding.status == "holding"
    assert holding.simulated_motor == "M1"
    assert holding.age_ms == 200
    assert stale.status == "stale"
    assert stale.simulated_motor is None
    assert stale.quadrant is None
    assert stale.age_ms == 500


def test_detection_tracker_expires_when_frames_stop() -> None:
    image = np.full((120, 160, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (20, 20), (70, 70), (0, 0, 0), -1)
    encoded_ok, encoded = cv2.imencode(".jpg", image)
    assert encoded_ok
    analysis = analyze_and_annotate_frame(encoded.tobytes(), min_area=500)
    tracker = CameraDetectionTracker(stale_timeout=0.4)
    captured_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

    tracker.update(analysis, captured_at=captured_at, monotonic_time=1.0)
    before_timeout = tracker.expire(
        captured_at=captured_at + timedelta(milliseconds=300),
        monotonic_time=1.3,
    )
    after_timeout = tracker.expire(
        captured_at=captured_at + timedelta(milliseconds=500),
        monotonic_time=1.5,
    )

    assert before_timeout is None
    assert after_timeout is not None
    assert after_timeout.status == "stale"
    assert after_timeout.simulated_motor is None
    assert after_timeout.age_ms == 500


def test_detection_subscriber_uses_shared_camera_process() -> None:
    async def scenario() -> None:
        process = FakeProcess()
        process_calls = 0

        async def create_process(command: tuple[str, ...]) -> FakeProcess:
            nonlocal process_calls
            del command
            process_calls += 1
            return process

        service = CameraService(process_factory=create_process)
        queue = service.subscribe_detections()
        await asyncio.sleep(0)

        image = np.full((120, 160, 3), 255, dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (70, 70), (0, 0, 0), -1)
        encoded_ok, encoded = cv2.imencode(".jpg", image)
        assert encoded_ok
        process.stdout.feed_data(encoded.tobytes())

        event = await asyncio.wait_for(queue.get(), timeout=1.0)

        assert event.status == "detected"
        assert event.quadrant == "top_left"
        assert event.simulated_motor == "M1"
        assert process_calls == 1

        await service.unsubscribe_detections(queue)

        assert process.returncode == 0
        assert service.status().state == "idle"

    asyncio.run(scenario())


def test_frame_without_square_keeps_original_jpeg() -> None:
    image = np.full((60, 80, 3), 255, dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".jpg", image)
    assert encoded_ok
    frame = encoded.tobytes()

    assert detect_and_annotate_frame(frame) == frame


def test_invalid_camera_jpeg_is_rejected() -> None:
    try:
        detect_and_annotate_frame(b"not-a-jpeg")
    except ValueError as error:
        assert str(error) == "camera emitted an invalid JPEG frame"
    else:
        raise AssertionError("invalid JPEG should be rejected")


def test_camera_error_is_reported_to_stream_and_status() -> None:
    async def scenario() -> None:
        async def fail_to_start(command: tuple[str, ...]) -> FakeProcess:
            del command
            raise FileNotFoundError("rpicam-vid was not found")

        service = CameraService(process_factory=fail_to_start)
        stream = service.stream()

        try:
            await anext(stream)
        except RuntimeError as error:
            assert str(error) == "rpicam-vid was not found"
        else:
            raise AssertionError("camera stream should report a startup failure")

        assert service.status().state == "error"
        assert service.status().error == "rpicam-vid was not found"

    asyncio.run(scenario())
