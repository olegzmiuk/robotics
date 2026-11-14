"""Typed API models exposed by the UAV dashboard backend."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FrozenModel(BaseModel):
    """Base API model whose fields cannot be changed after creation."""

    model_config = ConfigDict(frozen=True)


class ApiVersionData(FrozenModel):
    protocol: int
    major: int
    minor: int
    display: str


class StatusData(FrozenModel):
    pid_cycle_time_us: int
    i2c_error_count: int
    sensors: int
    sensor_names: list[str]
    flight_mode_flags: str
    pid_profile_index: int
    system_load_percent: int
    gyro_cycle_time_us: int
    arming_disable_flag_count: int
    arming_disable_flags: int
    configuration_state_flags: int
    reboot_required: bool


class AttitudeData(FrozenModel):
    roll: float
    pitch: float
    yaw: float


class AnalogData(FrozenModel):
    battery_voltage: float
    legacy_battery_voltage: float
    milliamp_hours_drawn: int
    rssi: int
    current: float


class TelemetrySnapshot(FrozenModel):
    captured_at: datetime
    connected: bool
    serial_port: str
    api_version: ApiVersionData
    status: StatusData
    attitude: AttitudeData
    analog: AnalogData


class CommandLogEntry(FrozenModel):
    timestamp: datetime
    command: str
    command_id: int
    ok: bool
    duration_ms: float
    error: str | None = None


class HealthData(FrozenModel):
    status: str
    flight_controller_connected: bool
    serial_port: str
