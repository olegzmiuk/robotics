from __future__ import annotations

import asyncio

from uav_dashboard.camera import CameraService, extract_jpeg_frames


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
