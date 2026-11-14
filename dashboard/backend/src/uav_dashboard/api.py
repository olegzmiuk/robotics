"""FastAPI application for the Raspberry Pi UAV dashboard."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from uav.msp import MspError

from uav_dashboard.camera import MJPEG_BOUNDARY, CameraService, CameraSource
from uav_dashboard.models import (
    CameraStatusData,
    CommandLogEntry,
    HealthData,
    TelemetrySnapshot,
    VisionResponseEnabledRequest,
    VisionResponseLogEntry,
    VisionResponseStateData,
)
from uav_dashboard.service import TelemetryService
from uav_dashboard.stream import TelemetryBroadcaster
from uav_dashboard.vision_response import VisionResponseController


def create_app(
    telemetry_service: TelemetryService | None = None,
    *,
    camera_service: CameraSource | None = None,
    vision_response_controller: VisionResponseController | None = None,
    frontend_dist: Path | None = None,
) -> FastAPI:
    """Create an API app with injectable hardware access for tests."""
    service = telemetry_service or TelemetryService(
        port=os.getenv("UAV_SERIAL_PORT", "/dev/ttyAMA0"),
        baud_rate=int(os.getenv("UAV_SERIAL_BAUD_RATE", "115200")),
        timeout=float(os.getenv("UAV_SERIAL_TIMEOUT", "2.0")),
    )
    response_controller = vision_response_controller or VisionResponseController(
        pulse_duration=float(os.getenv("UAV_SIMULATED_PULSE_DURATION", "0.2")),
        cooldown_duration=float(os.getenv("UAV_SIMULATED_PULSE_COOLDOWN", "1.0")),
        quadrant_dwell=float(os.getenv("UAV_SIMULATED_QUADRANT_DWELL", "0.25")),
    )
    broadcaster = TelemetryBroadcaster(
        service,
        interval=float(os.getenv("UAV_TELEMETRY_INTERVAL", "0.1")),
        slow_interval=float(os.getenv("UAV_SLOW_TELEMETRY_INTERVAL", "1.0")),
        reconnect_interval=float(os.getenv("UAV_TELEMETRY_RECONNECT_INTERVAL", "1.0")),
        event_observer=response_controller.observe_telemetry,
    )
    camera = camera_service or CameraService(
        executable=os.getenv("UAV_CAMERA_COMMAND", "rpicam-vid"),
        camera_index=int(os.getenv("UAV_CAMERA_INDEX", "0")),
        width=int(os.getenv("UAV_CAMERA_WIDTH", "1280")),
        height=int(os.getenv("UAV_CAMERA_HEIGHT", "720")),
        framerate=int(os.getenv("UAV_CAMERA_FRAMERATE", "15")),
        quality=int(os.getenv("UAV_CAMERA_JPEG_QUALITY", "70")),
        rotation=int(os.getenv("UAV_CAMERA_ROTATION", "180")),
        detection_stale_timeout=float(os.getenv("UAV_DETECTION_STALE_TIMEOUT", "0.4")),
        detection_observer=response_controller.observe_detection,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        broadcaster.start()
        try:
            yield
        finally:
            await camera.stop()
            await broadcaster.stop()

    app = FastAPI(title="UAV Dashboard API", version="0.4.0", lifespan=lifespan)

    allowed_origins = os.getenv(
        "UAV_DASHBOARD_CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in allowed_origins if origin.strip()],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthData)
    async def health() -> HealthData:
        return HealthData(
            status="ok",
            flight_controller_connected=service.connected,
            serial_port=service.port,
        )

    @app.get("/api/telemetry", response_model=TelemetrySnapshot)
    async def telemetry() -> TelemetrySnapshot:
        try:
            return await asyncio.to_thread(service.read_snapshot)
        except (MspError, OSError, ValueError) as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/api/commands", response_model=list[CommandLogEntry])
    async def commands() -> list[CommandLogEntry]:
        return service.command_log()

    @app.get("/api/vision-response", response_model=VisionResponseStateData)
    async def vision_response() -> VisionResponseStateData:
        return response_controller.snapshot()

    @app.get("/api/vision-response/journal", response_model=list[VisionResponseLogEntry])
    async def vision_response_journal() -> list[VisionResponseLogEntry]:
        return response_controller.journal()

    @app.post("/api/vision-response/enabled", response_model=VisionResponseStateData)
    async def set_vision_response_enabled(
        request: VisionResponseEnabledRequest,
    ) -> VisionResponseStateData:
        return response_controller.set_enabled(request.enabled)

    @app.websocket("/api/vision-response/ws")
    async def vision_response_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_text(response_controller.snapshot().model_dump_json())
                await asyncio.sleep(0.05)
        except WebSocketDisconnect:
            pass

    @app.get("/api/camera/status", response_model=CameraStatusData)
    async def camera_status() -> CameraStatusData:
        return camera.status()

    @app.get("/api/camera/stream")
    async def camera_stream() -> StreamingResponse:
        return StreamingResponse(
            camera.stream(),
            media_type=f"multipart/x-mixed-replace; boundary={MJPEG_BOUNDARY.decode()}",
            headers={"Cache-Control": "no-store"},
        )

    @app.websocket("/api/camera/detection/ws")
    async def camera_detection_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = camera.subscribe_detections()
        try:
            while True:
                event = await queue.get()
                await websocket.send_text(event.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            await camera.unsubscribe_detections(queue)

    @app.websocket("/api/telemetry/ws")
    async def telemetry_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = broadcaster.subscribe()
        try:
            while True:
                event = await queue.get()
                await websocket.send_text(event.model_dump_json())
        except WebSocketDisconnect:
            pass
        finally:
            broadcaster.unsubscribe(queue)

    static_directory = frontend_dist or (Path(__file__).resolve().parents[3] / "frontend" / "dist")
    if static_directory.is_dir():
        app.mount("/", StaticFiles(directory=static_directory, html=True), name="dashboard")

    return app


app = create_app()
