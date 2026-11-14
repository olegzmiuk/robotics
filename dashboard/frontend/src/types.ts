export interface ApiVersionData {
  protocol: number;
  major: number;
  minor: number;
  display: string;
}

export interface StatusData {
  pid_cycle_time_us: number;
  i2c_error_count: number;
  sensors: number;
  sensor_names: string[];
  flight_mode_flags: string;
  pid_profile_index: number;
  system_load_percent: number;
  gyro_cycle_time_us: number;
  arming_disable_flag_count: number;
  arming_disable_flags: number;
  configuration_state_flags: number;
  reboot_required: boolean;
}

export interface AttitudeData {
  roll: number;
  pitch: number;
  yaw: number;
}

export interface AnalogData {
  battery_voltage: number;
  legacy_battery_voltage: number;
  milliamp_hours_drawn: number;
  rssi: number;
  current: number;
}

export interface TelemetrySnapshot {
  captured_at: string;
  connected: boolean;
  serial_port: string;
  api_version: ApiVersionData;
  status: StatusData;
  attitude: AttitudeData;
  analog: AnalogData;
}

export interface CommandLogEntry {
  timestamp: string;
  command: string;
  command_id: number;
  ok: boolean;
  duration_ms: number;
  error: string | null;
}

export interface CameraStatusData {
  state: "idle" | "starting" | "streaming" | "error";
  subscribers: number;
  width: number;
  height: number;
  framerate: number;
  rotation: number;
  error: string | null;
}

export type TelemetryStreamEvent =
  | { type: "telemetry"; snapshot: TelemetrySnapshot }
  | { type: "error"; message: string };
