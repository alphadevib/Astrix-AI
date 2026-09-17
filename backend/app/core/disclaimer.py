"""The standing notice attached to every ASTRIX result.

ASTRIX runs on simulated spacecraft, first-order launch and intercept models,
and advisory AI reasoning. Nothing it produces is flight-qualified. The API
repeats this in an `X-Astrix-Notice` response header and in the payloads that
present results, and the dashboard shows it on every page.
"""

SHORT = "Hypothetical results: verify with real-time prototypes and uploaded data."

FULL = (
    "Results produced by Astrix-AI are hypothetical. Telemetry, launch, intercept and vehicle "
    "models are simulations and first-order estimates, and AI reasoning is advisory. Every "
    "result must be verified against real-time prototypes, hardware-in-the-loop tests and "
    "uploaded flight or test data before it informs any engineering or operational decision."
)
