from __future__ import annotations

import pytest
from uav.bench_motor import (
    MAX_BENCH_MOTOR_VALUE,
    MSP_SET_MOTOR,
    BenchMotorAdapter,
    BenchMotorDisabledError,
    BenchMotorStopError,
    encode_motor_outputs,
)
from uav.msp import MspFrameError, MspResponse


class FakeTransport:
    def __init__(self, *, fail_on_request: int | None = None, payload: bytes = b"") -> None:
        self.requests: list[tuple[int, bytes]] = []
        self._fail_on_request = fail_on_request
        self._payload = payload

    def request(self, command: int, payload: bytes = b"") -> MspResponse:
        self.requests.append((command, payload))
        if self._fail_on_request == len(self.requests):
            raise OSError("serial disconnected")
        return MspResponse(command=command, payload=self._payload)


def outputs_from_request(request: tuple[int, bytes]) -> tuple[int, ...]:
    command, payload = request
    assert command == MSP_SET_MOTOR
    return tuple(int.from_bytes(payload[index : index + 2], "little") for index in range(0, 8, 2))


def test_adapter_is_disabled_by_default() -> None:
    transport = FakeTransport()
    adapter = BenchMotorAdapter(transport)

    with pytest.raises(BenchMotorDisabledError):
        adapter.pulse("M1")

    assert transport.requests == []


def test_pulse_stops_before_and_after_single_motor_output() -> None:
    transport = FakeTransport()
    sleeps: list[float] = []
    adapter = BenchMotorAdapter(transport, hardware_enabled=True, sleep_fn=sleeps.append)

    adapter.pulse("M3", output=1050, duration=0.15)

    assert [outputs_from_request(request) for request in transport.requests] == [
        (1000, 1000, 1000, 1000),
        (1000, 1000, 1050, 1000),
        (1000, 1000, 1000, 1000),
    ]
    assert sleeps == [0.15]


def test_interrupted_pulse_still_sends_stop() -> None:
    transport = FakeTransport()

    def interrupt(_: float) -> None:
        raise KeyboardInterrupt

    adapter = BenchMotorAdapter(transport, hardware_enabled=True, sleep_fn=interrupt)

    with pytest.raises(KeyboardInterrupt):
        adapter.pulse("M2")

    assert outputs_from_request(transport.requests[-1]) == (1000, 1000, 1000, 1000)


def test_start_transport_error_still_attempts_stop() -> None:
    transport = FakeTransport(fail_on_request=2)
    adapter = BenchMotorAdapter(transport, hardware_enabled=True, sleep_fn=lambda _: None)

    with pytest.raises(OSError, match="serial disconnected"):
        adapter.pulse("M1")

    assert len(transport.requests) == 3
    assert outputs_from_request(transport.requests[-1]) == (1000, 1000, 1000, 1000)


def test_final_stop_failure_has_dedicated_emergency_error() -> None:
    transport = FakeTransport(fail_on_request=3)
    adapter = BenchMotorAdapter(transport, hardware_enabled=True, sleep_fn=lambda _: None)

    with pytest.raises(BenchMotorStopError, match="disconnect motor power"):
        adapter.pulse("M1")


@pytest.mark.parametrize("output", [0, 999, 1000, MAX_BENCH_MOTOR_VALUE + 1, 2000])
def test_pulse_rejects_output_outside_conservative_range(output: int) -> None:
    transport = FakeTransport()
    adapter = BenchMotorAdapter(transport, hardware_enabled=True)

    with pytest.raises(ValueError, match="pulse output"):
        adapter.pulse("M1", output=output)

    assert transport.requests == []


@pytest.mark.parametrize("duration", [-1.0, 0.0, 0.201, 1.0])
def test_pulse_rejects_duration_above_200_milliseconds(duration: float) -> None:
    transport = FakeTransport()
    adapter = BenchMotorAdapter(transport, hardware_enabled=True)

    with pytest.raises(ValueError, match="pulse duration"):
        adapter.pulse("M1", duration=duration)

    assert transport.requests == []


def test_manual_stop_is_available_while_pulses_are_disabled() -> None:
    transport = FakeTransport()

    BenchMotorAdapter(transport).stop()

    assert outputs_from_request(transport.requests[0]) == (1000, 1000, 1000, 1000)


def test_motor_payload_is_four_little_endian_u16_values() -> None:
    assert encode_motor_outputs((1000, 1000, 1050, 1000)) == (b"\xe8\x03\xe8\x03\x1a\x04\xe8\x03")


def test_payload_encoder_rejects_multiple_active_motors() -> None:
    with pytest.raises(ValueError, match="only one motor"):
        encode_motor_outputs((1050, 1000, 1050, 1000))


def test_rejects_nonempty_betaflight_acknowledgement() -> None:
    adapter = BenchMotorAdapter(FakeTransport(payload=b"unexpected"))

    with pytest.raises(MspFrameError, match="empty payload"):
        adapter.stop()
