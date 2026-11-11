"""Command-line interface for read-only Betaflight MSP telemetry."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from uav.msp import MspClient, MspError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read telemetry from Betaflight over MSP v1")
    parser.add_argument("--port", required=True, help="serial device, for example /dev/ttyAMA0")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument(
        "--command",
        choices=("api-version",),
        default="api-version",
        help="read-only MSP command to execute",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        with MspClient.connect(args.port, args.baud_rate, args.timeout) as client:
            version = client.api_version()
    except (MspError, OSError, ValueError) as error:
        raise SystemExit(f"MSP request failed: {error}") from error

    print(f"MSP API: {version.api} (protocol version {version.protocol})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
