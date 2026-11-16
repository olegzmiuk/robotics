"""Explicitly gated CLI for one supervised, propeller-free bench motor pulse."""

from __future__ import annotations

import argparse
import os
import signal
from collections.abc import Sequence
from types import FrameType

from uav.bench_motor import BenchMotorAdapter, BenchMotorError
from uav.msp import MspClient, MspError

ENABLE_ENVIRONMENT_VARIABLE = "UAV_BENCH_MOTOR_ENABLED"
ENABLE_ENVIRONMENT_VALUE = "PROPELLERS_REMOVED"


class BenchMotorInterrupted(Exception):
    """Raised when SIGINT or SIGTERM interrupts a supervised pulse."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one bounded Betaflight motor pulse on a secured propeller-free bench"
    )
    parser.add_argument("--port", required=True, help="serial device, for example /dev/ttyAMA0")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument("--motor", required=True, choices=("M1", "M2", "M3", "M4"))
    parser.add_argument("--output", type=int, default=1050)
    parser.add_argument("--duration", type=float, default=0.15)
    parser.add_argument("--confirm-propellers-removed", action="store_true")
    parser.add_argument("--confirm-frame-secured", action="store_true")
    parser.add_argument("--confirm-controller-disarmed", action="store_true")
    parser.add_argument("--confirm-battery-disconnect-ready", action="store_true")
    return parser


def _interrupt_handler(signum: int, frame: FrameType | None) -> None:
    del frame
    raise BenchMotorInterrupted(f"received signal {signum}")


def _validate_operator_confirmations(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> None:
    required_confirmations = {
        "--confirm-propellers-removed": args.confirm_propellers_removed,
        "--confirm-frame-secured": args.confirm_frame_secured,
        "--confirm-controller-disarmed": args.confirm_controller_disarmed,
        "--confirm-battery-disconnect-ready": args.confirm_battery_disconnect_ready,
    }
    missing = [flag for flag, confirmed in required_confirmations.items() if not confirmed]
    if missing:
        parser.error(f"missing required safety confirmation(s): {', '.join(missing)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_operator_confirmations(parser, args)
    if os.getenv(ENABLE_ENVIRONMENT_VARIABLE) != ENABLE_ENVIRONMENT_VALUE:
        raise SystemExit(
            f"physical output disabled: set {ENABLE_ENVIRONMENT_VARIABLE}="
            f"{ENABLE_ENVIRONMENT_VALUE} for this one supervised command"
        )

    previous_sigint = signal.getsignal(signal.SIGINT)
    previous_sigterm = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGINT, _interrupt_handler)
    signal.signal(signal.SIGTERM, _interrupt_handler)
    try:
        print(f"Bench pulse: {args.motor}, output {args.output}, duration {args.duration:.3f} s")
        with MspClient.connect(args.port, args.baud_rate, args.timeout) as client:
            BenchMotorAdapter(client, hardware_enabled=True).pulse(
                args.motor,
                output=args.output,
                duration=args.duration,
            )
    except (KeyboardInterrupt, BenchMotorInterrupted):
        print("Bench pulse interrupted; STOP attempted. Disconnect motor power now.")
        return 130
    except (BenchMotorError, MspError, OSError, ValueError) as error:
        raise SystemExit(
            f"Bench motor command failed: {error}. Disconnect motor power now."
        ) from error
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)

    print("Bench pulse complete; all motor outputs returned to STOP.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
