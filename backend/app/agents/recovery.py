"""Recovery Agent (master context §8.7).

Generates, ranks and explains candidate recovery strategies. Three properties
are enforced on the way out, and they are what make "AI proposes, constraints
verify" more than a slogan:

1. **Closed action space.** Options may only name an `action_id` from
   `ACTION_CATALOG`. An invented action is dropped, because only catalogued
   actions have a risk tier, a modelled twin effect and a safety rule set.
2. **Risk tiers are never taken from the LLM.** They are read from the
   catalogue after the fact. A model that labels `enter_safe_mode` as GREEN
   changes nothing about how it is treated.
3. **Historical success rates are never taken from the LLM.** They are measured
   from recorded outcomes in SQL. The model may *reason* about them; it may not
   author them.

So the LLM's real contribution is ordering and justification — which is where
judgement over ambiguous precedent actually helps.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from ..core.enums import OperatingMode, ReasonerKind, RiskLevel, Subsystem
from ..core.schemas import (
    Diagnosis,
    MemoryRecall,
    RecoveryOption,
    RecoveryPlan,
    ResourceState,
    RiskAssessment,
    TelemetryFrame,
)
from .knowledge import ACTION_CATALOG, FAILURE_MODES, catalogue_for_prompt
from .llm import AgentLLM
from .prompting import (
    ASTRIX_ROLE,
    diagnosis_summary,
    frame_summary,
    recall_summary,
    resource_summary,
    risk_summary,
)

log = logging.getLogger(__name__)

_MODES_BY_KEY = {mode.key: mode for mode in FAILURE_MODES}

# Actions that are always worth considering: they are observability changes, so
# they cost nothing and are never unsafe.
_ALWAYS_CANDIDATES = ("increase_monitoring_rate", "prioritize_telemetry")

_RISK_PREFERENCE = {RiskLevel.GREEN: 1.0, RiskLevel.YELLOW: 0.55, RiskLevel.RED: 0.15}

SYSTEM = f"""{ASTRIX_ROLE}

You are the RECOVERY AGENT. Propose and rank recovery options for the diagnosed fault.

Method:
1. Choose only from the candidate action ids given to you. Do not invent action ids.
2. Rank by expected effectiveness against *this* fault, weighted by what recorded
   outcomes show and by what the spacecraft can currently afford. Prefer the least
   disruptive action that actually addresses the cause — an action that merely
   improves observability is not a recovery.
3. Where mission history shows an action failed against this failure mode, do not
   rank it highly just because it is low risk.
4. Give each option a one-sentence rationale that names the evidence or the precedent
   it rests on. State plainly if an option is a stopgap rather than a fix.

{catalogue_for_prompt()}"""


class _RankedAction(BaseModel):
    action_id: str = Field(description="An action id from the candidate list")
    rationale: str = Field(description="One sentence: why this option, and what it rests on")


class _Draft(BaseModel):
    ranked_actions: list[_RankedAction] = Field(
        description="Candidate actions, most recommended first"
    )
    selected_action_id: str = Field(description="The single action you recommend executing")


class RecoveryAgent:
    name = "recovery"

    def __init__(self, llm: AgentLLM) -> None:
        self.llm = llm

    # ------------------------------------------------------------- candidates

    def _candidates(self, diagnosis: Diagnosis) -> list[str]:
        candidates: list[str] = []
        mode = _MODES_BY_KEY.get(diagnosis.failure_mode or "")
        if mode is not None:
            candidates.extend(mode.candidate_actions)
        else:
            # No catalogued failure mode matched, so the *cause* is unknown. Offering
            # every action for the implicated subsystem invites a confident-looking
            # but unjustified reconfiguration — an unclassified ADCS deviation would
            # be answered by isolating a reaction wheel that may be perfectly
            # healthy. Until the signature is recognised, only GREEN-tier actions
            # (reversible, no hardware reconfiguration) are on the table; safe mode
            # is appended below and remains available for a genuine emergency.
            candidates.extend(
                spec.action_id
                for spec in ACTION_CATALOG.values()
                if spec.subsystem is diagnosis.subsystem and spec.risk_level is RiskLevel.GREEN
            )
        candidates.extend(_ALWAYS_CANDIDATES)
        if diagnosis.subsystem is not Subsystem.UNKNOWN:
            candidates.append("enter_safe_mode")
        return list(dict.fromkeys(candidates))  # dedupe, preserve order

    def _feasibility(
        self, action_id: str, resources: ResourceState, frame: TelemetryFrame
    ) -> tuple[float, str | None]:
        """Returns (feasibility 0..1, blocking concern or None).

        Infeasible options are kept but ranked last rather than hidden — the
        safety engine is the authority on rejection, and showing its rejection is
        more informative to an operator than a silently shortened list.
        """
        spec = ACTION_CATALOG[action_id]

        if action_id in ("isolate_wheel_3", "switch_redundant_wheel_config"):
            remaining = len(resources.operational_wheels) - 1
            if remaining < 3:
                return 0.0, (
                    f"would leave {remaining} operational wheels; 3-axis control requires 3"
                )
            if resources.state_of_charge < 30.0:
                return 0.2, "state of charge below the 30% floor for a reconfiguration transient"
        if action_id == "recalibrate_gyro_bias":
            if frame.mode is OperatingMode.MANEUVER:
                return 0.1, "requires a quiet attitude arc; a maneuver is in progress"
            if len(resources.operational_wheels) < 3:
                return 0.3, "attitude must be controllable to hold a calibration arc"
        if action_id == "propulsion_maneuver" and resources.fuel_level < 5.0:
            return 0.0, f"fuel level {resources.fuel_level:.1f}% is below the reserve"
        if action_id in ("enter_power_save", "reduce_payload_duty_cycle") and not frame.payload_active:
            return 0.5, "payload already idle; limited additional power saving available"
        if spec.risk_level is RiskLevel.RED and resources.link_available:
            # Not infeasible, just rarely the right first move while we can still talk.
            return 0.7, None
        return 1.0, None

    # -------------------------------------------------------------------- run

    def run(
        self,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
        risk: RiskAssessment,
        resources: ResourceState,
        recall: MemoryRecall | None = None,
    ) -> RecoveryPlan:
        candidates = self._candidates(diagnosis)
        success_rates = (recall.action_success_rates if recall else {}) or {}

        draft = self.llm.structured(
            system=SYSTEM,
            user="\n\n".join(
                [
                    frame_summary(frame),
                    diagnosis_summary(diagnosis),
                    risk_summary(risk),
                    resource_summary(resources),
                    recall_summary(recall),
                    "CANDIDATE ACTIONS (choose and rank only from these):\n"
                    + "\n".join(
                        f"  - {action_id}: {ACTION_CATALOG[action_id].description} "
                        f"[{ACTION_CATALOG[action_id].risk_level.value}]"
                        for action_id in candidates
                    ),
                    "Rank the recovery options and recommend one.",
                ]
            ),
            output_model=_Draft,
            label=self.name,
        )

        if draft is not None:
            options = self._build_from_draft(draft, candidates, success_rates, resources, frame)
            if options:
                selected = self._select(draft.selected_action_id, options)
                return RecoveryPlan(
                    options=options,
                    selected_action_id=selected,
                    reasoner=ReasonerKind.LLM,
                )
            log.info("recovery agent returned no usable action ids; using the deterministic ranker")

        return self._deterministic(candidates, success_rates, resources, frame, diagnosis)

    # ------------------------------------------------------------------ build

    def _build_from_draft(
        self,
        draft: _Draft,
        candidates: list[str],
        success_rates: dict[str, float],
        resources: ResourceState,
        frame: TelemetryFrame,
    ) -> list[RecoveryOption]:
        allowed = set(candidates)
        options: list[RecoveryOption] = []
        seen: set[str] = set()
        total = max(len(draft.ranked_actions), 1)

        for position, ranked in enumerate(draft.ranked_actions):
            action_id = ranked.action_id.strip()
            if action_id not in allowed:
                log.info("dropping non-catalogue action '%s' from recovery plan", action_id)
                continue
            if action_id in seen:
                continue
            seen.add(action_id)
            options.append(
                self._option(
                    action_id=action_id,
                    rationale=ranked.rationale,
                    # Rank order carries the LLM's judgement; the numeric score
                    # just makes that order sortable and displayable.
                    score=round(1.0 - position / total, 3),
                    success_rates=success_rates,
                    resources=resources,
                    frame=frame,
                )
            )
        return options

    def _option(
        self,
        action_id: str,
        rationale: str,
        score: float,
        success_rates: dict[str, float],
        resources: ResourceState,
        frame: TelemetryFrame,
    ) -> RecoveryOption:
        spec = ACTION_CATALOG[action_id]
        feasibility, concern = self._feasibility(action_id, resources, frame)
        if concern:
            rationale = f"{rationale} Feasibility concern: {concern}."
        return RecoveryOption(
            action_id=action_id,
            description=spec.description,
            risk_level=spec.risk_level,  # authoritative: from the catalogue
            rationale=rationale,
            expected_effect=spec.expected_effect,
            historical_success_rate=success_rates.get(action_id),  # measured, not generated
            score=score,
            parameters={
                "feasibility": feasibility,
                "preconditions": list(spec.preconditions),
                "blocking_concern": concern,
            },
        )

    def _select(self, preferred: str, options: list[RecoveryOption]) -> str:
        """Prefer the LLM's pick, but never select an infeasible option."""
        by_id = {o.action_id: o for o in options}
        candidate = by_id.get(preferred.strip())
        if candidate and candidate.parameters.get("feasibility", 1.0) > 0.0:
            return candidate.action_id
        for option in options:
            if option.parameters.get("feasibility", 1.0) > 0.0:
                return option.action_id
        return options[0].action_id

    # ---------------------------------------------------------- deterministic

    def _deterministic(
        self,
        candidates: list[str],
        success_rates: dict[str, float],
        resources: ResourceState,
        frame: TelemetryFrame,
        diagnosis: Diagnosis,
    ) -> RecoveryPlan:
        options: list[RecoveryOption] = []
        for position, action_id in enumerate(candidates):
            spec = ACTION_CATALOG[action_id]
            feasibility, concern = self._feasibility(action_id, resources, frame)
            historical = success_rates.get(action_id)
            # 0.5 for an untried action: neither credited nor penalised for having
            # no track record.
            historical_term = historical if historical is not None else 0.5
            relevance = 1.0 - position / max(len(candidates), 1)

            score = (
                0.40 * historical_term
                + 0.25 * _RISK_PREFERENCE[spec.risk_level]
                + 0.20 * relevance
                + 0.15 * feasibility
            )

            reasons = []
            if historical is not None:
                reasons.append(
                    f"recorded effectiveness {historical:.2f} against this class of fault"
                )
            else:
                reasons.append("no recorded outcome for this action yet")
            reasons.append(f"{spec.risk_level.value}-tier action")
            if concern:
                reasons.append(f"feasibility concern: {concern}")

            options.append(
                RecoveryOption(
                    action_id=action_id,
                    description=spec.description,
                    risk_level=spec.risk_level,
                    rationale="; ".join(reasons).capitalize() + ".",
                    expected_effect=spec.expected_effect,
                    historical_success_rate=historical,
                    score=round(score, 3),
                    parameters={
                        "feasibility": feasibility,
                        "preconditions": list(spec.preconditions),
                        "blocking_concern": concern,
                    },
                )
            )

        options.sort(key=lambda o: o.score, reverse=True)
        selected = next(
            (o.action_id for o in options if o.parameters.get("feasibility", 1.0) > 0.0),
            options[0].action_id if options else None,
        )
        return RecoveryPlan(
            options=options,
            selected_action_id=selected,
            reasoner=ReasonerKind.DETERMINISTIC,
        )
