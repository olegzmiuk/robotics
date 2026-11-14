from __future__ import annotations

from types import TracebackType

import pytest
from uav.msp import (
    AnalogTelemetry,
    ApiVersion,
    Attitude,
    FlightControllerStatus,
    MspTimeoutError,
    Sensor,
)
from uav_dashboard.service import TelemetryService


class FakeClient:
    def __init__(self, *, fail_status: bool = False) -> None:
        self.fail_status = fail_status
        self.closed = False
        self.api_version_calls = 0
        self.status_calls = 0
        self.attitude_calls = 0
        self.analog_calls = 0

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.closed = True

    def api_version(self) -> ApiVersion:
        self.api_version_calls += 1
        return ApiVersion(protocol=0, major=1, minor=45)

    def status(self) -> FlightControllerStatus:
        self.status_calls += 1
        if self.fail_status:
            raise MspTimeoutError("status timeout")
        return FlightControllerStatus(
            pid_cycle_time_us=125,
            i2c_error_count=0,
            sensors=Sensor.ACCELEROMETER | Sensor.GYROSCOPE,
            flight_mode_flags=0,
            pid_profile_index=0,
            system_load_percent=5,
            gyro_cycle_time_us=0,
            arming_disable_flag_count=26,
            arming_disable_flags=0x80,
            configuration_state_flags=0,
        )

    def attitude(self) -> Attitude:
        self.attitude_calls += 1
        return Attitude(roll=12.3, pitch=-4.5, yaw=270.0)

    def analog(self) -> AnalogTelemetry:
        self.analog_calls += 1
        return AnalogTelemetry(
            battery_voltage=25.18,
            legacy_battery_voltage=25.2,
            milliamp_hours_drawn=1234,
            rssi=768,
            current=3.45,
        )


def test_reads_complete_snapshot_and_records_commands() -> None:
    client = FakeClient()
    service = TelemetryService(port="/dev/fake", client_factory=lambda: client)

    snapshot = service.read_snapshot()

    assert snapshot.connected is True
    assert snapshot.serial_port == "/dev/fake"
    assert snapshot.api_version.display == "1.45"
    assert snapshot.status.sensor_names == ["ACCELEROMETER", "GYROSCOPE"]
    assert snapshot.attitude.roll == 12.3
    assert snapshot.analog.battery_voltage == 25.18
    assert service.connected is True
    assert client.closed is True
    assert [entry.command for entry in reversed(service.command_log())] == [
        "MSP_API_VERSION",
        "MSP_STATUS",
        "MSP_ATTITUDE",
        "MSP_ANALOG",
    ]


def test_failed_command_marks_service_disconnected_and_is_logged() -> None:
    client = FakeClient(fail_status=True)
    service = TelemetryService(port="/dev/fake", client_factory=lambda: client)

    with pytest.raises(MspTimeoutError, match="status timeout"):
        service.read_snapshot()

    assert service.connected is False
    assert client.closed is True
    latest = service.command_log()[0]
    assert latest.command == "MSP_STATUS"
    assert latest.ok is False
    assert latest.error == "status timeout"


def test_stream_reuses_connection_and_schedules_fast_and_slow_commands() -> None:
    clients: list[FakeClient] = []

    def create_client() -> FakeClient:
        client = FakeClient()
        clients.append(client)
        return client

    service = TelemetryService(port="/dev/fake", client_factory=create_client)

    service.start_stream()
    first = service.read_stream_snapshot()
    second = service.read_stream_snapshot()
    third = service.read_stream_snapshot(refresh_slow=True)
    service.stop_stream()

    assert first.attitude.roll == second.attitude.roll == third.attitude.roll == 12.3
    assert len(clients) == 1
    assert clients[0].api_version_calls == 1
    assert clients[0].status_calls == 2
    assert clients[0].analog_calls == 2
    assert clients[0].attitude_calls == 3
    assert clients[0].closed is True
    assert service.connected is False


def test_manual_snapshot_reuses_active_stream_connection() -> None:
    client = FakeClient()
    service = TelemetryService(port="/dev/fake", client_factory=lambda: client)

    service.start_stream()
    service.read_stream_snapshot()
    service.read_snapshot()
    service.stop_stream()

    assert client.api_version_calls == 1
    assert client.status_calls == 2
    assert client.analog_calls == 2
    assert client.attitude_calls == 2


def test_stream_reconnects_after_command_failure() -> None:
    clients = [FakeClient(fail_status=True), FakeClient()]
    client_iterator = iter(clients)
    service = TelemetryService(port="/dev/fake", client_factory=lambda: next(client_iterator))

    service.start_stream()
    with pytest.raises(MspTimeoutError, match="status timeout"):
        service.read_stream_snapshot()

    snapshot = service.read_stream_snapshot()
    service.stop_stream()

    assert clients[0].closed is True
    assert clients[1].api_version_calls == 1
    assert clients[1].status_calls == 1
    assert snapshot.connected is True
    assert clients[1].closed is True
