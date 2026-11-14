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
        return ApiVersion(protocol=0, major=1, minor=45)

    def status(self) -> FlightControllerStatus:
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
        return Attitude(roll=12.3, pitch=-4.5, yaw=270.0)

    def analog(self) -> AnalogTelemetry:
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
