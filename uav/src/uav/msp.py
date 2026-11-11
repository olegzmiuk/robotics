"""Minimal read-only MultiWii Serial Protocol (MSP) v1 client."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol, cast

MSP_API_VERSION = 1
MSP_REQUEST_HEADER = b"$M<"
MSP_RESPONSE_HEADER = b"$M>"
MSP_ERROR_HEADER = b"$M!"


class MspError(Exception):
    """Base class for MSP protocol and transport errors."""


class MspTimeoutError(MspError):
    """Raised when the serial stream ends before a complete frame arrives."""


class MspFrameError(MspError):
    """Raised when an MSP frame is malformed or corrupted."""


class MspUnexpectedCommandError(MspError):
    """Raised when a response does not match the requested command."""


class MspCommandError(MspError):
    """Raised when Betaflight returns an MSP error response."""


class SerialConnection(Protocol):
    """Small serial interface used by the client and its test doubles."""

    def read(self, size: int = 1) -> bytes: ...

    def write(self, data: bytes) -> int: ...

    def flush(self) -> None: ...

    def close(self) -> None: ...


SerialFactory = Callable[..., SerialConnection]


@dataclass(frozen=True)
class MspResponse:
    """A validated MSP v1 response frame."""

    command: int
    payload: bytes


@dataclass(frozen=True)
class ApiVersion:
    """Version information returned by MSP_API_VERSION."""

    protocol: int
    major: int
    minor: int

    @property
    def api(self) -> str:
        return f"{self.major}.{self.minor}"


def xor_checksum(data: bytes) -> int:
    """Return the MSP v1 XOR checksum for length, command, and payload bytes."""
    checksum = 0
    for byte in data:
        checksum ^= byte
    return checksum


def encode_request(command: int, payload: bytes = b"") -> bytes:
    """Encode one host-to-controller MSP v1 request."""
    if not 0 <= command <= 255:
        raise ValueError("command must fit in one byte")
    if len(payload) > 255:
        raise ValueError("MSP v1 payload must not exceed 255 bytes")

    body = bytes((len(payload), command)) + payload
    return MSP_REQUEST_HEADER + body + bytes((xor_checksum(body),))


def _read_exact(connection: SerialConnection, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = connection.read(size - len(data))
        if not chunk:
            raise MspTimeoutError(f"serial timeout after {len(data)} of {size} expected bytes")
        data.extend(chunk)
    return bytes(data)


def read_response(connection: SerialConnection, expected_command: int) -> MspResponse:
    """Read and validate exactly one MSP v1 response from a serial byte stream."""
    header = _read_exact(connection, 3)
    if header not in (MSP_RESPONSE_HEADER, MSP_ERROR_HEADER):
        raise MspFrameError(f"invalid MSP response header: {header!r}")

    payload_length = _read_exact(connection, 1)[0]
    command = _read_exact(connection, 1)[0]
    payload = _read_exact(connection, payload_length)
    received_checksum = _read_exact(connection, 1)[0]
    checksum_data = bytes((payload_length, command)) + payload
    expected_checksum = xor_checksum(checksum_data)

    if received_checksum != expected_checksum:
        raise MspFrameError(
            "invalid MSP checksum: "
            f"received 0x{received_checksum:02x}, expected 0x{expected_checksum:02x}"
        )
    if command != expected_command:
        raise MspUnexpectedCommandError(
            f"received MSP command {command}, expected {expected_command}"
        )
    if header == MSP_ERROR_HEADER:
        raise MspCommandError(f"Betaflight rejected MSP command {command}")

    return MspResponse(command=command, payload=payload)


class MspClient:
    """Synchronous read-only MSP v1 client over a serial connection."""

    def __init__(self, connection: SerialConnection) -> None:
        self._connection = connection

    @classmethod
    def connect(
        cls,
        port: str,
        baud_rate: int = 115200,
        timeout: float = 2.0,
        *,
        serial_factory: SerialFactory | None = None,
    ) -> MspClient:
        """Open a serial port and return a client that owns the connection."""
        if baud_rate <= 0:
            raise ValueError("baud_rate must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        if serial_factory is None:
            import serial  # type: ignore[import-untyped]

            serial_factory = cast(SerialFactory, serial.Serial)

        connection = serial_factory(
            port=port,
            baudrate=baud_rate,
            timeout=timeout,
            bytesize=8,
            parity="N",
            stopbits=1,
            xonxoff=False,
            rtscts=False,
            dsrdtr=False,
        )
        return cls(connection)

    def close(self) -> None:
        """Close the underlying serial connection."""
        self._connection.close()

    def __enter__(self) -> MspClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def request(self, command: int, payload: bytes = b"") -> MspResponse:
        """Send one MSP v1 request and wait for its validated response."""
        frame = encode_request(command, payload)
        written = self._connection.write(frame)
        if written != len(frame):
            raise MspError(f"serial write accepted {written} of {len(frame)} bytes")
        self._connection.flush()
        return read_response(self._connection, command)

    def api_version(self) -> ApiVersion:
        """Read and decode Betaflight's MSP API version."""
        response = self.request(MSP_API_VERSION)
        if len(response.payload) != 3:
            raise MspFrameError(
                "MSP_API_VERSION payload must contain exactly 3 bytes, "
                f"received {len(response.payload)}"
            )
        protocol, major, minor = response.payload
        return ApiVersion(protocol=protocol, major=major, minor=minor)
