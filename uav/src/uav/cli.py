"""Command-line interface for detecting squares in a static image."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import cast

import cv2

from uav.square_detection import Image, annotate_squares, detect_squares


def default_output_path(now: datetime | None = None) -> Path:
    """Build a unique output path using the local date and time."""
    current_time = now or datetime.now().astimezone()
    timestamp = current_time.strftime("%H:%M:%S:%f")
    return Path(f"uav/artifacts/annotated_{timestamp}.jpg")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Detect black squares in an image")
    parser.add_argument("image", type=Path, help="path to the input image")
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_path(),
        help="where to save the annotated image (defaults to a timestamped filename)",
    )
    parser.add_argument("--threshold", type=int, default=100)
    parser.add_argument("--min-area", type=float, default=1000.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    loaded_image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if loaded_image is None:
        raise SystemExit(f"Could not read image: {args.image}")

    frame = cast(Image, loaded_image)
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
