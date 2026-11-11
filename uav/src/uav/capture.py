"""Capture one webcam frame and run square detection on it."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import cv2

from uav.cli import default_output_path
from uav.square_detection import Image, annotate_squares, detect_squares


def capture_frame(camera_index: int = 0, warmup_frames: int = 30) -> Image:
    """Return one frame from a local webcam after discarding warmup frames."""
    if camera_index < 0:
        raise ValueError("camera_index must not be negative")
    if warmup_frames < 1:
        raise ValueError("warmup_frames must be at least 1")

    camera = cv2.VideoCapture(camera_index)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError(
            "Could not open the camera. Check macOS Settings > Privacy & Security > Camera."
        )

    frame: Image | None = None
    try:
        for _ in range(warmup_frames):
            success, captured_frame = camera.read()
            if success:
                frame = cast(Image, captured_frame)
    finally:
        camera.release()

    if frame is None:
        raise RuntimeError("The camera opened but did not return a frame")

    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture one webcam frame and detect black squares"
    )
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--warmup-frames", type=int, default=30)
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--min-area", type=float, default=1000.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="where to save the annotated image (defaults to a timestamped filename)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        frame = capture_frame(args.camera_index, args.warmup_frames)
    except (RuntimeError, ValueError) as error:
        raise SystemExit(str(error)) from error

    detections = detect_squares(
        frame,
        threshold=args.threshold,
        min_area=args.min_area,
    )
    annotated = annotate_squares(frame, detections)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(args.output), annotated):
        raise SystemExit(f"Could not write image: {args.output}")

    result = {
        "detected": bool(detections),
        "detections": [detection.to_dict() for detection in detections],
        "output": str(args.output),
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
