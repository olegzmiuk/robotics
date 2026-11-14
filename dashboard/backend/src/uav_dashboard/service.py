"""Serial-backed telemetry snapshots for the web dashboard."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter
from types import TracebackType
from typing import Protocol, TypeVar

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
    Sensor,
)

from uav_dashboard.models import (
    AnalogData,
    ApiVersionData,
    AttitudeData,
    CommandLogEntry,
    StatusData,
    TelemetrySnapshot,
)

Result = TypeVar("Result")


class TelemetryClient(Protocol):
    """Read-only client surface required by the dashboard."""

    def __enter__(self) -> TelemetryClient: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def api_version(self) -> ApiVersion: ...

    def status(self) -> FlightControllerStatus: ...

    def attitude(self) -> Attitude: ...

    def analog(self) -> AnalogTelemetry: ...


class TelemetryService:
    """Own serial access and return complete, sequential telemetry snapshots."""

    def __init__(
        self,
        port: str = "/dev/ttyAMA0",
        baud_rate: int = 115200,
        timeout: float = 2.0,
        *,
        client_factory: Callable[[], TelemetryClient] | None = None,
        command_log_size: int = 100,
    ) -> None:
        if command_log_size < 1:
            raise ValueError("command_log_size must be at least 1")

        self.port = port
        self._client_factory = client_factory or (
            lambda: MspClient.connect(port=port, baud_rate=baud_rate, timeout=timeout)
        )
        self._serial_lock = Lock()
        self._state_lock = Lock()
        self._command_log: deque[CommandLogEntry] = deque(maxlen=command_log_size)
        self._connected = False
        self._stream_active = False
        self._stream_client_context: TelemetryClient | None = None
        self._stream_client: TelemetryClient | None = None
        self._stream_api_version: ApiVersion | None = None
        self._stream_status: FlightControllerStatus | None = None
        self._stream_analog: AnalogTelemetry | None = None

    @property
    def connected(self) -> bool:
        """Return the result of the most recent telemetry attempt."""
        with self._state_lock:
            return self._connected

    def command_log(self) -> list[CommandLogEntry]:
        """Return newest command records first."""
        with self._state_lock:
            return list(reversed(self._command_log))

    def read_snapshot(self) -> TelemetrySnapshot:
        """Read one complete snapshot without interleaving serial requests."""
        with self._serial_lock:
            if self._stream_active:
                return self._read_stream_snapshot_locked(refresh_slow=True)

            try:
                with self._client_factory() as client:
                    snapshot = self._read_complete_snapshot(client)
            except Exception:
                self._set_connected(False)
                raise

            self._set_connected(True)
            return snapshot

    def start_stream(self) -> None:
        """Enable persistent serial access for WebSocket telemetry."""
        with self._serial_lock:
            self._stream_active = True

    def stop_stream(self) -> None:
        """Close the persistent serial client and clear cached telemetry."""
        with self._serial_lock:
            self._stream_active = False
            self._close_stream_client_locked()
            self._set_connected(False)

    def read_stream_snapshot(self, *, refresh_slow: bool = False) -> TelemetrySnapshot:
        """Read fast attitude and optionally refresh slow telemetry fields."""
        with self._serial_lock:
            if not self._stream_active:
                raise RuntimeError("telemetry stream is not active")
            return self._read_stream_snapshot_locked(refresh_slow=refresh_slow)

    def _read_stream_snapshot_locked(self, *, refresh_slow: bool) -> TelemetrySnapshot:
        try:
            client = self._ensure_stream_client_locked()
            if self._stream_api_version is None:
                self._stream_api_version = self._execute(
                    "MSP_API_VERSION", MSP_API_VERSION, client.api_version
                )
            if refresh_slow or self._stream_status is None:
                self._stream_status = self._execute("MSP_STATUS", MSP_STATUS, client.status)
            if refresh_slow or self._stream_analog is None:
                self._stream_analog = self._execute("MSP_ANALOG", MSP_ANALOG, client.analog)

            attitude = self._execute("MSP_ATTITUDE", MSP_ATTITUDE, client.attitude)
            snapshot = self._build_snapshot(
                self._stream_api_version,
                self._stream_status,
                attitude,
                self._stream_analog,
            )
        except Exception:
            self._set_connected(False)
            self._close_stream_client_locked()
            raise

        self._set_connected(True)
        return snapshot

    def _ensure_stream_client_locked(self) -> TelemetryClient:
        if self._stream_client is None:
            context = self._client_factory()
            client = context.__enter__()
            self._stream_client_context = context
            self._stream_client = client
        return self._stream_client

    def _close_stream_client_locked(self) -> None:
        context = self._stream_client_context
        self._stream_client_context = None
        self._stream_client = None
        self._stream_api_version = None
        self._stream_status = None
        self._stream_analog = None
        if context is not None:
            context.__exit__(None, None, None)

    def _read_complete_snapshot(self, client: TelemetryClient) -> TelemetrySnapshot:
        api_version = self._execute("MSP_API_VERSION", MSP_API_VERSION, client.api_version)
        status = self._execute("MSP_STATUS", MSP_STATUS, client.status)
        attitude = self._execute("MSP_ATTITUDE", MSP_ATTITUDE, client.attitude)
        analog = self._execute("MSP_ANALOG", MSP_ANALOG, client.analog)
        return self._build_snapshot(api_version, status, attitude, analog)

    def _execute(
        self,
        command: str,
        command_id: int,
        operation: Callable[[], Result],
    ) -> Result:
        started_at = perf_counter()
        try:
            result = operation()
        except Exception as error:
            self._record_command(command, command_id, started_at, error)
            raise

        self._record_command(command, command_id, started_at)
        return result

    def _record_command(
        self,
        command: str,
        command_id: int,
        started_at: float,
        error: Exception | None = None,
    ) -> None:
        entry = CommandLogEntry(
            timestamp=datetime.now(timezone.utc),
            command=command,
            command_id=command_id,
            ok=error is None,
            duration_ms=round((perf_counter() - started_at) * 1000, 3),
            error=str(error) if error else None,
        )
        with self._state_lock:
            self._command_log.append(entry)

    def _set_connected(self, connected: bool) -> None:
        with self._state_lock:
            self._connected = connected

    def _build_snapshot(
        self,
        api_version: ApiVersion,
        status: FlightControllerStatus,
        attitude: Attitude,
        analog: AnalogTelemetry,
    ) -> TelemetrySnapshot:
        sensor_names = [
            sensor.name or f"0x{sensor.value:x}" for sensor in Sensor if status.sensors & sensor
        ]
        return TelemetrySnapshot(
            captured_at=datetime.now(timezone.utc),
            connected=True,
            serial_port=self.port,
            api_version=ApiVersionData(
                protocol=api_version.protocol,
                major=api_version.major,
                minor=api_version.minor,
                display=api_version.api,
            ),
            status=StatusData(
                pid_cycle_time_us=status.pid_cycle_time_us,
                i2c_error_count=status.i2c_error_count,
                sensors=int(status.sensors),
                sensor_names=sensor_names,
                flight_mode_flags=f"0x{status.flight_mode_flags:x}",
                pid_profile_index=status.pid_profile_index,
                system_load_percent=status.system_load_percent,
                gyro_cycle_time_us=status.gyro_cycle_time_us,
                arming_disable_flag_count=status.arming_disable_flag_count,
                arming_disable_flags=status.arming_disable_flags,
                configuration_state_flags=status.configuration_state_flags,
                reboot_required=status.reboot_required,
            ),
            attitude=AttitudeData(
                roll=attitude.roll,
                pitch=attitude.pitch,
                yaw=attitude.yaw,
            ),
            analog=AnalogData(
                battery_voltage=analog.battery_voltage,
                legacy_battery_voltage=analog.legacy_battery_voltage,
                milliamp_hours_drawn=analog.milliamp_hours_drawn,
                rssi=analog.rssi,
                current=analog.current,
            ),
        )
