import { useCallback, useEffect, useRef, useState } from "react";
import {
  cameraDetectionWebSocketUrl,
  fetchCameraStatus,
  fetchCommandLog,
  fetchTelemetry,
  telemetryWebSocketUrl,
} from "./api";
import type {
  CameraDetectionData,
  CameraStatusData,
  CommandLogEntry,
  TelemetryErrorData,
  TelemetrySnapshot,
  TelemetryStreamEvent,
} from "./types";

const COMMAND_LOG_INTERVAL_MS = 1_000;
const CAMERA_STATUS_INTERVAL_MS = 2_000;
const CAMERA_RETRY_INTERVAL_MS = 2_000;
const RECONNECT_INTERVAL_MS = 1_000;
const DETECTION_QUADRANTS = ["top_left", "top_right", "bottom_left", "bottom_right"] as const;
type StreamState = "connecting" | "live" | "offline";

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    fractionalSecondDigits: 1,
    hour12: false,
  }).format(new Date(value));
}

function hex(value: number): string {
  return `0x${value.toString(16).padStart(8, "0")}`;
}

function Metric({ label, value, unit }: { label: string; value: string | number; unit?: string }) {
  return (
    <div className="metric">
      <span className="metric-label">{label}</span>
      <span className="metric-value">
        {value}
        {unit && <span className="metric-unit"> {unit}</span>}
      </span>
    </div>
  );
}

function TelemetryAlert({ error }: { error: TelemetryErrorData }) {
  const uartDisconnected = error.code === "uart_disconnected";

  return (
    <section className={`telemetry-alert ${uartDisconnected ? "telemetry-alert-uart" : ""}`} role="alert">
      <div className="telemetry-alert-icon" aria-hidden="true">!</div>
      <div className="telemetry-alert-copy">
        <span className="eyebrow">{uartDisconnected ? "UART link failure" : "Telemetry error"}</span>
        <strong>{uartDisconnected ? "UART connection lost" : "Telemetry unavailable"}</strong>
        <p>
          {uartDisconnected
            ? "Check flight-controller power and the TX, RX, and GND wiring. Reconnecting automatically…"
            : error.message}
        </p>
      </div>
      <div className="telemetry-alert-detail">
        <code>{error.serial_port}</code>
        <span>{uartDisconnected ? error.message : "Retrying automatically"}</span>
      </div>
    </section>
  );
}

function AttitudeIndicator({ snapshot }: { snapshot: TelemetrySnapshot }) {
  const { roll, pitch, yaw } = snapshot.attitude;
  return (
    <section className="panel attitude-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">IMU orientation</span>
          <h2>Attitude</h2>
        </div>
        <span className="heading-readout">HDG {yaw.toFixed(0).padStart(3, "0")}°</span>
      </div>
      <div className="attitude-content">
        <div className="horizon" aria-label={`Roll ${roll} degrees, pitch ${pitch} degrees`}>
          <div
            className="horizon-plane"
            style={{ transform: `translateY(${pitch * 1.2}px) rotate(${-roll}deg)` }}
          />
          <div className="horizon-center">
            <span />
            <i />
            <span />
          </div>
          <div className="horizon-ring" />
        </div>
        <div className="attitude-values">
          <Metric label="Roll" value={roll.toFixed(1)} unit="°" />
          <Metric label="Pitch" value={pitch.toFixed(1)} unit="°" />
          <Metric label="Yaw" value={yaw.toFixed(1)} unit="°" />
        </div>
      </div>
    </section>
  );
}

function CameraDecision({ detection }: { detection: CameraDetectionData | null }) {
  const status = detection?.status ?? "none";
  const active = status === "detected" || status === "holding";
  const title = status === "detected"
    ? "Target detected"
    : status === "holding"
      ? "Holding last target"
      : status === "stale"
        ? "Target stale"
        : status === "error"
          ? "Vision unavailable"
          : "No target";
  const detail = active && detection?.quadrant
    ? detection.quadrant.replace("_", " ")
    : status === "stale"
      ? "Decision cleared"
      : status === "error"
        ? detection?.error ?? "Camera processing error"
        : "Waiting for square";

  return (
    <div className={`camera-decision camera-decision-${status}`}>
      <div className="camera-decision-heading">
        <div>
          <span>Vision response</span>
          <strong>{title}</strong>
        </div>
        <b>{active ? `SIM ${detection?.simulated_motor}` : "OUTPUT NONE"}</b>
      </div>
      <div className="camera-decision-body">
        <div className="quadrant-map" aria-label="Detected image quadrant">
          {DETECTION_QUADRANTS.map((quadrant) => (
            <span
              className={active && detection?.quadrant === quadrant ? "quadrant-active" : ""}
              key={quadrant}
            >
              {quadrant.split("_").map((part) => part[0]).join("").toUpperCase()}
            </span>
          ))}
        </div>
        <div className="camera-decision-copy">
          <strong>{detail}</strong>
          <small>
            {detection?.age_ms != null ? `${Math.round(detection.age_ms)} ms old` : "Simulation only"}
          </small>
        </div>
      </div>
    </div>
  );
}

function CameraPanel() {
  const [status, setStatus] = useState<CameraStatusData | null>(null);
  const [streamAttempt, setStreamAttempt] = useState(0);
  const [imageReady, setImageReady] = useState(false);
  const [imageFailed, setImageFailed] = useState(false);
  const [detection, setDetection] = useState<CameraDetectionData | null>(null);

  useEffect(() => {
    const updateStatus = async () => {
      try {
        setStatus(await fetchCameraStatus());
      } catch {
        setStatus(null);
      }
    };
    void updateStatus();
    const timer = window.setInterval(() => void updateStatus(), CAMERA_STATUS_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!imageFailed) return;
    const timer = window.setTimeout(() => {
      setImageFailed(false);
      setStreamAttempt((attempt) => attempt + 1);
    }, CAMERA_RETRY_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [imageFailed]);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (stopped) return;
      socket = new WebSocket(cameraDetectionWebSocketUrl());
      socket.addEventListener("message", (message) => {
        setDetection(JSON.parse(String(message.data)) as CameraDetectionData);
      });
      socket.addEventListener("close", () => {
        if (stopped) return;
        reconnectTimer = window.setTimeout(connect, CAMERA_RETRY_INTERVAL_MS);
      });
      socket.addEventListener("error", () => socket?.close());
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  const cameraLive = imageReady || (status?.state === "streaming" && !imageFailed);
  const stateLabel = cameraLive ? "Live" : status?.state === "error" ? "Unavailable" : "Connecting";
  const streamDetails = status
    ? `${status.width}×${status.height} · ${status.framerate} FPS · ${status.rotation}°`
    : "CAM 01";

  return (
    <section className="panel camera-panel">
      <div className="panel-heading camera-heading">
        <div>
          <span className="eyebrow">Computer vision</span>
          <h2>Camera Module 3</h2>
        </div>
        <span className={`badge ${cameraLive ? "badge-safe" : "badge-muted"}`}>{stateLabel}</span>
      </div>
      <div className="camera-viewport">
        <img
          key={streamAttempt}
          className={`camera-feed ${cameraLive ? "camera-feed-ready" : ""}`}
          src={`/api/camera/stream?attempt=${streamAttempt}`}
          alt="Live Raspberry Pi Camera Module 3 feed"
          onLoad={() => {
            setImageReady(true);
            setImageFailed(false);
          }}
          onError={() => {
            setImageReady(false);
            setImageFailed(true);
          }}
        />
        <div className="camera-grid" />
        <div className="camera-reticle"><span /></div>
        <CameraDecision detection={detection} />
        {!cameraLive && (
          <div className="camera-message">
            <span className="camera-icon">◉</span>
            <strong>{status?.state === "error" ? "Camera unavailable" : "Starting camera"}</strong>
            <small>{status?.error ?? "Waiting for the first MJPEG frame"}</small>
          </div>
        )}
        <span className="camera-label">
          {streamDetails} · SQUARE DETECTION · {cameraLive ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </section>
  );
}

function CommandLog({ entries }: { entries: CommandLogEntry[] }) {
  return (
    <section className="panel log-panel">
      <div className="panel-heading">
        <div>
          <span className="eyebrow">Read-only transport</span>
          <h2>MSP command activity</h2>
        </div>
        <span className="badge">Last {entries.length}</span>
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr><th>Time</th><th>Command</th><th>ID</th><th>Latency</th><th>Result</th></tr>
          </thead>
          <tbody>
            {entries.length === 0 ? (
              <tr><td colSpan={5} className="empty-cell">Waiting for the first telemetry poll…</td></tr>
            ) : entries.slice(0, 12).map((entry, index) => (
              <tr key={`${entry.timestamp}-${entry.command}-${index}`}>
                <td className="mono muted">{formatTime(entry.timestamp)}</td>
                <td className="command-name">{entry.command}</td>
                <td className="mono muted">{entry.command_id}</td>
                <td className="mono">{entry.duration_ms.toFixed(2)} ms</td>
                <td><span className={`result ${entry.ok ? "result-ok" : "result-error"}`}>{entry.ok ? "OK" : "ERROR"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function App() {
  const [snapshot, setSnapshot] = useState<TelemetrySnapshot | null>(null);
  const [commands, setCommands] = useState<CommandLogEntry[]>([]);
  const [error, setError] = useState<TelemetryErrorData | null>(null);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [streamRateHz, setStreamRateHz] = useState(0);
  const requestInFlight = useRef(false);
  const streamMessageCount = useRef(0);

  const refresh = useCallback(async () => {
    if (requestInFlight.current) return;
    requestInFlight.current = true;
    try {
      const nextSnapshot = await fetchTelemetry();
      setSnapshot(nextSnapshot);
      setError(null);
    } catch (requestError) {
      setError({
        code: "telemetry_error",
        message: requestError instanceof Error ? requestError.message : "Unknown telemetry error",
        serial_port: snapshot?.serial_port ?? "/dev/ttyAMA0",
      });
    }

    try {
      setCommands(await fetchCommandLog());
    } catch {
      // The primary telemetry error is more useful than a secondary log error.
    } finally {
      requestInFlight.current = false;
    }
  }, [snapshot?.serial_port]);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    const connect = () => {
      if (stopped) return;
      setStreamState("connecting");
      socket = new WebSocket(telemetryWebSocketUrl());

      socket.addEventListener("open", () => setStreamState("live"));
      socket.addEventListener("message", (message) => {
        const event = JSON.parse(String(message.data)) as TelemetryStreamEvent;
        if (event.type === "telemetry") {
          streamMessageCount.current += 1;
          setSnapshot(event.snapshot);
          setError(null);
        } else {
          setError(event);
        }
      });
      socket.addEventListener("close", () => {
        if (stopped) return;
        setStreamState("offline");
        reconnectTimer = window.setTimeout(connect, RECONNECT_INTERVAL_MS);
      });
      socket.addEventListener("error", () => socket?.close());
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setStreamRateHz(streamMessageCount.current);
      streamMessageCount.current = 0;
    }, 1_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    const updateCommands = async () => {
      try {
        setCommands(await fetchCommandLog());
      } catch {
        // Telemetry stream errors are displayed separately.
      }
    };
    void updateCommands();
    const timer = window.setInterval(() => void updateCommands(), COMMAND_LOG_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, []);

  const connected = snapshot?.connected === true && error === null && streamState === "live";
  const uartDisconnected = error?.code === "uart_disconnected";

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark"><span /></div>
          <div>
            <span className="eyebrow">Raspberry Pi 5 · SpeedyBee F405 V4</span>
            <h1>UAV Ground Station</h1>
          </div>
        </div>
        <div className="topbar-actions">
          <div className={`connection ${connected ? "connection-online" : "connection-offline"}`}>
            <span className="status-dot" />
            <div><strong>{connected ? "Flight controller online" : uartDisconnected ? "UART disconnected" : streamState === "connecting" ? "Connecting telemetry" : "Flight controller offline"}</strong><small>{error?.serial_port ?? snapshot?.serial_port ?? "/dev/ttyAMA0"} · {streamState === "live" ? `${streamRateHz} Hz` : streamState}</small></div>
          </div>
          <button type="button" onClick={() => void refresh()}>
            <span>↻</span> Refresh
          </button>
        </div>
      </header>

      {error && <TelemetryAlert error={error} />}

      <section className="summary-strip">
        <div><span>MSP API</span><strong>{snapshot?.api_version.display ?? "—"}</strong></div>
        <div><span>FC CPU load</span><strong>{snapshot ? `${snapshot.status.system_load_percent}%` : "—"}</strong></div>
        <div><span>PID cycle</span><strong>{snapshot ? `${snapshot.status.pid_cycle_time_us} µs` : "—"}</strong></div>
        <div><span>Last sample</span><strong>{snapshot ? formatTime(snapshot.captured_at) : "—"}</strong></div>
      </section>

      <div className="dashboard-grid">
        <CameraPanel />
        {snapshot ? <AttitudeIndicator snapshot={snapshot} /> : <section className="panel skeleton-panel" />}

        <section className="panel power-panel">
          <div className="panel-heading"><div><span className="eyebrow">Power system</span><h2>Battery telemetry</h2></div><span className="badge badge-safe">Read only</span></div>
          <div className="metric-grid">
            <Metric label="Voltage" value={snapshot?.analog.battery_voltage.toFixed(2) ?? "—"} unit="V" />
            <Metric label="Current" value={snapshot?.analog.current.toFixed(2) ?? "—"} unit="A" />
            <Metric label="Consumed" value={snapshot?.analog.milliamp_hours_drawn ?? "—"} unit="mAh" />
            <Metric label="RSSI raw" value={snapshot?.analog.rssi ?? "—"} />
          </div>
        </section>

        <section className="panel fc-panel">
          <div className="panel-heading"><div><span className="eyebrow">Flight controller</span><h2>System state</h2></div><span className={`badge ${error ? "badge-error" : snapshot?.status.reboot_required ? "badge-warn" : "badge-safe"}`}>{uartDisconnected ? "UART offline" : error ? "Telemetry error" : snapshot?.status.reboot_required ? "Reboot required" : "Configuration ready"}</span></div>
          <div className="metric-grid compact">
            <Metric label="I2C errors" value={snapshot?.status.i2c_error_count ?? "—"} />
            <Metric label="PID profile" value={snapshot?.status.pid_profile_index ?? "—"} />
            <Metric label="Mode flags" value={snapshot?.status.flight_mode_flags ?? "—"} />
            <Metric label="Arming disabled" value={snapshot ? hex(snapshot.status.arming_disable_flags) : "—"} />
          </div>
          <div className="sensor-list">
            <span className="metric-label">Detected sensors</span>
            <div>{snapshot?.status.sensor_names.length ? snapshot.status.sensor_names.map((sensor) => <span className="sensor-chip" key={sensor}>{sensor}</span>) : <span className="muted">No sensor data</span>}</div>
          </div>
        </section>

        <CommandLog entries={commands} />
      </div>

      <footer><span>Read-only MSP telemetry</span><span>No ARM · No motor control · No configuration writes</span></footer>
    </main>
  );
}
