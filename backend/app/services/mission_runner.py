"""In-process mission runner.

Hosts the whole simulated mission inside the backend and steps it on a timer, so
the dashboard can drive the demo end to end with no second terminal:

    IDLE -> COUNTDOWN -> ASCENT -> ORBIT_INSERTION -> DEPLOYMENT -> ORBIT -> STOPPED

The launch phases replay a deterministic ascent profile (`telemetry.launch`) at an
accelerated time scale and publish `launch` snapshots and `launch_milestone`
events for the 2D view. ASTRIX's anomaly detector is trained on on-orbit
telemetry, so it is deliberately *not* fed ascent data; it engages at ORBIT, once
the satellite has been released and has settled. Faults can only be injected
into a satellite that exists, i.e. in ORBIT — a scenario requested at start is
queued and injected at handover.

The runner also closes the control loop. Approved recovery actions are queued by
the pipeline; the runner drains that queue and applies each command to the
simulator, so vibration really does collapse and pointing really does re-converge
after an operator clicks approve.

The external CLI simulator (`python -m telemetry.simulator --stream`) remains the
more honest architecture — spacecraft and ground segment in separate processes,
talking over HTTP — and both paths drive the same pipeline. They must not feed
the same spacecraft at the same time; the telemetry endpoint refuses that.
"""

from __future__ import annotations

import asyncio
import logging

from ..core.schemas import TelemetryFrame
from .pipeline import AstrixPipeline

log = logging.getLogger(__name__)

LAUNCH_TICK_SECONDS = 0.1  # wall-clock seconds between launch snapshots


class MissionError(RuntimeError):
    """A control request that is invalid in the mission's current state."""


class MissionRunner:
    def __init__(
        self,
        pipeline: AstrixPipeline,
        spacecraft_id: str = "ASTRIX-01",
        hardware=None,
        model_lab=None,
    ) -> None:
        self.pipeline = pipeline
        self.hardware = hardware  # HardwareHub | None — real sensors blended into telemetry
        self.model_lab = model_lab  # ModelLab | None — captures the end-of-mission summary
        self._vehicle: dict | None = None
        self._plan = None
        self.spacecraft_id = spacecraft_id
        self._sim = None
        self._launch = None
        self._task: asyncio.Task | None = None
        self._interval = 0.4
        self._dt = 1.0
        self._settle_frames = 30
        self._seed: int | None = 42
        self._time_scale = 20.0
        self._queued_scenario: str | None = None
        self.phase = "IDLE"
        self.frames_emitted = 0
        self.commands_applied: list[dict] = []
        self.milestones: list[dict] = []
        self.launch_state: dict | None = None
        self.error: str | None = None

    # -- lifecycle ---------------------------------------------------------- #

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def in_orbit(self) -> bool:
        return self.running and self.phase == "ORBIT" and self._sim is not None

    async def start(
        self,
        interval: float = 0.4,
        dt: float = 1.0,
        scenario: str | None = None,
        settle_frames: int = 30,
        seed: int | None = 42,
        include_launch: bool = True,
        launch_time_scale: float = 20.0,
        launch_fault: str | None = None,
        vehicle: dict | None = None,
    ) -> dict:
        """Start (or restart) the mission from the launch pad, or directly in orbit.

        `vehicle` is an optional `telemetry.vehicles.VehicleDesign` as a dict; the
        ascent then flies that rocket and the orbital simulator uses that satellite.
        """
        from telemetry.scenarios import get_scenario
        from telemetry.vehicles import VehicleDesign, build_plan

        if scenario is not None:
            get_scenario(scenario)  # validate before tearing anything down (KeyError -> 422)
        design = VehicleDesign(**vehicle) if vehicle else None  # ValidationError -> 422

        await self.stop(publish=False)

        # A restart is a new mission: the previous session's open anomalies,
        # pending approvals and rolling window must not carry over.
        self.pipeline.reset(self.spacecraft_id, reason="mission restarted")

        self._sim = None
        self._launch = None
        self._launch_fault = launch_fault
        self._vehicle = design.model_dump() if design else None
        self._plan = build_plan(design) if design else None
        self._interval = max(0.02, interval)
        self._dt = dt
        self._settle_frames = max(0, settle_frames)
        self._seed = seed
        self._time_scale = max(1.0, launch_time_scale)
        self._queued_scenario = scenario
        self.frames_emitted = 0
        self.commands_applied = []
        self.milestones = []
        self.launch_state = None
        self.error = None
        self.phase = "COUNTDOWN" if include_launch else "DEPLOYMENT"

        self.pipeline.bus.publish(
            "mission_started",
            {
                "spacecraft_id": self.spacecraft_id,
                "include_launch": include_launch,
                "vehicle": self.vehicle_summary(),
            },
        )
        self._task = asyncio.create_task(self._run(include_launch), name="astrix-mission-runner")
        log.info(
            "mission started (launch=%s, interval=%.2fs, dt=%.1fs)", include_launch, self._interval, dt
        )
        return self.status()

    async def stop(self, publish: bool = True) -> dict:
        task = self._task
        self._task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        flew = self.phase not in ("IDLE", "STOPPED", "FAILED")
        if flew:
            self.phase = "STOPPED"
            if publish:
                self.pipeline.bus.publish("mission_stopped", {"spacecraft_id": self.spacecraft_id})

        status = self.status()
        # Every completed mission becomes one training example: the arc, not the
        # frames. Learning must never be able to fail a stop request.
        if flew and self.model_lab is not None:
            try:
                summary = self.model_lab.record_mission({**status, "spacecraft_id": self.spacecraft_id})
                if summary is not None:
                    self.pipeline.bus.publish("mission_summary", summary)
            except Exception:  # noqa: BLE001
                log.exception("mission summary capture failed")
        return status

    # -- fault control ------------------------------------------------------ #

    def inject(self, scenario_key: str) -> dict:
        from telemetry.scenarios import BENIGN_SCENARIOS, get_scenario

        scenario = get_scenario(scenario_key)  # KeyError -> 422
        if not self.running:
            raise MissionError("mission is not running; start it first")
        if not self.in_orbit:
            raise MissionError(
                f"fault injection is available once the satellite is in orbit "
                f"(current phase: {self.phase})"
            )
        assert self._sim is not None
        self._sim.inject(scenario.key)
        log.info("fault injected: %s", scenario.title)
        self.pipeline.bus.publish(
            "fault_injected",
            {
                "scenario": scenario.key,
                "title": scenario.title,
                "subsystem": scenario.subsystem,
                "description": scenario.description,
                "ramp_seconds": scenario.ramp_seconds,
                "expected_signals": list(scenario.expected_signals),
                "is_fault": scenario.key not in BENIGN_SCENARIOS,
                "seq": self._sim.seq,
            },
        )
        return {"injected": scenario.key, "title": scenario.title}

    def clear_fault(self) -> dict:
        if not self.in_orbit:
            raise MissionError("no satellite in orbit to clear a fault on")
        assert self._sim is not None
        had_fault = self._sim.scenario is not None
        self._sim.clear_fault()
        if had_fault:
            self.pipeline.bus.publish("fault_cleared", {"seq": self._sim.seq})
        return {"cleared": had_fault}

    # -- launch ------------------------------------------------------------- #

    async def _fly_launch(self) -> None:
        from telemetry.launch import LaunchProfile, milestone

        profile = LaunchProfile(fault=getattr(self, "_launch_fault", None), plan=self._plan)
        summary = self.vehicle_summary()
        self._launch = profile
        step = LAUNCH_TICK_SECONDS * self._time_scale
        while not profile.finished:
            snapshot = profile.step(step)
            self.phase = snapshot.phase
            state = snapshot.as_dict()
            state["vehicle"] = summary
            self.launch_state = state
            self.pipeline.bus.publish("launch", state)
            for key in snapshot.events:
                record = {**milestone(key, profile.plan), "at_t": round(snapshot.t, 1)}
                self.milestones.append(record)
                self.pipeline.bus.publish("launch_milestone", record)
            await asyncio.sleep(LAUNCH_TICK_SECONDS)

        if profile.aborted:
            self.phase = "ASCENT_ABORT"
            self.pipeline.bus.publish(
                "launch_milestone",
                {
                    "key": "ascent_abort",
                    "t": profile.t,
                    "title": "Ascent Abort",
                    "description": f"Launch vehicle aborted: {profile.fault}",
                    "at_t": round(profile.t, 1),
                },
            )

    # -- orbit -------------------------------------------------------------- #

    async def _acquire_orbit(self) -> None:
        from telemetry.simulator import SpacecraftSimulator

        self._sim = SpacecraftSimulator(
            spacecraft_id=self.spacecraft_id,
            dt=self._dt,
            seed=self._seed,
            satellite=self._vehicle["satellite"] if self._vehicle else None,
        )
        # Settle frames give the rolling window and the smoothing state context,
        # so the first monitored frames look like a healthy mission rather than a
        # cold start.
        for _ in range(self._settle_frames):
            await self._step()
        self.phase = "ORBIT"
        self.pipeline.bus.publish(
            "orbit_acquired",
            {"spacecraft_id": self.spacecraft_id, "seq": self._sim.seq},
        )
        if self._queued_scenario:
            key, self._queued_scenario = self._queued_scenario, None
            self.inject(key)

    async def _step(self) -> TelemetryFrame:
        assert self._sim is not None
        frame = self._sim.step()
        if self.hardware is not None:
            frame = self.hardware.overlay(frame)
        # The pipeline is synchronous and may make a blocking LLM call, so it runs
        # off the event loop.
        await asyncio.to_thread(self.pipeline.ingest, frame)
        self.frames_emitted += 1
        self._apply_commands()
        return frame

    def _apply_commands(self) -> None:
        assert self._sim is not None
        for command in self.pipeline.pop_commands(self.spacecraft_id):
            effect = self._sim.apply_recovery(command["action_id"])
            record = {**command, "effect": effect, "seq": self._sim.seq}
            self.commands_applied.append(record)
            del self.commands_applied[:-50]
            log.info("command applied to spacecraft: %s -> %s", command["action_id"], effect)
            self.pipeline.bus.publish("command_applied", record)
            if self.hardware is not None and self.hardware.connected:
                from ..hardware import action_to_command

                hw_command = action_to_command(command["action_id"])
                if hw_command:
                    self.hardware.queue(hw_command, source="astrix-recovery")

    async def _run(self, include_launch: bool) -> None:
        try:
            if include_launch:
                await self._fly_launch()
                if self.phase == "ASCENT_ABORT":
                    log.warning("launch sequence ended in ascent abort — vehicle recovered")
                    return
            await self._acquire_orbit()
            while True:
                await self._step()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a runner crash must be visible, not silent
            log.exception("mission runner stopped on an unexpected error")
            self.phase = "FAILED"
            self.error = f"{exc.__class__.__name__}: {exc}"
            self.pipeline.bus.publish("runner_error", {"message": f"mission runner stopped: {self.error}"})

    # -- introspection ------------------------------------------------------ #

    def vehicle_summary(self) -> dict:
        from telemetry.launch import DEFAULT_PLAN

        plan = self._plan or DEFAULT_PLAN
        summary = plan.summary()
        if self._vehicle:
            summary["rocket_color"] = self._vehicle["rocket"]["color"]
            summary["satellite_color"] = self._vehicle["satellite"]["color"]
            summary["satellite"] = self._vehicle["satellite"]
        summary["custom"] = self._vehicle is not None
        return summary

    def status(self) -> dict:
        sim = self._sim
        scenario = sim.scenario if sim else None
        return {
            "running": self.running,
            "phase": self.phase,
            "spacecraft_id": self.spacecraft_id,
            "error": self.error,
            "interval_seconds": self._interval,
            "launch_time_scale": self._time_scale,
            "frames_emitted": self.frames_emitted,
            "simulated_seconds": round(sim.t, 1) if sim else 0.0,
            "launch": self.launch_state,
            "vehicle": self.vehicle_summary(),
            "hardware_in_loop": bool(self.hardware and self.hardware.connected and self.hardware.baseline),
            "milestones": self.milestones,
            "queued_scenario": self._queued_scenario,
            "active_scenario": scenario.key if scenario else None,
            "active_scenario_title": scenario.title if scenario else None,
            "fault_onset_seq": (
                sim.seq - int((sim.t - sim.fault_onset_t) / sim.dt) if scenario and sim else None
            ),
            "wheels_disabled": (
                [i + 1 for i, off in enumerate(sim.wheel_disabled) if off] if sim else []
            ),
            "safe_mode": sim.safe_mode if sim else False,
            "power_save": sim.power_save if sim else False,
            "commands_applied": self.commands_applied[-10:],
        }
