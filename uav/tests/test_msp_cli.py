from __future__ import annotations

from uav.msp import ApiVersion
from uav.msp_cli import main


class FakeClient:
    def __enter__(self) -> FakeClient:
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def api_version(self) -> ApiVersion:
        return ApiVersion(protocol=0, major=1, minor=45)


def test_cli_prints_api_version(monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("uav.msp_cli.MspClient.connect", lambda *_: FakeClient())

    assert main(["--port", "/dev/fake"]) == 0
    assert capsys.readouterr().out == "MSP API: 1.45 (protocol version 0)\n"
