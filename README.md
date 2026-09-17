# Astrix-AI — Autonomous Spacecraft Intelligence & eXecution

**Detect. Reason. Recover. Learn.**

Astrix-AI flies a simulated Earth-observation satellite from the launch pad to orbit,
watches its telemetry, and when something goes wrong it detects the fault,
diagnoses it, plans a recovery, proves the plan against safety rules and a digital
twin, executes it (or asks a human first), measures what actually happened, and
writes the lesson to mission memory.

Everything runs locally. Multi-model gateway supports Anthropic, Google Gemini (free tier),
Groq (free tier), OpenAI, and local offline models via Ollama. No API key is strictly required:
without one, every agent falls back to its deterministic reasoner seamlessly. The
detection, safety, simulation, ontological knowledge graph and memory layers are identical either way.

---

## Quick start

```bash
python -m venv .venv
.venv/Scripts/activate            # Windows;  source .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

# terminal 1 — backend (run from the repository root)
uvicorn backend.app.main:app --reload --port 8000

# terminal 2 — dashboard
cd frontend && npm install && npm run dev      # http://localhost:5173
```

The first backend start trains the anomaly detector on two simulated orbits of
fault-free telemetry (about a minute) and seeds mission memory. Both are cached.

## Running the demo

1. **Launch.** Press **Launch mission**. The 2D view flies the ascent: countdown,
   liftoff, max-Q, staging, fairing jettison, orbit insertion at 500 km, payload
   separation and array deployment, with the milestone checklist filling in beside
   it. Pick 10×/20×/60× for how fast the ascent replays.
2. **Nominal orbit.** At deployment the view switches to the orbit scene and ASTRIX
   begins monitoring. Let it run: eclipse, imaging passes and ground contacts all
   come and go without an alarm. That silence is a result, not dead air — see
   *Detection quality* below.
3. **Inject a fault.** Choose a scenario and press **Inject** (only enabled in
   orbit — before deployment there is no satellite to inject into). Watch the
   fault timeline record how many spacecraft-seconds ASTRIX took to detect,
   alarm, diagnose, decide and act.
4. **Approve a recovery.** Anything above the autonomy limit waits for a human.
   Approving re-verifies the action against current telemetry before it is sent.
5. **Try the benign case.** `benign_thermal_transient` is *not* a fault. ASTRIX
   should notice the excursion and suppress it. Showing this is what separates a
   detector from an alarm generator.

Prefer to skip the ascent? Untick **fly launch** to start in orbit.

## What is real and what is simulated

| Layer | Status |
|---|---|
| Spacecraft dynamics, faults, launch profile | Simulated (`telemetry/`) — physics-lite and deterministic |
| Anomaly detection, context scoring, diagnosis, planning, safety rules, digital twin, mission memory | Real implementations operating on that telemetry |
| LLM reasoning | Optional. Without credentials every agent uses its deterministic reasoner |

ASTRIX never reaches into the spacecraft: approved actions are queued and the
vehicle collects them, which is why the simulator applies them on its next step.

**The launch is not monitored by the anomaly detector.** The detector is trained on
on-orbit telemetry; ascent is checked against fixed range-safety limits instead.
Monitoring engages once the satellite is deployed and settled.

## Detection quality

`scripts/evaluate_detection.py` is the honest scoreboard: it soaks ASTRIX in
fault-free flight and then measures time-to-detect for every scenario.

```bash
python scripts/evaluate_detection.py --orbits 4
```

It exits non-zero if nominal flight raises any alarm or a real fault is missed.

The detector is a hybrid, because neither half is sufficient alone:

* an **Isolation Forest** catches unusual *combinations* of channels, but saturates
  on single-channel excursions (a CPU pinned at 100% scored ~0.05);
* a **mode-conditioned extreme-value score** catches those excursions, judging each
  channel against what is normal *for the current operating mode* (imaging, eclipse,
  ground contact, slew), with per-channel thresholds calibrated on held-out orbits.

The context scorer then weighs the ML score against the mode envelope, cross-sensor
corroboration, history, persistence and mission impact, and can suppress an
uncorroborated deviation outright.

## Tests

```bash
python -m pytest tests -q                 # launch profile, loop logic, runner and API
python scripts/run_scenario.py --no-llm   # one full loop, headless, end to end
node frontend/scripts/ui_smoke.mjs                 # dashboard in a real browser (needs both servers)
```

## Layout

```
backend/app/
  agents/      diagnostic, risk, recovery, learning, resource + the LLM bridge
  ml/          feature engineering, hybrid detector, context-aware scoring
  safety/      deterministic rule engine and limits.yaml
  simulation/  digital twin used to test plans before execution
  memory/      structured (SQL) and vector mission memory
  services/    the loop (pipeline), mission runner, telemetry buffer, event bus
  api/         REST + WebSocket surface
telemetry/     spacecraft simulator, fault scenarios, launch profile
frontend/      React mission-control dashboard (2D mission view, charts, panels)
scripts/       scenario runner, detection evaluation, local-model test
```

## Configuration

Copy `.env.example` to `.env`. Everything a judge might ask you to change live —
severity thresholds, scoring weights, the autonomy limit, the model id — is in
`backend/app/config.py` or `backend/app/safety/limits.yaml`, never hard-coded in a
decision path. `POST /safety/reload` re-reads the limits without a restart.

API documentation is at http://localhost:8000/docs while the backend is running.
