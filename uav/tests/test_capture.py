import numpy as np
import pytest
from uav.capture import capture_frame


class FakeCamera:
    def __init__(self, opened: bool = True) -> None:
        self.opened = opened
        self.released = False
        self.read_count = 0

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, np.ndarray]:
        self.read_count += 1
        frame = np.full((10, 20, 3), self.read_count, dtype=np.uint8)
        return True, frame

    def release(self) -> None:
        self.released = True


def test_capture_frame_returns_last_warmup_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    camera = FakeCamera()
    monkeypatch.setattr("uav.capture.cv2.VideoCapture", lambda _: camera)

    frame = capture_frame(warmup_frames=3)

    assert camera.read_count == 3
    assert camera.released is True
    assert np.all(frame == 3)


def test_capture_frame_fails_when_camera_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera = FakeCamera(opened=False)
    monkeypatch.setattr("uav.capture.cv2.VideoCapture", lambda _: camera)

    with pytest.raises(RuntimeError, match="Could not open the camera"):
        capture_frame()

    assert camera.released is True
