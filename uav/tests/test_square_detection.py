import cv2
import numpy as np
import pytest
from uav.square_detection import detect_squares


def test_detects_black_square_and_reports_its_center() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (140, 90), (260, 210), (0, 0, 0), thickness=-1)

    detections = detect_squares(image)

    assert len(detections) == 1
    assert detections[0].center.x == pytest.approx(200, abs=1)
    assert detections[0].center.y == pytest.approx(150, abs=1)


def test_ignores_rectangle_that_is_not_square_shaped() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (50, 100), (350, 180), (0, 0, 0), thickness=-1)

    assert detect_squares(image) == []


def test_ignores_square_smaller_than_minimum_area() -> None:
    image = np.full((200, 200, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (90, 90), (110, 110), (0, 0, 0), thickness=-1)

    assert detect_squares(image, min_area=1000) == []


def test_rejects_invalid_threshold() -> None:
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    with pytest.raises(ValueError, match="threshold"):
        detect_squares(image, threshold=256)
