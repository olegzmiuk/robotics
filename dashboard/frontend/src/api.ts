import type {
  CameraStatusData,
  CommandLogEntry,
  TelemetrySnapshot,
  VisionResponseLogEntry,
  VisionResponseStateData,
} from "./types";

async function getJson<Result>(path: string): Promise<Result> {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `Request failed with status ${response.status}`);
  }
  return (await response.json()) as Result;
}

async function postJson<Result>(path: string, body: object): Promise<Result> {
  const response = await fetch(path, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const responseBody = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(responseBody?.detail ?? `Request failed with status ${response.status}`);
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

export function fetchVisionResponse(): Promise<VisionResponseStateData> {
  return getJson<VisionResponseStateData>("/api/vision-response");
}

export function fetchVisionResponseJournal(): Promise<VisionResponseLogEntry[]> {
  return getJson<VisionResponseLogEntry[]>("/api/vision-response/journal");
}

export function setVisionResponseEnabled(enabled: boolean): Promise<VisionResponseStateData> {
  return postJson<VisionResponseStateData>("/api/vision-response/enabled", { enabled });
}

export function telemetryWebSocketUrl(): string {
  const url = new URL("/api/telemetry/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function cameraDetectionWebSocketUrl(): string {
  const url = new URL("/api/camera/detection/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function visionResponseWebSocketUrl(): string {
  const url = new URL("/api/vision-response/ws", window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}
