"""Typed API models exposed by the UAV dashboard backend."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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


class CameraStatusData(FrozenModel):
    state: Literal["idle", "starting", "streaming", "error"]
    subscribers: int
    width: int
    height: int
    framerate: int
    rotation: int
    error: str | None = None


class CameraPointData(FrozenModel):
    x: int
    y: int


class CameraBoundingBoxData(FrozenModel):
    x: int
    y: int
    width: int
    height: int


class CameraDetectionData(FrozenModel):
    """Latest square decision emitted by the camera pipeline."""

    type: Literal["detection"] = "detection"
    status: Literal["detected", "holding", "stale", "none", "error"]
    captured_at: datetime
    last_detected_at: datetime | None = None
    age_ms: float | None = None
    frame_width: int
    frame_height: int
    center: CameraPointData | None = None
    bounding_box: CameraBoundingBoxData | None = None
    area: float | None = None
    quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = None
    simulated_motor: Literal["M1", "M2", "M3", "M4"] | None = None
    error: str | None = None


class VisionResponseStateData(FrozenModel):
    """Current state of the simulation-only vision response controller."""

    state: Literal["disabled", "ready", "simulated_pulse", "cooldown", "fault"]
    enabled: bool
    simulated_only: Literal[True] = True
    updated_at: datetime
    selected_motor: Literal["M1", "M2", "M3", "M4"] | None = None
    quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = None
    pulse_remaining_ms: float = 0.0
    cooldown_remaining_ms: float = 0.0
    target_latched: bool = False
    pending_quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = None
    quadrant_dwell_remaining_ms: float = 0.0
    reason: str


class VisionResponseLogEntry(FrozenModel):
    timestamp: datetime
    event: str
    state: Literal["disabled", "ready", "simulated_pulse", "cooldown", "fault"]
    selected_motor: Literal["M1", "M2", "M3", "M4"] | None = None
    quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = None
    reason: str


class VisionResponseEnabledRequest(FrozenModel):
    enabled: bool


class TelemetryStreamData(FrozenModel):
    """Successful telemetry event sent over the dashboard WebSocket."""

    type: Literal["telemetry"] = "telemetry"
    snapshot: TelemetrySnapshot


class TelemetryStreamError(FrozenModel):
    """Recoverable telemetry error sent while the stream remains open."""

    type: Literal["error"] = "error"
    code: Literal["uart_disconnected", "telemetry_error"]
    message: str
    serial_port: str
