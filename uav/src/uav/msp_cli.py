"""Command-line interface for read-only Betaflight MSP telemetry."""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

from uav.msp import (
    AnalogTelemetry,
    Attitude,
    FlightControllerStatus,
    MspClient,
    MspError,
    Sensor,
)


def format_attitude(attitude: Attitude) -> str:
    """Format attitude angles for terminal output."""
    return f"Roll: {attitude.roll:.1f}°, Pitch: {attitude.pitch:.1f}°, Yaw: {attitude.yaw:.1f}°"


def format_status(status: FlightControllerStatus) -> str:
    """Format flight-controller status for terminal output."""
    sensor_names = [
        sensor.name or f"0x{sensor.value:x}" for sensor in Sensor if status.sensors & sensor
    ]
    sensors = ", ".join(sensor_names) if sensor_names else "none"
    reboot_required = "yes" if status.reboot_required else "no"
    return (
        f"PID cycle: {status.pid_cycle_time_us} µs, "
        f"I2C errors: {status.i2c_error_count}, "
        f"Sensors: {sensors}, "
        f"Flight modes: 0x{status.flight_mode_flags:x}, "
        f"PID profile: {status.pid_profile_index}, "
        f"System load: {status.system_load_percent}%, "
        f"Arming disabled: 0x{status.arming_disable_flags:08x}, "
        f"Reboot required: {reboot_required}"
    )


def format_analog(telemetry: AnalogTelemetry) -> str:
    """Format battery and signal telemetry for terminal output."""
    return (
        f"Voltage: {telemetry.battery_voltage:.2f} V, "
        f"Current: {telemetry.current:.2f} A, "
        f"Drawn: {telemetry.milliamp_hours_drawn} mAh, "
        f"RSSI: {telemetry.rssi}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read telemetry from Betaflight over MSP v1")
    parser.add_argument("--port", required=True, help="serial device, for example /dev/ttyAMA0")
    parser.add_argument("--baud-rate", type=int, default=115200)
    parser.add_argument("--timeout", type=float, default=2.0)
    parser.add_argument(
        "--command",
        choices=("api-version", "status", "attitude", "analog"),
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
            elif args.command == "status":
                print(format_status(client.status()))
            elif args.command == "analog":
                print(format_analog(client.analog()))
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
