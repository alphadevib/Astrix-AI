"""Reasoner selection and the Astrix operator assistant.

The assistant is the conversational front door to the console. Operational
intents — launch, stop, inject, clear, approve, design a vehicle, train the
model, switch reasoner — are
recognised deterministically and executed through the same services the
buttons use, so a chat command can never do something a button could not.
Everything else is answered by the active LLM with a live state summary, or by
a deterministic status report when no LLM is available.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..core.disclaimer import FULL as DISCLAIMER
from ..core.disclaimer import SHORT as NOTICE
from .deps import AstrixDep, CurrentUser

router = APIRouter(tags=["assistant"])


class SelectRequest(BaseModel):
    provider: str = Field(min_length=2, max_length=32)
    model: str | None = Field(default=None, max_length=120)


class ChatTurn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: str = Field(min_length=1, max_length=2000)
    # Omitted for a brand-new thread: the server creates one and returns its id.
    conversation_id: str | None = Field(default=None, max_length=36)
    # Client-supplied history is a fallback only. When a conversation id is given,
    # the stored thread is authoritative — a client cannot rewrite what was said.
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    vehicle: dict | None = None  # the design currently open in Vehicle Studio


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=160)


class ConversationRename(BaseModel):
    title: str = Field(min_length=1, max_length=160)


# --------------------------------------------------------------------------- #
# Reasoner
# --------------------------------------------------------------------------- #


@router.get("/reasoner/providers", summary="Every reasoner provider, its free tier and configuration")
async def reasoner_providers(astrix: AstrixDep) -> dict:
    gateway = astrix.llm.gateway
    # Answer from the last Ollama probe; a stale one is refreshed behind this
    # response rather than making the picker wait on a connect timeout.
    gateway.refresh_local_in_background()
    return {"status": astrix.llm.status, "providers": gateway.catalogue()}


@router.post("/reasoner/select", summary="Switch the active reasoner at runtime")
async def reasoner_select(body: SelectRequest, astrix: AstrixDep) -> dict:
    try:
        await asyncio.to_thread(astrix.llm.gateway.select, body.provider, body.model)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc).strip("'")) from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from None
    return {"status": astrix.llm.status, "providers": astrix.llm.gateway.catalogue()}


# --------------------------------------------------------------------------- #
# Assistant
# --------------------------------------------------------------------------- #

HELP = """I can operate the whole console. Try:

- **launch mission** · *start in orbit* · *launch with thrust loss* · **stop mission**
- **inject wheel degradation** · *inject benign thermal transient* · **clear fault**
- **approve** / **reject** the pending recovery
- **explain** the current anomaly · **status**
- **design** a 3-stage rocket for a 400 kg imaging satellite to 700 km
- **train model** · **use groq** / *switch to local qwen3:8b* / *use deterministic*"""


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _best_match(text: str, candidates: dict[str, str]) -> str | None:
    """Candidate key whose key/title shares the most words with the text."""
    words = _words(text)
    best, score = None, 0
    for key, title in candidates.items():
        overlap = len(words & (_words(key.replace("_", " ")) | _words(title)))
        if overlap > score:
            best, score = key, overlap
    return best


def _last_cycle(astrix) -> dict | None:
    for event in reversed(astrix.bus.recent(200)):
        if event["type"] == "cycle" and event["payload"].get("diagnosis"):
            return event["payload"]
    return None


def _state_summary(astrix) -> dict[str, Any]:
    mission = astrix.runner.status() if astrix.runner else {"running": False, "phase": "IDLE"}
    pipeline = astrix.pipeline.status()
    cycle = _last_cycle(astrix)
    return {
        "mission": {
            k: mission.get(k)
            for k in ("running", "phase", "simulated_seconds", "active_scenario_title", "safe_mode", "power_save", "wheels_disabled")
        },
        "vehicle": (mission.get("vehicle") or {}).get("name"),
        "awaiting_approval": pipeline["awaiting_approval"],
        "anomalies_opened": pipeline["anomalies_opened"],
        "suppressed": pipeline["suppressed"],
        "last_diagnosis": (cycle or {}).get("diagnosis"),
        "last_risk": (cycle or {}).get("risk"),
        "selected_action": ((cycle or {}).get("plan") or {}).get("selected_action_id"),
        "reasoner": astrix.llm.status.get("provider"),
    }


def _status_text(state: dict[str, Any]) -> str:
    m = state["mission"]
    lines = []
    if m.get("running"):
        phase = str(m.get("phase", "")).replace("_", " ").lower()
        lines.append(f"Mission is **{phase}** (MET {m.get('simulated_seconds') or 0:.0f} s, vehicle {state['vehicle']}).")
    else:
        lines.append(f"No mission running (last phase: {str(m.get('phase', 'IDLE')).lower()}).")
    if m.get("active_scenario_title"):
        lines.append(f"Active injected fault: **{m['active_scenario_title']}**.")
    if state["last_diagnosis"]:
        d = state["last_diagnosis"]
        lines.append(
            f"Latest diagnosis: **{d.get('subsystem')}** — {d.get('probable_cause')} "
            f"(confidence {float(d.get('confidence') or 0):.0%})."
        )
    if state["awaiting_approval"]:
        a = state["awaiting_approval"][0]
        lines.append(f"Awaiting your approval: `{a['action_id']}` ({a['risk_level']}) — say **approve** or **reject**.")
    lines.append(
        f"Anomalies opened: {state['anomalies_opened']}, suppressed as benign: {state['suppressed']}."
    )
    return "\n\n".join(lines)


async def _handle_intent(astrix, text: str, body: ChatRequest) -> dict[str, Any] | None:
    t = text.lower().strip()
    runner = astrix.runner

    if re.fullmatch(r"(help|\?|what can you do\??|commands)", t):
        return {"reply": HELP}

    # ---- reasoner switching ------------------------------------------------
    if m := re.match(r"^(?:use|switch to|select)\s+(?:the\s+)?([a-z]+)(?:\s+(?:model\s+)?(\S+))?", t):
        provider, model = m.group(1), m.group(2)
        from ..agents.providers import PROVIDER_MAP

        if provider in PROVIDER_MAP or provider in ("deterministic", "ollama"):
            try:
                st = await asyncio.to_thread(astrix.llm.gateway.select, provider, model)
            except (KeyError, ValueError) as exc:
                return {"reply": f"Couldn't switch reasoner: {str(exc).strip(chr(39))}"}
            label = st.get("provider_label")
            model_note = f" · `{st['model']}`" if st.get("model") else ""
            return {"reply": f"Reasoner switched to **{label}**{model_note}.", "cards": [{"kind": "reasoner", "data": st}]}

    # ---- mission control ---------------------------------------------------
    if re.search(r"\b(stop|end|terminate)\b.*\bmission\b|^stop$", t):
        result = await runner.stop()
        return {"reply": "Mission stopped.", "cards": [{"kind": "mission", "data": result}]}

    if re.search(r"\b(launch|start|fly)\b", t) and not re.search(r"\b(design|generate)\b", t):
        include_launch = not re.search(r"in orbit|skip (the )?launch|no launch", t)
        launch_fault = None
        if "premature" in t or "meco" in t:
            launch_fault = "premature_meco"
        elif "thrust" in t:
            launch_fault = "ascent_thrust_loss"
        elif "max-q" in t or "max q" in t or "maxq" in t:
            launch_fault = "max_q_excursion"
        vehicle = body.vehicle if re.search(r"\b(my|custom|studio|designed|this)\b.*\b(vehicle|rocket|design)\b", t) else None
        try:
            result = await runner.start(
                interval=0.35, include_launch=include_launch, launch_fault=launch_fault, vehicle=vehicle
            )
        except (KeyError, ValueError) as exc:
            return {"reply": f"Couldn't start the mission: {exc}"}
        what = "Launch sequence started" if include_launch else "Satellite started directly in orbit"
        extra = f" with the **{launch_fault.replace('_', ' ')}** ascent fault" if launch_fault else ""
        name = (result.get("vehicle") or {}).get("name")
        return {
            "reply": f"{what}{extra} — vehicle **{name}**. Open **Flight Assurance** to watch it fly.",
            "cards": [{"kind": "mission", "data": {k: result.get(k) for k in ("running", "phase", "vehicle")}}],
            "navigate": "assurance",
        }

    if re.search(r"\bclear\b.*\bfault\b", t):
        try:
            result = runner.clear_fault()
        except RuntimeError as exc:
            return {"reply": str(exc)}
        return {"reply": "Fault cleared." if result["cleared"] else "There was no active fault to clear."}

    if re.search(r"\binject\b", t):
        from telemetry.scenarios import SCENARIOS

        key = _best_match(t, {k: s.title for k, s in SCENARIOS.items()})
        if key is None:
            return {"reply": "Which fault? " + ", ".join(f"`{k}`" for k in sorted(SCENARIOS))}
        try:
            result = runner.inject(key)
        except RuntimeError as exc:
            return {"reply": f"Can't inject yet: {exc}."}
        return {"reply": f"Injected **{result['title']}**. I'll detect, diagnose and plan a recovery — say **explain** in a moment.", "navigate": None}

    # ---- approvals ---------------------------------------------------------
    if re.fullmatch(r"(approve|approved|yes,? approve|go ahead|reject|deny|decline)( it| the (recovery|action))?\.?", t):
        pending = astrix.pipeline.pending_approvals()
        if not pending:
            return {"reply": "Nothing is awaiting approval."}
        first = pending[0]
        approved = t.startswith(("approve", "yes", "go"))
        result = await asyncio.to_thread(
            astrix.pipeline.approve, first["anomaly_id"], first["action_id"], approved, "assistant-operator", "via chat"
        )
        return {"reply": result.message or f"{'Approved' if approved else 'Rejected'} `{first['action_id']}`."}

    # ---- vehicle design ----------------------------------------------------
    if re.search(r"\b(design|generate|create|build)\b.*\b(rocket|satellite|launcher|vehicle|cubesat|smallsat)\b", t):
        from .routes_vehicles import GenerateRequest, generate_design

        report = await generate_design(GenerateRequest(prompt=text), astrix)
        a = report["analysis"]
        d = report["design"]
        return {
            "reply": (
                f"Designed **{d['rocket']['name']}** ({len(d['rocket']['stages'])} stages, {a['gross_mass_t']:.1f} t) "
                f"carrying **{d['satellite']['name']}** ({d['satellite']['mass_kg']:.0f} kg) to "
                f"{d['target_altitude_km']:.0f} km. Verdict: **{a['verdict']}**, Δv margin {a['margin_ms']:.0f} m/s."
            ),
            "cards": [{"kind": "design", "data": report}],
        }

    # ---- model -------------------------------------------------------------
    if re.search(r"\btrain\b.*\bmodel\b|\bretrain\b", t) and astrix.model_lab is not None:
        result = await asyncio.to_thread(astrix.model_lab.nano.train)
        if not result.get("trained"):
            return {"reply": f"Not trained: {result.get('reason')}."}
        metrics = result["metrics"]
        acc = metrics["subsystem"].get("accuracy")
        acc_text = f"held-out subsystem accuracy {acc:.0%}" if acc is not None else metrics["subsystem"]["evaluation"]
        return {"reply": f"Trained **{result['version']}** on {result['examples']} examples — {acc_text}.", "cards": [{"kind": "training", "data": result}]}

    # ---- status / explain --------------------------------------------------
    if re.search(r"\b(status|what'?s happening|how is|health|summary|explain|why|diagnos)", t):
        state = _state_summary(astrix)
        reply = _status_text(state)
        if state["last_diagnosis"] and re.search(r"\b(explain|why|diagnos)", t):
            d = state["last_diagnosis"]
            evidence = "\n".join(f"- {e}" for e in (d.get("evidence") or [])[:5])
            reply += f"\n\n**Evidence**\n{evidence}" if evidence else ""
            if state["selected_action"]:
                reply += f"\n\nSelected recovery: `{state['selected_action']}` (verified by the safety engine and digital twin)."
        return {"reply": reply, "cards": [{"kind": "status", "data": state}], "llm_context": state}

    return None


# --------------------------------------------------------------------------- #
# Conversations
# --------------------------------------------------------------------------- #


@router.get("/assistant/conversations", summary="This account's conversation threads")
async def list_conversations(user: CurrentUser, astrix: AstrixDep) -> dict:
    return {"conversations": await asyncio.to_thread(astrix.conversations.list, user.id)}


@router.post("/assistant/conversations", summary="Start a new conversation")
async def create_conversation(body: ConversationCreate, user: CurrentUser, astrix: AstrixDep) -> dict:
    return {"conversation": await asyncio.to_thread(astrix.conversations.create, user.id, body.title)}


@router.get("/assistant/conversations/{conversation_id}", summary="One thread with its turns")
async def get_conversation(conversation_id: str, user: CurrentUser, astrix: AstrixDep) -> dict:
    thread = await asyncio.to_thread(astrix.conversations.get, user.id, conversation_id)
    if thread is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"conversation": thread}


@router.patch("/assistant/conversations/{conversation_id}", summary="Rename a thread")
async def rename_conversation(
    conversation_id: str, body: ConversationRename, user: CurrentUser, astrix: AstrixDep
) -> dict:
    ok = await asyncio.to_thread(astrix.conversations.rename, user.id, conversation_id, body.title)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"renamed": True}


@router.delete("/assistant/conversations/{conversation_id}", summary="Delete a thread")
async def delete_conversation(conversation_id: str, user: CurrentUser, astrix: AstrixDep) -> dict:
    ok = await asyncio.to_thread(astrix.conversations.delete, user.id, conversation_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"deleted": True}


@router.delete("/assistant/conversations", summary="Delete every thread for this account")
async def clear_conversations(user: CurrentUser, astrix: AstrixDep) -> dict:
    return {"deleted": await asyncio.to_thread(astrix.conversations.clear_all, user.id)}


@router.post("/assistant/chat", summary="Operate the console in plain language")
async def assistant_chat(body: ChatRequest, user: CurrentUser, astrix: AstrixDep) -> dict:
    store = astrix.conversations
    # The turn is recorded before it is answered, so a failure mid-answer still
    # leaves the operator's own words in the thread.
    conversation_id = await asyncio.to_thread(
        store.append, user.id, body.conversation_id, "user", body.message
    )
    result = await _handle_intent(astrix, body.message, body)
    reasoner: dict[str, Any] = {"kind": "deterministic"}

    if result is None or "llm_context" in result:
        gateway = astrix.llm.gateway
        state = (result or {}).get("llm_context") or _state_summary(astrix)
        system = (
            "You are Astrix, the operator assistant for a spacecraft anomaly-assurance "
            "console. Be concise and precise; use markdown sparingly. For questions about the current mission, "
            "ground every statement in the state JSON below and say so if it lacks the answer; never invent "
            "telemetry or results. General spacecraft, launch and engineering questions may be answered from "
            "your own knowledge. You cannot execute actions yourself: tell the operator the exact command "
            "(e.g. 'inject wheel degradation', 'approve'). Remind that results are hypothetical when giving "
            f"engineering conclusions.\n\nSTATE:\n{state}"
        )
        # Stored history wins; the client's copy only covers the very first turn
        # of a thread that has not been written yet.
        stored = await asyncio.to_thread(store.history, user.id, conversation_id, 12)
        history = [turn for turn in stored[:-1]] or [t.model_dump() for t in body.history[-8:]]
        answer = None
        if astrix.llm.available:
            answer = await asyncio.to_thread(gateway.chat, system, [*history, {"role": "user", "content": body.message}])
        if answer:
            reply = answer["text"]
            if result is not None:
                result["reply"] = reply
            else:
                result = {"reply": reply}
            reasoner = {"kind": "llm", "provider": answer["provider"], "model": answer["model"]}
        elif result is None:
            result = {
                "reply": (
                    "I didn't recognise that as a console command, and no LLM reasoner is available to answer "
                    "free-form questions.\n\n" + _status_text(state) + "\n\n" + HELP
                )
            }

    result.pop("llm_context", None)
    await asyncio.to_thread(
        store.append,
        user.id,
        conversation_id,
        "assistant",
        result["reply"],
        {
            "cards": result.get("cards") or None,
            "navigate": result.get("navigate"),
            "reasoner": reasoner,
        },
    )
    return {
        "conversation_id": conversation_id,
        "reply": result["reply"],
        "cards": result.get("cards", []),
        "navigate": result.get("navigate"),
        "reasoner": reasoner,
        "notice": NOTICE,
        "disclaimer": DISCLAIMER,
    }
