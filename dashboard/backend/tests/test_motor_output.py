from __future__ import annotations

from threading import Event

from uav_dashboard.motor_output import BenchMotorOutput


class FakeMotorService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.started = Event()
        self.finished = Event()
        self.cancelled = False
        self.motor: str | None = None

    def run_bench_motor_pulse(
        self,
        motor: str,
        *,
        output: int,
        duration: float,
        cancel_event: Event,
    ) -> None:
        self.motor = motor
        assert output == 1050
        assert duration == 0.15
        self.started.set()
        if self.error is not None:
            raise self.error
        self.cancelled = cancel_event.wait(timeout=1.0)
        self.finished.set()


def test_motor_output_can_cancel_active_worker() -> None:
    service = FakeMotorService()
    output = BenchMotorOutput(service)  # type: ignore[arg-type]

    output.start("M3")
    assert service.started.wait(timeout=1.0)
    output.stop()
    assert service.finished.wait(timeout=1.0)
    output.shutdown()

    assert service.motor == "M3"
    assert service.cancelled is True
    assert output.fault is None


def test_motor_output_exposes_worker_failure() -> None:
    service = FakeMotorService(error=OSError("UART disconnected"))
    output = BenchMotorOutput(service)  # type: ignore[arg-type]

    output.start("M1")
    assert service.started.wait(timeout=1.0)
    output.shutdown()

    assert output.fault == "UART disconnected"
