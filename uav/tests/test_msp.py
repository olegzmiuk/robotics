from __future__ import annotations

from collections.abc import Iterable
from struct import pack

import pytest
from uav.msp import (
    MSP_ANALOG,
    MSP_API_VERSION,
    MSP_ATTITUDE,
    MSP_STATUS,
    AnalogTelemetry,
    ApiVersion,
    Attitude,
    FlightControllerStatus,
    MspClient,
    MspCommandError,
    MspFrameError,
    MspTimeoutError,
    MspUnexpectedCommandError,
    Sensor,
    encode_request,
    read_response,
    xor_checksum,
)


def response_frame(command: int, payload: bytes = b"", *, error: bool = False) -> bytes:
    body = bytes((len(payload), command)) + payload
    header = b"$M!" if error else b"$M>"
    return header + body + bytes((xor_checksum(body),))


class FakeSerial:
    def __init__(self, chunks: Iterable[bytes] = ()) -> None:
        self._chunks = [bytearray(chunk) for chunk in chunks]
        self.writes: list[bytes] = []
        self.flushed = False
        self.closed = False

    def read(self, size: int = 1) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks[0]
        data = bytes(chunk[:size])
        del chunk[:size]
        if not chunk:
            self._chunks.pop(0)
        return data

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def flush(self) -> None:
        self.flushed = True

    def close(self) -> None:
        self.closed = True


def test_xor_checksum_matches_verified_hardware_response() -> None:
    assert xor_checksum(b"\x03\x01\x00\x01\x2d") == 0x2E


def test_encodes_api_version_request() -> None:
    assert encode_request(MSP_API_VERSION) == b"$M<\x00\x01\x01"


def test_reads_successful_response() -> None:
    serial = FakeSerial([response_frame(MSP_API_VERSION, b"\x00\x01\x2d")])

    response = read_response(serial, MSP_API_VERSION)

    assert response.command == MSP_API_VERSION
    assert response.payload == b"\x00\x01\x2d"


def test_rejects_invalid_checksum() -> None:
    frame = response_frame(MSP_API_VERSION, b"\x00\x01\x2d")[:-1] + b"\x00"

    with pytest.raises(MspFrameError, match="checksum"):
        read_response(FakeSerial([frame]), MSP_API_VERSION)


def test_rejects_invalid_header() -> None:
    with pytest.raises(MspFrameError, match="header"):
        read_response(FakeSerial([b"BAD"]), MSP_API_VERSION)


def test_rejects_incomplete_frame() -> None:
    frame = response_frame(MSP_API_VERSION, b"\x00\x01\x2d")

    with pytest.raises(MspTimeoutError):
        read_response(FakeSerial([frame[:-1]]), MSP_API_VERSION)


def test_reports_serial_timeout() -> None:
    with pytest.raises(MspTimeoutError):
        read_response(FakeSerial(), MSP_API_VERSION)


def test_rejects_unexpected_command() -> None:
    with pytest.raises(MspUnexpectedCommandError, match="expected 1"):
        read_response(FakeSerial([response_frame(2, b"BTFL")]), MSP_API_VERSION)


def test_rejects_msp_error_response() -> None:
    frame = response_frame(MSP_API_VERSION, error=True)

    with pytest.raises(MspCommandError, match="rejected"):
        read_response(FakeSerial([frame]), MSP_API_VERSION)


def test_supports_partial_serial_reads() -> None:
    frame = response_frame(MSP_API_VERSION, b"\x00\x01\x2d")
    serial = FakeSerial(bytes((byte,)) for byte in frame)

    assert read_response(serial, MSP_API_VERSION).payload == b"\x00\x01\x2d"


def test_leaves_next_frame_in_stream_for_next_read() -> None:
    first = response_frame(MSP_API_VERSION, b"\x00\x01\x2d")
    second = response_frame(MSP_API_VERSION, b"\x00\x01\x2e")
    serial = FakeSerial([first + second])

    assert read_response(serial, MSP_API_VERSION).payload[-1] == 45
    assert read_response(serial, MSP_API_VERSION).payload[-1] == 46


def test_client_requests_and_decodes_api_version() -> None:
    serial = FakeSerial([response_frame(MSP_API_VERSION, b"\x00\x01\x2d")])

    with MspClient(serial) as client:
        version = client.api_version()

    assert version == ApiVersion(protocol=0, major=1, minor=45)
    assert serial.writes == [b"$M<\x00\x01\x01"]
    assert serial.flushed is True
    assert serial.closed is True


def test_api_version_requires_exact_payload_length() -> None:
    serial = FakeSerial([response_frame(MSP_API_VERSION, b"\x01\x2d")])

    with pytest.raises(MspFrameError, match="exactly 3"):
        MspClient(serial).api_version()


@pytest.mark.parametrize(
    ("raw_angles", "expected"),
    [
        ((123, 456, 270), Attitude(roll=12.3, pitch=45.6, yaw=270.0)),
        ((-123, -456, -30), Attitude(roll=-12.3, pitch=-45.6, yaw=-30.0)),
        ((0, 0, 0), Attitude(roll=0.0, pitch=0.0, yaw=0.0)),
    ],
)
def test_decodes_attitude_values(
    raw_angles: tuple[int, int, int],
    expected: Attitude,
) -> None:
    payload = pack("<hhh", *raw_angles)
    serial = FakeSerial([response_frame(MSP_ATTITUDE, payload)])

    assert MspClient(serial).attitude() == expected
    assert serial.writes == [encode_request(MSP_ATTITUDE)]


@pytest.mark.parametrize("payload", [b"", b"\x00" * 5, b"\x00" * 7])
def test_attitude_requires_exact_payload_length(payload: bytes) -> None:
    serial = FakeSerial([response_frame(MSP_ATTITUDE, payload)])

    with pytest.raises(MspFrameError, match="exactly 6"):
        MspClient(serial).attitude()


def test_decodes_status_with_extended_flight_mode_flags() -> None:
    fixed_payload = pack(
        "<HHHIBHHB",
        125,
        3,
        int(Sensor.ACCELEROMETER | Sensor.GYROSCOPE),
        0x00000005,
        1,
        42,
        0,
        1,
    )
    trailing_payload = b"\x02" + pack("<BIB", 26, 0x02004080, 1)
    serial = FakeSerial([response_frame(MSP_STATUS, fixed_payload + trailing_payload)])

    assert MspClient(serial).status() == FlightControllerStatus(
        pid_cycle_time_us=125,
        i2c_error_count=3,
        sensors=Sensor.ACCELEROMETER | Sensor.GYROSCOPE,
        flight_mode_flags=0x0000000200000005,
        pid_profile_index=1,
        system_load_percent=42,
        gyro_cycle_time_us=0,
        arming_disable_flag_count=26,
        arming_disable_flags=0x02004080,
        configuration_state_flags=1,
    )


def test_status_reports_reboot_required() -> None:
    payload = pack("<HHHIBHHBBIB", 100, 0, 0, 0, 0, 5, 0, 0, 26, 0, 1)
    serial = FakeSerial([response_frame(MSP_STATUS, payload)])

    assert MspClient(serial).status().reboot_required is True


@pytest.mark.parametrize("payload", [b"", b"\x00" * 15, b"\x00" * 21, b"\x00" * 23])
def test_status_rejects_malformed_payload(payload: bytes) -> None:
    serial = FakeSerial([response_frame(MSP_STATUS, payload)])

    with pytest.raises(MspFrameError, match="MSP_STATUS payload"):
        MspClient(serial).status()


@pytest.mark.parametrize(
    ("raw_values", "expected"),
    [
        ((252, 1234, 768, 345, 2518), AnalogTelemetry(25.18, 25.2, 1234, 768, 3.45)),
        ((0, 0, 0, -125, 0), AnalogTelemetry(0.0, 0.0, 0, 0, -1.25)),
    ],
)
def test_decodes_analog_telemetry(
    raw_values: tuple[int, int, int, int, int],
    expected: AnalogTelemetry,
) -> None:
    payload = pack("<BHHhH", *raw_values)
    serial = FakeSerial([response_frame(MSP_ANALOG, payload)])

    assert MspClient(serial).analog() == expected
    assert serial.writes == [encode_request(MSP_ANALOG)]


@pytest.mark.parametrize("payload", [b"", b"\x00" * 8, b"\x00" * 10])
def test_analog_requires_exact_payload_length(payload: bytes) -> None:
    serial = FakeSerial([response_frame(MSP_ANALOG, payload)])

    with pytest.raises(MspFrameError, match="exactly 9"):
        MspClient(serial).analog()
