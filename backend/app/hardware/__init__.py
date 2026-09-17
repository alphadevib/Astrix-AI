"""Hardware-in-the-loop (HIL) support for Arduino-class prototypes.

Physical boards run `hardware/arduino/astrix_hil/astrix_hil.ino`. They stream
newline-delimited JSON readings over USB serial and accept one-line text
commands. Two transports reach the backend with the same protocol:

* the browser (Web Serial API) — works even when the dashboard is served from
  Vercel, because the USB port belongs to the operator's machine, not the server;
* `python -m backend.app.hardware.bridge --port COM3` — a headless serial bridge
  for lab benches without a browser.

Both POST readings to `/hardware/telemetry` and receive queued commands in the
response. The hub blends readings into the simulated spacecraft as deviations
from a calibrated baseline, so real sensors perturb the digital spacecraft and
ASTRIX's detection, diagnosis and recovery loop runs against them.
"""

from .hub import COMMANDS, FAULTS, HardwareHub, HardwareReading, action_to_command

__all__ = ["COMMANDS", "FAULTS", "HardwareHub", "HardwareReading", "action_to_command"]
