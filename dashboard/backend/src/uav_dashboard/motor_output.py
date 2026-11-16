"""Threaded, bounded physical motor output for verified bench deployments."""

from __future__ import annotations

from threading import Event, Lock, Thread, current_thread

from uav.bench_motor import (
    MAX_BENCH_MOTOR_VALUE,
    MAX_BENCH_PULSE_DURATION,
    MOTOR_STOP_VALUE,
    BenchMotor,
)

from uav_dashboard.service import TelemetryService


class BenchMotorOutput:
    """Run at most one cancellable bench pulse without blocking the event loop."""

    def __init__(
        self,
        service: TelemetryService,
        *,
        output: int = 1050,
        duration: float = 0.15,
    ) -> None:
        if not MOTOR_STOP_VALUE < output <= MAX_BENCH_MOTOR_VALUE:
            raise ValueError(
                f"motor output must be above {MOTOR_STOP_VALUE} and at most {MAX_BENCH_MOTOR_VALUE}"
            )
        if not 0 < duration <= MAX_BENCH_PULSE_DURATION:
            raise ValueError(
                f"motor pulse duration must be positive and at most "
                f"{MAX_BENCH_PULSE_DURATION} seconds"
            )
        self._service = service
        self._output = output
        self._duration = duration
        self._lock = Lock()
        self._cancel_event: Event | None = None
        self._thread: Thread | None = None
        self._fault: str | None = None

    @property
    def fault(self) -> str | None:
        with self._lock:
            return self._fault

    def start(self, motor: BenchMotor) -> None:
        """Start one pulse; reject overlap instead of changing motors mid-pulse."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("a physical motor pulse is already active")
            cancel_event = Event()
            thread = Thread(
                target=self._run,
                args=(motor, cancel_event),
                name="uav-bench-motor",
                daemon=True,
            )
            self._fault = None
            self._cancel_event = cancel_event
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        """Request early STOP; the pulse worker retains exclusive serial ownership."""
        with self._lock:
            cancel_event = self._cancel_event
        if cancel_event is not None:
            cancel_event.set()

    def shutdown(self) -> None:
        """Request STOP and wait briefly for the final MSP acknowledgement."""
        self.stop()
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not current_thread():
            thread.join(timeout=1.0)
            if thread.is_alive():
                with self._lock:
                    self._fault = "motor STOP did not complete within one second"

    def _run(self, motor: BenchMotor, cancel_event: Event) -> None:
        try:
            self._service.run_bench_motor_pulse(
                motor,
                output=self._output,
                duration=self._duration,
                cancel_event=cancel_event,
            )
        except Exception as error:
            with self._lock:
                self._fault = str(error)
        finally:
            with self._lock:
                if self._thread is current_thread():
                    self._thread = None
                    self._cancel_event = None
