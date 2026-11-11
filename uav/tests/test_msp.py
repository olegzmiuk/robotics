from __future__ import annotations

from collections.abc import Iterable

import pytest
from uav.msp import (
    MSP_API_VERSION,
    ApiVersion,
    MspClient,
    MspCommandError,
    MspFrameError,
    MspTimeoutError,
    MspUnexpectedCommandError,
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
