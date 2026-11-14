from pathlib import Path

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
