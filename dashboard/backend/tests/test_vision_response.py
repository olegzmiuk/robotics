from __future__ import annotations

from datetime import datetime, timezone

from uav_dashboard.models import CameraDetectionData, TelemetryStreamData, TelemetryStreamError
from uav_dashboard.vision_response import VisionResponseController


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def detection_event(
    status: str = "detected",
    *,
    motor: str | None = "M1",
    quadrant: str | None = "top_left",
) -> CameraDetectionData:
    return CameraDetectionData.model_validate(
        {
            "status": status,
            "captured_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "frame_width": 1280,
            "frame_height": 720,
            "simulated_motor": motor,
            "quadrant": quadrant,
        }
    )


def mark_uart_healthy(controller: VisionResponseController) -> None:
    controller.observe_telemetry(TelemetryStreamData.model_construct(snapshot=None))


def test_controller_is_disabled_and_produces_no_output_by_default() -> None:
    controller = VisionResponseController()

    controller.observe_detection(detection_event())
    state = controller.snapshot()

    assert state.state == "disabled"
    assert state.enabled is False
    assert state.selected_motor is None
    assert state.simulated_only is True


def test_fresh_target_runs_one_pulse_then_requires_target_clear() -> None:
    clock = FakeClock()
    controller = VisionResponseController(
        pulse_duration=0.2,
        cooldown_duration=1.0,
        clock=clock,
    )
    mark_uart_healthy(controller)
    controller.set_enabled(True)

    controller.observe_detection(detection_event())
    pulsing = controller.snapshot()
    clock.advance(0.2)
    cooldown = controller.snapshot()
    clock.advance(1.0)
    ready_latched = controller.snapshot()
    controller.observe_detection(detection_event())
    still_ready = controller.snapshot()
    controller.observe_detection(detection_event("stale", motor=None, quadrant=None))
    controller.observe_detection(detection_event())
    second_pulse = controller.snapshot()

    assert pulsing.state == "simulated_pulse"
    assert pulsing.selected_motor == "M1"
    assert cooldown.state == "cooldown"
    assert cooldown.selected_motor is None
    assert ready_latched.state == "ready"
    assert ready_latched.target_latched is True
    assert still_ready.state == "ready"
    assert second_pulse.state == "simulated_pulse"
    assert [entry.event for entry in controller.journal()].count("pulse_started") == 2


def test_stable_quadrant_transition_selects_new_motor_after_cooldown() -> None:
    clock = FakeClock()
    controller = VisionResponseController(
        pulse_duration=0.2,
        cooldown_duration=1.0,
        quadrant_dwell=0.25,
        clock=clock,
    )
    mark_uart_healthy(controller)
    controller.set_enabled(True)
    controller.observe_detection(detection_event())

    clock.advance(0.05)
    controller.observe_detection(detection_event(motor="M2", quadrant="top_right"))
    during_first_pulse = controller.snapshot()
    clock.advance(0.15)
    cooldown = controller.snapshot()
    clock.advance(1.0)
    ready = controller.snapshot()
    controller.observe_detection(detection_event(motor="M2", quadrant="top_right"))
    second_pulse = controller.snapshot()

    assert during_first_pulse.state == "simulated_pulse"
    assert during_first_pulse.selected_motor == "M1"
    assert during_first_pulse.pending_quadrant == "top_right"
    assert cooldown.state == "cooldown"
    assert cooldown.selected_motor is None
    assert ready.state == "ready"
    assert second_pulse.state == "simulated_pulse"
    assert second_pulse.selected_motor == "M2"
    assert second_pulse.quadrant == "top_right"


def test_holding_does_not_start_and_stale_aborts_active_pulse() -> None:
    controller = VisionResponseController()
    mark_uart_healthy(controller)
    controller.set_enabled(True)

    controller.observe_detection(detection_event("holding"))
    assert controller.snapshot().state == "ready"

    controller.observe_detection(detection_event())
    controller.observe_detection(detection_event("stale", motor=None, quadrant=None))
    state = controller.snapshot()

    assert state.state == "ready"
    assert state.selected_motor is None
    assert state.target_latched is False
    assert controller.journal()[0].event == "aborted"


def test_uart_error_enters_fault_and_clears_simulated_output() -> None:
    controller = VisionResponseController()
    mark_uart_healthy(controller)
    controller.set_enabled(True)
    controller.observe_detection(detection_event())

    controller.observe_telemetry(
        TelemetryStreamError(
            code="uart_disconnected",
            message="serial timeout",
            serial_port="/dev/ttyAMA0",
        )
    )
    state = controller.snapshot()

    assert state.state == "fault"
    assert state.selected_motor is None
    assert state.target_latched is True
    assert state.reason == "UART unavailable: serial timeout"


def test_camera_shutdown_enters_fault_and_clears_simulated_output() -> None:
    controller = VisionResponseController()
    mark_uart_healthy(controller)
    controller.set_enabled(True)
    controller.observe_detection(detection_event())

    camera_stopped = detection_event("error", motor=None, quadrant=None).model_copy(
        update={"error": "Camera stream stopped"}
    )
    controller.observe_detection(camera_stopped)
    state = controller.snapshot()

    assert state.state == "fault"
    assert state.selected_motor is None
    assert state.reason == "Camera stream stopped"


def test_disabling_always_clears_output_and_latch() -> None:
    controller = VisionResponseController()
    mark_uart_healthy(controller)
    controller.set_enabled(True)
    controller.observe_detection(detection_event())

    state = controller.set_enabled(False)

    assert state.state == "disabled"
    assert state.enabled is False
    assert state.selected_motor is None
    assert state.target_latched is False


def test_enabling_without_uart_is_fail_closed() -> None:
    controller = VisionResponseController()

    state = controller.set_enabled(True)

    assert state.state == "fault"
    assert state.selected_motor is None
    assert state.reason == "Waiting for healthy UART telemetry"
