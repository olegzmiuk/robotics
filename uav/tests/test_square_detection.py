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


def test_detects_softly_defocused_square() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (140, 90), (260, 210), (35, 35, 35), thickness=-1)
    defocused = cv2.GaussianBlur(image, (21, 21), 8)

    detections = detect_squares(defocused)

    assert len(detections) == 1
    assert detections[0].center.x == pytest.approx(200, abs=2)
    assert detections[0].center.y == pytest.approx(150, abs=2)


def test_detects_square_with_small_horizontal_motion_blur() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (140, 90), (260, 210), (0, 0, 0), thickness=-1)
    motion_kernel = np.zeros((15, 15), dtype=np.float32)
    motion_kernel[7, :] = 1 / 15
    motion_blurred = cv2.filter2D(image, -1, motion_kernel)

    detections = detect_squares(motion_blurred)

    assert len(detections) == 1
    assert detections[0].center.x == pytest.approx(200, abs=2)
    assert detections[0].center.y == pytest.approx(150, abs=2)


def test_detects_rough_square_outline() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    points = np.array([[140, 92], [258, 88], [263, 211], [137, 207]], dtype=np.int32)
    cv2.polylines(image, [points], isClosed=True, color=(20, 20, 20), thickness=8)
    softened = cv2.GaussianBlur(image, (9, 9), 3)

    detections = detect_squares(softened)

    assert len(detections) == 1
    assert detections[0].center.x == pytest.approx(200, abs=3)
    assert detections[0].center.y == pytest.approx(150, abs=3)


def test_ignores_square_like_fragment_clipped_by_frame_edge() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    cv2.rectangle(image, (320, 100), (420, 180), (0, 0, 0), thickness=-1)

    assert detect_squares(image) == []


def test_ignores_slanted_quadrilateral_with_non_square_corners() -> None:
    image = np.full((300, 400, 3), 255, dtype=np.uint8)
    points = np.array([[80, 80], [180, 80], [270, 200], [170, 200]], dtype=np.int32)
    cv2.fillConvexPoly(image, points, (0, 0, 0))

    assert detect_squares(image) == []


def test_rejects_invalid_threshold() -> None:
    image = np.full((100, 100, 3), 255, dtype=np.uint8)

    with pytest.raises(ValueError, match="threshold"):
        detect_squares(image, threshold=256)
