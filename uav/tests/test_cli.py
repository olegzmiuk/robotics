from datetime import datetime
from pathlib import Path

from uav.cli import default_output_path


def test_default_output_path_contains_timestamp() -> None:
    now = datetime(2026, 11, 9, 14, 37, 22, 184532)

    output = default_output_path(now)

    assert output == Path("uav/artifacts/annotated_14:37:22:184532.jpg")
