"""Verification Agent / Safety Constraint Engine (master context §6, §9, §31).

The one component in ASTRIX with absolute authority. Everything upstream is
advisory: the detector scores, the agents reason, the twin predicts. This engine
decides.

Design commitments:

* **Deterministic.** No model, no scoring, no probability. Given the same state
  and the same action, it returns the same verdict, and you can read the rule
  that produced it.
* **Not overridable.** A `FAIL` cannot be approved by an operator or argued away
  by an agent. The only path forward is a different action.
* **Thresholds are configuration**, loaded from `limits.yaml`, so a flight-rule
  change is a reviewable diff rather than a code change.
* **Rules can escalate risk but never de-escalate it.** `effective_risk_level`
  is the maximum of the catalogue tier and any escalation a rule applies, so no
  combination of conditions can make an action look safer than its tier.

`check()` runs twice per cycle: once before simulation (constraints on state) and
once after (including the twin verdict), matching the §6 flow
Recovery Plan → Safety Engine → Digital Twin → Verification.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from ..core.enums import ApprovalStatus, RiskLevel, VerificationStatus
from ..core.schemas import (
    Diagnosis,
    RecoveryOption,
    ResourceState,
    RuleResult,
    SafetyVerdict,
    SimulationResult,
    TelemetryFrame,
)
from ..agents.knowledge import ACTION_CATALOG

log = logging.getLogger(__name__)

LIMITS_PATH = Path(__file__).with_name("limits.yaml")


@dataclass
class RuleContext:
    frame: TelemetryFrame
    resources: ResourceState
    option: RecoveryOption
    diagnosis: Diagnosis
    simulation: SimulationResult | None
    limits: dict[str, Any]
    config: dict[str, Any]


@dataclass
class RuleOutcome:
    violated: bool
    detail: str = ""
    escalate_to: RiskLevel | None = None
    requires_approval: bool = False


@dataclass(frozen=True)
class SafetyRule:
    rule_id: str
    description: str
    evaluate: Callable[[RuleContext], RuleOutcome]


# --------------------------------------------------------------------------- #
# Rules
# --------------------------------------------------------------------------- #


def _sr001_low_soc_high_power(ctx: RuleContext) -> RuleOutcome:
    threshold = ctx.limits["battery_soc_critical"]
    if ctx.option.action_id not in ctx.config["high_power_actions"]:
        return RuleOutcome(False)
    if ctx.resources.state_of_charge < threshold:
        return RuleOutcome(
            True,
            f"state of charge {ctx.resources.state_of_charge:.1f}% is below the "
            f"{threshold:.0f}% critical floor; non-essential high-power actions are prohibited",
        )
    return RuleOutcome(False)


def _sr002_thermal_load(ctx: RuleContext) -> RuleOutcome:
    limit = ctx.limits["temperature_critical"]
    if ctx.option.action_id not in ctx.config["thermal_load_actions"]:
        return RuleOutcome(False)
    if ctx.frame.temperature > limit:
        return RuleOutcome(
            True,
            f"temperature {ctx.frame.temperature:.1f} C exceeds the {limit:.0f} C critical limit; "
            f"actions that increase thermal load are prohibited",
        )
    return RuleOutcome(False)


def _sr003_wheel_isolation_authority(ctx: RuleContext) -> RuleOutcome:
    """The rule that makes wheel isolation decidable rather than a judgement call."""
    minimum = int(ctx.limits["min_operational_wheels"])
    isolating = ctx.option.action_id in (
        "isolate_wheel_3",
        "switch_redundant_wheel_config",
        "permanent_subsystem_shutdown",
    )
    if not isolating:
        return RuleOutcome(False)
    remaining = len(ctx.resources.operational_wheels) - 1
    if remaining < minimum:
        return RuleOutcome(
            True,
            f"isolation would leave {remaining} operational reaction wheel(s); "
            f"{minimum} are required for 3-axis attitude control",
        )
    soc_min = ctx.limits["battery_soc_reconfig_min"]
    if ctx.resources.state_of_charge < soc_min:
        return RuleOutcome(
            True,
            f"state of charge {ctx.resources.state_of_charge:.1f}% is below the {soc_min:.0f}% "
            f"minimum for a reconfiguration transient",
        )
    return RuleOutcome(False)


def _sr004_link_loss(ctx: RuleContext) -> RuleOutcome:
    """No link means no human in the loop, so only pre-approved responses run."""
    if ctx.resources.link_available:
        return RuleOutcome(False)
    if ctx.option.action_id in ctx.config["link_loss_permitted_actions"]:
        return RuleOutcome(False)
    return RuleOutcome(
        True,
        "ground link unavailable; only pre-approved onboard safe responses are permitted "
        f"({', '.join(ctx.config['link_loss_permitted_actions'])})",
    )


def _sr005_simulation_failed(ctx: RuleContext) -> RuleOutcome:
    if ctx.simulation is None:
        return RuleOutcome(False)  # not yet run; the post-simulation pass re-checks
    if ctx.simulation.status is VerificationStatus.FAIL:
        return RuleOutcome(
            True,
            "digital twin simulation failed: "
            + (", ".join(ctx.simulation.failed_checks) or "unspecified check"),
        )
    return RuleOutcome(False)


def _sr006_red_requires_approval(ctx: RuleContext) -> RuleOutcome:
    if ctx.option.risk_level is RiskLevel.RED:
        return RuleOutcome(
            False,
            "RED-tier action: human approval required",
            requires_approval=True,
        )
    return RuleOutcome(False)


def _sr007_action_in_catalogue(ctx: RuleContext) -> RuleOutcome:
    """Defence in depth: the Recovery Agent already filters, this guarantees it."""
    if ctx.option.action_id not in ACTION_CATALOG:
        return RuleOutcome(
            True,
            f"action '{ctx.option.action_id}' is not in the verified action catalogue",
        )
    return RuleOutcome(False)


def _sr008_propellant_reserve(ctx: RuleContext) -> RuleOutcome:
    reserve = ctx.limits["fuel_reserve_percent"]
    if ctx.option.action_id != "propulsion_maneuver":
        return RuleOutcome(False)
    if ctx.resources.fuel_level < reserve:
        return RuleOutcome(
            True,
            f"fuel level {ctx.resources.fuel_level:.1f}% is below the {reserve:.0f}% reserve",
        )
    return RuleOutcome(False)


def _sr009_irreversible(ctx: RuleContext) -> RuleOutcome:
    if ctx.option.action_id in ctx.config["irreversible_actions"]:
        return RuleOutcome(
            False,
            "irreversible action: escalated to RED and human approval required",
            escalate_to=RiskLevel.RED,
            requires_approval=True,
        )
    return RuleOutcome(False)


def _sr010_pointing_budget(ctx: RuleContext) -> RuleOutcome:
    """Already outside the vehicle-safety pointing limit: only safing or recovery."""
    limit = ctx.limits["pointing_error_safe_deg"]
    if ctx.frame.attitude_error_deg <= limit:
        return RuleOutcome(False)
    permitted = {"enter_safe_mode", "isolate_wheel_3", "switch_redundant_wheel_config", "recalibrate_gyro_bias"}
    if ctx.option.action_id in permitted:
        return RuleOutcome(False)
    return RuleOutcome(
        True,
        f"pointing error {ctx.frame.attitude_error_deg:.2f} deg exceeds the {limit:.2f} deg "
        f"vehicle-safety limit; only attitude recovery or safing is permitted",
    )


def _sr011_feasibility(ctx: RuleContext) -> RuleOutcome:
    """Honour the Resource Agent's hard blocks."""
    if ctx.option.parameters.get("feasibility", 1.0) > 0.0:
        return RuleOutcome(False)
    concern = ctx.option.parameters.get("blocking_concern") or "action is not feasible"
    return RuleOutcome(True, f"resource check: {concern}")


RULES: tuple[SafetyRule, ...] = (
    SafetyRule("SR-001", "No non-essential high-power action below the critical SOC floor", _sr001_low_soc_high_power),
    SafetyRule("SR-002", "No action that increases thermal load above the critical temperature", _sr002_thermal_load),
    SafetyRule("SR-003", "Wheel isolation must preserve minimum attitude-control authority", _sr003_wheel_isolation_authority),
    SafetyRule("SR-004", "Without a ground link, only pre-approved onboard responses are permitted", _sr004_link_loss),
    SafetyRule("SR-005", "An action whose simulation fails must not be executed", _sr005_simulation_failed),
    SafetyRule("SR-006", "RED-tier actions require human approval", _sr006_red_requires_approval),
    SafetyRule("SR-007", "Only actions in the verified catalogue may be executed", _sr007_action_in_catalogue),
    SafetyRule("SR-008", "Propulsion maneuvers must respect the propellant reserve", _sr008_propellant_reserve),
    SafetyRule("SR-009", "Irreversible actions require human approval", _sr009_irreversible),
    SafetyRule("SR-010", "Outside the vehicle pointing limit, only attitude recovery or safing", _sr010_pointing_budget),
    SafetyRule("SR-011", "Actions the resource check marks infeasible must not be executed", _sr011_feasibility),
)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


class SafetyEngine:
    def __init__(self, limits_path: Path | str = LIMITS_PATH, auto_execute_max_risk: RiskLevel = RiskLevel.GREEN) -> None:
        self.limits_path = Path(limits_path)
        self.auto_execute_max_risk = auto_execute_max_risk
        self.config = self._load()

    def _load(self) -> dict[str, Any]:
        with self.limits_path.open(encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
        for key in ("limits", "high_power_actions", "thermal_load_actions", "irreversible_actions", "link_loss_permitted_actions", "simulation"):
            if key not in config:
                raise ValueError(f"{self.limits_path.name} is missing required section '{key}'")
        return config

    def reload(self) -> None:
        """Re-read limits without restarting — useful when a jury asks 'what if'."""
        self.config = self._load()

    @property
    def limits(self) -> dict[str, Any]:
        return self.config["limits"]

    @property
    def simulation_config(self) -> dict[str, Any]:
        return self.config["simulation"]

    def check(
        self,
        frame: TelemetryFrame,
        resources: ResourceState,
        option: RecoveryOption,
        diagnosis: Diagnosis,
        simulation: SimulationResult | None = None,
        audit_id: str | None = None,
    ) -> SafetyVerdict:
        ctx = RuleContext(
            frame=frame,
            resources=resources,
            option=option,
            diagnosis=diagnosis,
            simulation=simulation,
            limits=self.limits,
            config=self.config,
        )

        results: list[RuleResult] = []
        effective_risk = option.risk_level
        requires_approval = False

        for rule in RULES:
            outcome = rule.evaluate(ctx)
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    description=rule.description,
                    violated=outcome.violated,
                    detail=outcome.detail,
                )
            )
            if outcome.escalate_to and outcome.escalate_to.rank > effective_risk.rank:
                effective_risk = outcome.escalate_to
            if outcome.requires_approval:
                requires_approval = True

        violated = any(r.violated for r in results)
        status = VerificationStatus.FAIL if violated else VerificationStatus.PASS

        if status is VerificationStatus.FAIL:
            approval = ApprovalStatus.BLOCKED
        elif requires_approval or effective_risk.rank > self.auto_execute_max_risk.rank:
            approval = ApprovalStatus.PENDING_APPROVAL
        else:
            approval = ApprovalStatus.AUTO_APPROVED

        verdict = SafetyVerdict(
            action_id=option.action_id,
            status=status,
            effective_risk_level=effective_risk,
            approval=approval,
            rules=results,
            audit_id=audit_id or uuid.uuid4().hex[:12],
        )
        if violated:
            log.info(
                "safety engine BLOCKED '%s': %s", option.action_id, "; ".join(verdict.violations)
            )
        return verdict
