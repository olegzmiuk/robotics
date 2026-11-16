from __future__ import annotations

import pytest
from uav.bench_motor import MSP_SET_MOTOR
from uav.bench_motor_cli import ENABLE_ENVIRONMENT_VALUE, ENABLE_ENVIRONMENT_VARIABLE, main
from uav.msp import MspResponse


class FakeClient:
    def __init__(self) -> None:
        self.requests: list[tuple[int, bytes]] = []
        self.closed = False

    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.closed = True

    def request(self, command: int, payload: bytes = b"") -> MspResponse:
        self.requests.append((command, payload))
        return MspResponse(command=command, payload=b"")


SAFETY_FLAGS = [
    "--confirm-propellers-removed",
    "--confirm-frame-secured",
    "--confirm-controller-disarmed",
    "--confirm-battery-disconnect-ready",
]


def command_args() -> list[str]:
    return ["--port", "/dev/fake", "--motor", "M2", *SAFETY_FLAGS]


def decode_outputs(payload: bytes) -> tuple[int, ...]:
    return tuple(int.from_bytes(payload[index : index + 2], "little") for index in range(0, 8, 2))


def test_cli_requires_every_operator_confirmation(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(ENABLE_ENVIRONMENT_VARIABLE, ENABLE_ENVIRONMENT_VALUE)

    with pytest.raises(SystemExit) as error:
        main(["--port", "/dev/fake", "--motor", "M1"])

    assert error.value.code == 2


def test_cli_requires_separate_environment_opt_in(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delenv(ENABLE_ENVIRONMENT_VARIABLE, raising=False)

    with pytest.raises(SystemExit, match="physical output disabled"):
        main(command_args())


def test_cli_runs_one_motor_and_stops_before_and_after(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient()
    monkeypatch.setenv(ENABLE_ENVIRONMENT_VARIABLE, ENABLE_ENVIRONMENT_VALUE)
    monkeypatch.setattr("uav.bench_motor_cli.MspClient.connect", lambda *_: client)
    monkeypatch.setattr("uav.bench_motor.sleep", lambda _: None)

    assert main(command_args()) == 0

    assert [command for command, _ in client.requests] == [MSP_SET_MOTOR] * 3
    assert [decode_outputs(payload) for _, payload in client.requests] == [
        (1000, 1000, 1000, 1000),
        (1000, 1050, 1000, 1000),
        (1000, 1000, 1000, 1000),
    ]
    assert client.closed is True
    assert "all motor outputs returned to STOP" in capsys.readouterr().out


def test_cli_preserves_adapter_output_limit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient()
    monkeypatch.setenv(ENABLE_ENVIRONMENT_VARIABLE, ENABLE_ENVIRONMENT_VALUE)
    monkeypatch.setattr("uav.bench_motor_cli.MspClient.connect", lambda *_: client)

    with pytest.raises(SystemExit, match="pulse output"):
        main([*command_args(), "--output", "1101"])

    assert client.requests == []
    assert client.closed is True
