from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_service import FakeClient
from uav_dashboard.api import create_app
from uav_dashboard.service import TelemetryService


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


def test_api_returns_503_for_serial_failure(tmp_path: Path) -> None:
    service = TelemetryService(
        port="/dev/fake",
        client_factory=lambda: FakeClient(fail_status=True),
    )
    client = TestClient(create_app(service, frontend_dist=tmp_path / "missing"))

    response = client.get("/api/telemetry")

    assert response.status_code == 503
    assert response.json() == {"detail": "status timeout"}


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

    assert event == {"type": "error", "message": "status timeout"}


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

    assert error_event == {"type": "error", "message": "status timeout"}
    assert telemetry_event["type"] == "telemetry"
    assert clients[0].closed is True
    assert clients[1].closed is True
