"""Simulation-only state machine for a future bounded bench response."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from time import monotonic
from typing import Literal

from uav_dashboard.models import (
    CameraDetectionData,
    TelemetryStreamData,
    TelemetryStreamError,
    VisionResponseLogEntry,
    VisionResponseStateData,
)

ResponseState = Literal["disabled", "ready", "simulated_pulse", "cooldown", "fault"]


class VisionResponseController:
    """Model a conservative motor response without producing physical output."""

    def __init__(
        self,
        *,
        pulse_duration: float = 0.2,
        cooldown_duration: float = 1.0,
        quadrant_dwell: float = 0.25,
        journal_size: int = 100,
        clock: Callable[[], float] = monotonic,
        now_factory: Callable[[], datetime] | None = None,
    ) -> None:
        if pulse_duration <= 0:
            raise ValueError("simulated pulse duration must be positive")
        if cooldown_duration <= 0:
            raise ValueError("simulated cooldown duration must be positive")
        if quadrant_dwell <= 0:
            raise ValueError("quadrant dwell duration must be positive")
        if journal_size < 1:
            raise ValueError("vision response journal size must be at least 1")

        self._pulse_duration = pulse_duration
        self._cooldown_duration = cooldown_duration
        self._quadrant_dwell = quadrant_dwell
        self._clock = clock
        self._now_factory = now_factory or (lambda: datetime.now(timezone.utc))
        self._journal: deque[VisionResponseLogEntry] = deque(maxlen=journal_size)
        self._enabled = False
        self._state: ResponseState = "disabled"
        self._selected_motor: Literal["M1", "M2", "M3", "M4"] | None = None
        self._quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None = (
            None
        )
        self._deadline: float | None = None
        self._target_latched = False
        self._latched_quadrant: (
            Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None
        ) = None
        self._pending_quadrant: (
            Literal["top_left", "top_right", "bottom_left", "bottom_right"] | None
        ) = None
        self._pending_since: float | None = None
        self._reason = "Simulation disabled"
        self._fault_source: Literal["camera", "uart"] | None = None
        self._telemetry_available = False
        self._updated_at = self._now_factory()

    def set_enabled(self, enabled: bool) -> VisionResponseStateData:
        """Explicitly enable or disable the simulation state machine."""
        now = self._clock()
        self._advance(now)
        if enabled == self._enabled:
            return self._snapshot(now)

        self._enabled = enabled
        self._selected_motor = None
        self._quadrant = None
        self._deadline = None
        self._clear_target_tracking()
        self._fault_source = None
        if enabled:
            if self._telemetry_available:
                self._transition(
                    "ready",
                    "Simulation enabled; waiting for a fresh target",
                    "enabled",
                )
            else:
                self._fault_source = "uart"
                self._target_latched = True
                self._transition("fault", "Waiting for healthy UART telemetry", "enabled")
        else:
            self._transition("disabled", "Simulation disabled; output is zero", "disabled")
        return self._snapshot(now)

    def observe_detection(self, event: CameraDetectionData) -> None:
        """Consume one camera decision and update the simulated response."""
        now = self._clock()
        self._advance(now)

        if event.status == "error":
            self._target_latched = True
            self._enter_fault("camera", event.error or "Camera processing failed")
            return

        if event.status in ("none", "stale"):
            self._clear_target_tracking()
            if self._enabled and self._state == "simulated_pulse":
                self._selected_motor = None
                self._quadrant = None
                self._deadline = None
                self._transition(
                    "ready",
                    "Target became stale; simulated output cleared",
                    "aborted",
                )
            elif self._enabled and self._state == "ready":
                self._reason = "Waiting for a fresh target"
                self._touch()
            return

        if event.status != "detected" or event.simulated_motor is None or event.quadrant is None:
            return
        if not self._enabled or self._fault_source is not None:
            return
        self._observe_quadrant(event.quadrant, now)
        if self._state != "ready":
            return

        transition_ready = (
            self._pending_quadrant == event.quadrant
            and self._pending_since is not None
            and now - self._pending_since >= self._quadrant_dwell
        )
        if self._target_latched and not transition_ready:
            return

        self._target_latched = True
        self._latched_quadrant = event.quadrant
        self._pending_quadrant = None
        self._pending_since = None
        self._selected_motor = event.simulated_motor
        self._quadrant = event.quadrant
        self._deadline = now + self._pulse_duration
        self._transition(
            "simulated_pulse",
            f"Fresh target selected {event.simulated_motor} in {event.quadrant}",
            "pulse_started",
        )

    def _observe_quadrant(
        self,
        quadrant: Literal["top_left", "top_right", "bottom_left", "bottom_right"],
        now: float,
    ) -> None:
        if not self._target_latched or quadrant == self._latched_quadrant:
            self._pending_quadrant = None
            self._pending_since = None
            return
        if quadrant != self._pending_quadrant:
            self._pending_quadrant = quadrant
            self._pending_since = now

    def observe_telemetry(self, event: TelemetryStreamData | TelemetryStreamError) -> None:
        """Fail closed on UART errors and recover only after fresh telemetry."""
        self._advance(self._clock())
        if isinstance(event, TelemetryStreamError):
            self._telemetry_available = False
            self._target_latched = True
            self._enter_fault("uart", f"UART unavailable: {event.message}")
            return
        self._telemetry_available = True
        if self._fault_source == "uart":
            self._fault_source = None
            if self._enabled:
                self._transition(
                    "ready",
                    "UART recovered; clear the target before another simulation",
                    "fault_cleared",
                )
            else:
                self._transition("disabled", "Simulation disabled; output is zero", "fault_cleared")

    def snapshot(self) -> VisionResponseStateData:
        """Return current state, advancing pulse and cooldown deadlines first."""
        now = self._clock()
        self._advance(now)
        return self._snapshot(now)

    def journal(self) -> list[VisionResponseLogEntry]:
        return list(reversed(self._journal))

    def _advance(self, now: float) -> None:
        if self._deadline is None or now < self._deadline:
            return
        if self._state == "simulated_pulse":
            self._selected_motor = None
            self._quadrant = None
            self._deadline = now + self._cooldown_duration
            self._transition(
                "cooldown",
                "Simulated pulse complete; output is zero",
                "pulse_completed",
            )
        elif self._state == "cooldown":
            self._deadline = None
            reason = (
                "Cooldown complete; hold a new quadrant or clear the target"
                if self._target_latched
                else "Cooldown complete; waiting for a fresh target"
            )
            self._transition("ready", reason, "cooldown_completed")

    def _enter_fault(self, source: Literal["camera", "uart"], reason: str) -> None:
        if not self._enabled:
            return
        self._clear_target_tracking()
        self._target_latched = True
        self._fault_source = source
        self._selected_motor = None
        self._quadrant = None
        self._deadline = None
        self._transition("fault", reason, "fault")

    def _transition(self, state: ResponseState, reason: str, event: str) -> None:
        self._state = state
        self._reason = reason
        self._touch()
        self._journal.append(
            VisionResponseLogEntry(
                timestamp=self._updated_at,
                event=event,
                state=state,
                selected_motor=self._selected_motor,
                quadrant=self._quadrant,
                reason=reason,
            )
        )

    def _touch(self) -> None:
        self._updated_at = self._now_factory()

    def _clear_target_tracking(self) -> None:
        self._target_latched = False
        self._latched_quadrant = None
        self._pending_quadrant = None
        self._pending_since = None

    def _snapshot(self, now: float) -> VisionResponseStateData:
        pulse_remaining = 0.0
        cooldown_remaining = 0.0
        quadrant_dwell_remaining = 0.0
        if self._deadline is not None:
            remaining_ms = round(max(0.0, self._deadline - now) * 1000, 1)
            if self._state == "simulated_pulse":
                pulse_remaining = remaining_ms
            elif self._state == "cooldown":
                cooldown_remaining = remaining_ms
        if self._pending_since is not None:
            quadrant_dwell_remaining = round(
                max(0.0, self._quadrant_dwell - (now - self._pending_since)) * 1000,
                1,
            )
        return VisionResponseStateData(
            state=self._state,
            enabled=self._enabled,
            updated_at=self._updated_at,
            selected_motor=self._selected_motor,
            quadrant=self._quadrant,
            pulse_remaining_ms=pulse_remaining,
            cooldown_remaining_ms=cooldown_remaining,
            target_latched=self._target_latched,
            pending_quadrant=self._pending_quadrant,
            quadrant_dwell_remaining_ms=quadrant_dwell_remaining,
            reason=self._reason,
        )
