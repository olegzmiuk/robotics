import type { CameraStatusData, CommandLogEntry, TelemetrySnapshot } from "./types";

async function getJson<Result>(path: string): Promise<Result> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return (await response.json()) as Result;
}

export function fetchTelemetry(): Promise<TelemetrySnapshot> {
  return getJson<TelemetrySnapshot>("/api/telemetry");
}

export function fetchCommandLog(): Promise<CommandLogEntry[]> {
  return getJson<CommandLogEntry[]>("/api/commands");
}

export function fetchCameraStatus(): Promise<CameraStatusData> {
  return getJson<CameraStatusData>("/api/camera/status");
}

export function telemetryWebSocketUrl(): string {
  const url = new URL("/api/telemetry/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
