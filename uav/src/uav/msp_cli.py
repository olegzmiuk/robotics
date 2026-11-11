"""Command-line interface for read-only Betaflight MSP telemetry."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from uav.msp import Attitude, MspClient, MspError


def format_attitude(attitude: Attitude) -> str:
    """Format attitude angles for terminal output."""
    return f"Roll: {attitude.roll:.1f}°, Pitch: {attitude.pitch:.1f}°, Yaw: {attitude.yaw:.1f}°"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read telemetry from Betaflight over MSP v1")
    parser.add_argument("--port", required=True, help="serial device, for example /dev/ttyAMA0")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument(
        "--command",
        choices=("api-version", "attitude"),
        default="api-version",
        help="read-only MSP command to execute",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="continuously display attitude until Ctrl+C",
    )
    parser.add_argument(
        "--poll-interval",
        "--interval",
        type=float,
        default=0.5,
        help="seconds between attitude requests in watch mode (default: 0.5)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.watch and args.command != "attitude":
        parser.error("--watch is supported only with --command attitude")
    if args.poll_interval <= 0:
        parser.error("--poll-interval must be positive")

    try:
        with MspClient.connect(args.port, args.baud_rate, args.timeout) as client:
            if args.command == "api-version":
                version = client.api_version()
                print(f"MSP API: {version.api} (protocol version {version.protocol})")
            elif args.watch:
                while True:
                    print(format_attitude(client.attitude()), flush=True)
                    time.sleep(args.poll_interval)
            else:
                print(format_attitude(client.attitude()))
    except KeyboardInterrupt:
        print("Stopped.")
        return 0
    except (MspError, OSError, ValueError) as error:
        raise SystemExit(f"MSP request failed: {error}") from error

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
