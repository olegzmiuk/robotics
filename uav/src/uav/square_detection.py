"""Detect and annotate dark squares in OpenCV images.

This module contains only the reusable image-processing logic. It does not
read files, access a camera, parse terminal arguments, or control the UAV.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray

Image = NDArray[np.uint8]
Contour = NDArray[np.int32]

ANNOTATION_COLOR = (0, 255, 0)
ANNOTATION_THICKNESS = 2
ANNOTATION_RADIUS = 4
DEFAULT_THRESHOLD = 140
DEFAULT_MIN_AREA = 1000.0
BLUR_KERNEL_SIZE = (5, 5)
MORPHOLOGY_KERNEL_SIZE = (5, 5)
POLYGON_APPROXIMATION_EPSILON = 0.04
MIN_SQUARE_SIDE_RATIO = 0.75


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class BoundingBox:
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class SquareDetection:
    center: Point
    bounding_box: BoundingBox
    area: float
    contour: Contour

    def to_dict(self) -> dict[str, object]:
        """Convert the result to a JSON-compatible dictionary."""
        return {
            "center": {"x": self.center.x, "y": self.center.y},
            "bounding_box": {
                "x": self.bounding_box.x,
                "y": self.bounding_box.y,
                "width": self.bounding_box.width,
                "height": self.bounding_box.height,
            },
            "area": self.area,
        }


def detect_squares(
    frame: Image,
    threshold: int = DEFAULT_THRESHOLD,
    min_area: float = DEFAULT_MIN_AREA,
) -> list[SquareDetection]:
    """Find dark, approximately square contours in a BGR OpenCV image."""
    if not 0 <= threshold <= 255:
        raise ValueError("threshold must be between 0 and 255")
    if min_area < 0:
        raise ValueError("min_area must not be negative")
    if frame.size == 0:
        raise ValueError("frame must not be empty")

    gray = cast(Image, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    blurred = cast(Image, cv2.GaussianBlur(gray, BLUR_KERNEL_SIZE, 0))
    _, binary = cv2.threshold(blurred, threshold, 255, cv2.THRESH_BINARY_INV)
    morphology_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, MORPHOLOGY_KERNEL_SIZE)
    binary = cast(Image, cv2.morphologyEx(binary, cv2.MORPH_CLOSE, morphology_kernel))
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    detections: list[SquareDetection] = []

    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area:
            continue

        hull = cv2.convexHull(contour)
        perimeter = cv2.arcLength(hull, True)
        approximation = cv2.approxPolyDP(
            hull,
            POLYGON_APPROXIMATION_EPSILON * perimeter,
            True,
        )

        if len(approximation) != 4 or not cv2.isContourConvex(approximation):
            continue

        _, (rotated_width, rotated_height), _ = cv2.minAreaRect(approximation)
        longest_side = max(rotated_width, rotated_height)
        shortest_side = min(rotated_width, rotated_height)
        if longest_side == 0 or shortest_side / longest_side < MIN_SQUARE_SIDE_RATIO:
            continue

        x, y, width, height = cv2.boundingRect(approximation)

        detections.append(
            SquareDetection(
                center=Point(x=x + width // 2, y=y + height // 2),
                bounding_box=BoundingBox(x=x, y=y, width=width, height=height),
                area=area,
                contour=cast(Contour, approximation),
            )
        )

    return sorted(detections, key=lambda detection: detection.area, reverse=True)


def annotate_squares(frame: Image, detections: Iterable[SquareDetection]) -> Image:
    """Return a copy with a contour and center drawn for every detection."""
    annotated = frame.copy()

    for detection in detections:
        cv2.drawContours(annotated, [detection.contour], -1, ANNOTATION_COLOR, ANNOTATION_THICKNESS)
        cv2.circle(
            annotated,
            (detection.center.x, detection.center.y),
            ANNOTATION_RADIUS,
            ANNOTATION_COLOR,
            -1,
        )

    return cast(Image, annotated)
