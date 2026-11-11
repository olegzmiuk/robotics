from __future__ import annotations

import pytest
from uav.msp import ApiVersion, Attitude
from uav.msp_cli import format_attitude, main


class FakeClient:
    def __init__(self) -> None:
        self.closed = False
        self.attitude_calls = 0

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def api_version(self) -> ApiVersion:
        return ApiVersion(protocol=0, major=1, minor=45)

    def attitude(self) -> Attitude:
        self.attitude_calls += 1
        return Attitude(roll=12.3, pitch=-4.5, yaw=270.0)


def test_cli_prints_api_version(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("uav.msp_cli.MspClient.connect", lambda *_: FakeClient())

    assert main(["--port", "/dev/fake"]) == 0
    assert capsys.readouterr().out == "MSP API: 1.45 (protocol version 0)\n"


def test_formats_attitude() -> None:
    attitude = Attitude(roll=12.3, pitch=-4.5, yaw=270.0)

    assert format_attitude(attitude) == "Roll: 12.3°, Pitch: -4.5°, Yaw: 270.0°"


def test_cli_prints_attitude_once(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("uav.msp_cli.MspClient.connect", lambda *_: FakeClient())

    assert main(["--port", "/dev/fake", "--command", "attitude"]) == 0
    assert capsys.readouterr().out == "Roll: 12.3°, Pitch: -4.5°, Yaw: 270.0°\n"


def test_watch_repeats_until_keyboard_interrupt(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient()
    intervals: list[float] = []

    monkeypatch.setattr("uav.msp_cli.MspClient.connect", lambda *_: client)

    def interrupt_after_first_poll(interval: float) -> None:
        intervals.append(interval)
        raise KeyboardInterrupt

    monkeypatch.setattr("uav.msp_cli.time.sleep", interrupt_after_first_poll)

    assert (
        main(
            [
                "--port",
                "/dev/fake",
                "--command",
                "attitude",
                "--watch",
                "--poll-interval",
                "0.25",
            ]
        )
        == 0
    )
    assert client.attitude_calls == 1
    assert client.closed is True
    assert intervals == [0.25]
    assert capsys.readouterr().out.endswith("Stopped.\n")


def test_watch_rejects_api_version_command() -> None:
    with pytest.raises(SystemExit) as error:
        main(["--port", "/dev/fake", "--watch"])

    assert error.value.code == 2
