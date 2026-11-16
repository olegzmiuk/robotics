"""Narrowly scoped, disabled-by-default Betaflight bench motor adapter."""

from __future__ import annotations

from collections.abc import Callable
from struct import pack
from threading import Lock
from time import sleep
from typing import Literal, Protocol

from uav.msp import MspFrameError, MspResponse

MSP_SET_MOTOR = 214
MOTOR_STOP_VALUE = 1000
MAX_BENCH_MOTOR_VALUE = 1100
MAX_BENCH_PULSE_DURATION = 0.2
MOTOR_COUNT = 4
STOP_OUTPUTS: tuple[int, int, int, int] = (
    MOTOR_STOP_VALUE,
    MOTOR_STOP_VALUE,
    MOTOR_STOP_VALUE,
    MOTOR_STOP_VALUE,
)

BenchMotor = Literal["M1", "M2", "M3", "M4"]


class BenchMotorError(Exception):
    """Base error for the propeller-free bench motor adapter."""


class BenchMotorDisabledError(BenchMotorError):
    """Raised when a pulse is attempted without deployment opt-in."""


class BenchMotorStopError(BenchMotorError):
    """Raised when the adapter cannot send the final all-motors STOP."""


class MspCommandTransport(Protocol):
    """Minimal MSP surface used by the isolated write adapter."""

    def request(self, command: int, payload: bytes = b"") -> MspResponse: ...


def encode_motor_outputs(outputs: tuple[int, int, int, int]) -> bytes:
    """Encode four Betaflight external motor values as little-endian U16s."""
    if len(outputs) != MOTOR_COUNT:
        raise ValueError(f"exactly {MOTOR_COUNT} motor outputs are required")
    for output in outputs:
        if not MOTOR_STOP_VALUE <= output <= MAX_BENCH_MOTOR_VALUE:
            raise ValueError(
                f"bench motor output must be between {MOTOR_STOP_VALUE} and {MAX_BENCH_MOTOR_VALUE}"
            )
    if sum(output > MOTOR_STOP_VALUE for output in outputs) > 1:
        raise ValueError("only one motor may be above STOP")
    return pack("<4H", *outputs)


class BenchMotorAdapter:
    """Issue one bounded motor pulse and always attempt an all-motors STOP.

    This low-level adapter deliberately has no camera, dashboard, or flight-control
    dependency. It is not exposed by an API or CLI. A caller must explicitly set
    ``hardware_enabled=True`` at construction time before a pulse can be issued.
    """

    def __init__(
        self,
        transport: MspCommandTransport,
        *,
        hardware_enabled: bool = False,
        sleep_fn: Callable[[float], None] = sleep,
    ) -> None:
        self._transport = transport
        self._hardware_enabled = hardware_enabled
        self._sleep = sleep_fn
        self._lock = Lock()

    @property
    def hardware_enabled(self) -> bool:
        return self._hardware_enabled

    def pulse(
        self,
        motor: BenchMotor,
        *,
        output: int = 1050,
        duration: float = 0.15,
    ) -> None:
        """Pulse exactly one motor, then restore all four outputs to STOP."""
        if not self._hardware_enabled:
            raise BenchMotorDisabledError("physical bench motor output is disabled")
        if motor not in ("M1", "M2", "M3", "M4"):
            raise ValueError(f"unknown motor: {motor}")
        if not MOTOR_STOP_VALUE < output <= MAX_BENCH_MOTOR_VALUE:
            raise ValueError(
                f"pulse output must be above {MOTOR_STOP_VALUE} and at most {MAX_BENCH_MOTOR_VALUE}"
            )
        if not 0 < duration <= MAX_BENCH_PULSE_DURATION:
            raise ValueError(
                f"pulse duration must be positive and at most {MAX_BENCH_PULSE_DURATION} seconds"
            )

        motor_index = int(motor[1]) - 1
        active_values = [MOTOR_STOP_VALUE] * MOTOR_COUNT
        active_values[motor_index] = output
        active_outputs = (
            active_values[0],
            active_values[1],
            active_values[2],
            active_values[3],
        )

        with self._lock:
            self._write_stop()
            try:
                self._write(active_outputs)
                self._sleep(duration)
            finally:
                try:
                    self._write_stop()
                except Exception as error:
                    raise BenchMotorStopError(
                        "failed to send the final all-motors STOP; disconnect motor power"
                    ) from error

    def stop(self) -> None:
        """Send the safe external STOP value to every motor, even when disabled."""
        with self._lock:
            self._write_stop()

    def _write_stop(self) -> None:
        self._write(STOP_OUTPUTS)

    def _write(self, outputs: tuple[int, int, int, int]) -> None:
        response = self._transport.request(MSP_SET_MOTOR, encode_motor_outputs(outputs))
        if response.payload:
            raise MspFrameError(
                "MSP_SET_MOTOR acknowledgement must have an empty payload, "
                f"received {len(response.payload)} bytes"
            )
