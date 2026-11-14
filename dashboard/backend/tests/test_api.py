import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_service import FakeClient
from uav_dashboard.api import create_app
from uav_dashboard.models import CameraDetectionData, CameraStatusData, TelemetryStreamData
from uav_dashboard.service import TelemetryService
from uav_dashboard.vision_response import VisionResponseController


class FakeCamera:
    def __init__(self) -> None:
        self.stopped = False
        self.detection_unsubscribed = False

    def status(self) -> CameraStatusData:
        return CameraStatusData(
            state="streaming",
            subscribers=1,
            width=640,
            height=360,
            framerate=12,
            rotation=180,
        )

    async def stream(self) -> AsyncIterator[bytes]:
        yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\nimage\r\n"

    def subscribe_detections(self) -> asyncio.Queue[CameraDetectionData]:
        queue: asyncio.Queue[CameraDetectionData] = asyncio.Queue(maxsize=1)
        queue.put_nowait(
            CameraDetectionData(
                status="detected",
                captured_at=datetime.now(timezone.utc),
                last_detected_at=datetime.now(timezone.utc),
                age_ms=0,
                frame_width=640,
                frame_height=360,
                center={"x": 120, "y": 80},
                bounding_box={"x": 80, "y": 40, "width": 80, "height": 80},
                area=6400,
                quadrant="top_left",
                simulated_motor="M1",
            )
        )
        return queue

    async def unsubscribe_detections(self, queue: asyncio.Queue[CameraDetectionData]) -> None:
        del queue
        self.detection_unsubscribed = True

    async def stop(self) -> None:
        self.stopped = True


def test_api_returns_telemetry_and_command_log(tmp_path: Path) -> None:
    service = TelemetryService(port="/dev/fake", client_factory=FakeClient)
    client = TestClient(create_app(service, frontend_dist=tmp_path / "missing"))

    telemetry_response = client.get("/api/telemetry")
    commands_response = client.get("/api/commands")

    assert telemetry_response.status_code == 200
    assert telemetry_response.json()["api_version"]["display"] == "1.45"
    assert telemetry_response.json()["status"]["sensor_names"] == [
        "ACCELEROMETER",
        "GYROSCOPE",
    ]
    assert commands_response.status_code == 200
    assert len(commands_response.json()) == 4


def test_api_enables_and_disables_simulated_vision_response(tmp_path: Path) -> None:
    service = TelemetryService(port="/dev/fake", client_factory=FakeClient)
    response_controller = VisionResponseController()
    response_controller.observe_telemetry(TelemetryStreamData.model_construct(snapshot=None))

    with TestClient(
        create_app(
            service,
            vision_response_controller=response_controller,
            frontend_dist=tmp_path / "missing",
        )
    ) as client:
        initial = client.get("/api/vision-response")
        enabled = client.post("/api/vision-response/enabled", json={"enabled": True})
        with client.websocket_connect("/api/vision-response/ws") as websocket:
            streamed = websocket.receive_json()
        journal = client.get("/api/vision-response/journal")
        disabled = client.post("/api/vision-response/enabled", json={"enabled": False})

    assert initial.json()["state"] == "disabled"
    assert enabled.json()["state"] == "ready"
    assert enabled.json()["simulated_only"] is True
    assert streamed["state"] == "ready"
    assert journal.json()[0]["event"] == "enabled"
    assert disabled.json()["state"] == "disabled"


def test_api_returns_503_for_serial_failure(tmp_path: Path) -> None:
    service = TelemetryService(
        port="/dev/fake",
        client_factory=lambda: FakeClient(fail_status=True),
    )
    client = TestClient(create_app(service, frontend_dist=tmp_path / "missing"))

    response = client.get("/api/telemetry")

    assert response.status_code == 503
    assert response.json() == {"detail": "status timeout"}


def test_api_returns_camera_status_and_mjpeg_stream(tmp_path: Path) -> None:
    camera = FakeCamera()
    service = TelemetryService(port="/dev/fake", client_factory=FakeClient)

    with TestClient(
        create_app(service, camera_service=camera, frontend_dist=tmp_path / "missing")
    ) as client:
        status_response = client.get("/api/camera/status")
        stream_response = client.get("/api/camera/stream")

    assert status_response.status_code == 200
    assert status_response.json()["state"] == "streaming"
    assert status_response.json()["width"] == 640
    assert stream_response.status_code == 200
    assert stream_response.headers["content-type"].startswith("multipart/x-mixed-replace")
    assert stream_response.content.endswith(b"image\r\n")
    assert camera.stopped is True


def test_websocket_streams_telemetry(tmp_path: Path) -> None:
    service = TelemetryService(port="/dev/fake", client_factory=FakeClient)

    with (
        TestClient(create_app(service, frontend_dist=tmp_path / "missing")) as client,
        client.websocket_connect("/api/telemetry/ws") as websocket,
    ):
        event = websocket.receive_json()

    assert event["type"] == "telemetry"
    assert event["snapshot"]["attitude"] == {
        "roll": 12.3,
        "pitch": -4.5,
        "yaw": 270.0,
    }


def test_websocket_streams_camera_detection_decisions(tmp_path: Path) -> None:
    camera = FakeCamera()
    service = TelemetryService(port="/dev/fake", client_factory=FakeClient)

    with (
        TestClient(
            create_app(service, camera_service=camera, frontend_dist=tmp_path / "missing")
        ) as client,
        client.websocket_connect("/api/camera/detection/ws") as websocket,
    ):
        event = websocket.receive_json()

    assert event["type"] == "detection"
    assert event["status"] == "detected"
    assert event["quadrant"] == "top_left"
    assert event["simulated_motor"] == "M1"
    assert camera.detection_unsubscribed is True


def test_websocket_keeps_running_after_serial_failure(tmp_path: Path) -> None:
    service = TelemetryService(
        port="/dev/fake",
        client_factory=lambda: FakeClient(fail_status=True),
    )

    with (
        TestClient(create_app(service, frontend_dist=tmp_path / "missing")) as client,
        client.websocket_connect("/api/telemetry/ws") as websocket,
    ):
        event = websocket.receive_json()

    assert event == {
        "type": "error",
        "code": "uart_disconnected",
        "message": "status timeout",
        "serial_port": "/dev/fake",
    }


def test_websocket_reconnects_with_a_new_serial_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UAV_TELEMETRY_RECONNECT_INTERVAL", "0.1")
    clients = [FakeClient(fail_status=True), FakeClient()]
    client_iterator = iter(clients)
    service = TelemetryService(
        port="/dev/fake",
        client_factory=lambda: next(client_iterator),
    )

    with (
        TestClient(create_app(service, frontend_dist=tmp_path / "missing")) as client,
        client.websocket_connect("/api/telemetry/ws") as websocket,
    ):
        error_event = websocket.receive_json()
        telemetry_event = websocket.receive_json()

    assert error_event == {
        "type": "error",
        "code": "uart_disconnected",
        "message": "status timeout",
        "serial_port": "/dev/fake",
    }
    assert telemetry_event["type"] == "telemetry"
    assert clients[0].closed is True
    assert clients[1].closed is True
