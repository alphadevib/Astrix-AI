# Astrix HIL: Arduino prototypes in the loop

`arduino/astrix_hil/astrix_hil.ino` turns a bench prototype into a spacecraft
stand-in that Astrix-AI can monitor, command and fault-inject in real time.

> Prototype results are still hypothetical until they are checked against
> qualified test data. Never connect this firmware to flight hardware or to
> anything whose failure could cause harm.

## Bill of materials

Every sensor is optional. A missing channel reports `null`, and the rest keep working.

| Pin | Part | Stands in for |
|---|---|---|
| A0 | 10k NTC thermistor + 10k resistor (thermistor to GND) | Bus temperature |
| A1 | 10k/10k voltage divider | Battery bus voltage |
| A2 | ACS712-05B current sensor | Load current |
| A3 | LDR + 10k divider | Solar-array illumination |
| SDA/SCL | MPU-6050 IMU at 0x68 | Wheel vibration, gyro rates |
| D2 | Hall sensor or encoder, 1 pulse/rev | Reaction-wheel speed |
| D5 | Logic-level MOSFET + small DC motor (flyback diode!) | Reaction wheel |
| D7 | Relay or MOSFET + load | Payload power line |
| A6 (Nano/Mega) | Second thermistor divider | Redundant temperature sensor |

Disable a channel you have not wired by setting its `HAS_*` define to `0`.

## Flash

1. Arduino IDE → open `arduino/astrix_hil/astrix_hil.ino`.
2. Select the board and port, then upload. No libraries needed beyond `Wire`.
3. Open Serial Monitor at 115200 baud. You should see a `BOOT` ack, then one JSON reading every 200 ms.

## Connect

**From the browser (works with the Vercel-hosted console):** open **Hardware Link**
in Chrome or Edge, click **Connect board** and choose the port. Readings stream
to the backend, which calibrates a baseline from the first 10 nominal samples.

**Headless bench / CI:**

```bash
python -m backend.app.hardware.bridge --port COM3            # or /dev/ttyACM0
python -m backend.app.hardware.bridge --emulate              # no board
ASTRIX_API_TOKEN=... python -m backend.app.hardware.bridge --api https://your-api --port COM3
```

## What happens in the loop

1. Start a Flight Assurance mission and let it reach orbit.
2. Calibrated readings are blended into the simulated spacecraft as deviations
   from baseline. Warming the thermistor with your fingers raises bus temperature,
   and shaking the IMU raises wheel #3 vibration.
3. Inject a fault on the chip (`INJECT VIB_SPIKE 0.8`). Astrix detects it, diagnoses
   it and plans a recovery.
4. When a recovery executes, the matching command is written back to the board
   (`isolate_wheel_3` → `ACT ISOLATE_WHEEL`), so the motor physically stops.

## Protocol

Board → host: one JSON object per line.

```json
{"dev":"astrix-hil","fw":"1.0.0","seq":12,"ms":2400,"temp_c":24.61,"bus_v":5.012,
 "current_a":0.412,"light":0.713,"vib_g":0.084,"gyro":[0.12,-0.03,0.01],
 "rpm":2950.0,"fault":"NONE","sev":0.00,"mode":"NOMINAL"}
```

Host → board: one command per line. The backend validates every command against
an allow-list before sending it.

| Command | Effect |
|---|---|
| `PING`, `STATUS` | Liveness, state report |
| `CAL` | Zero current-sensor and gyro offsets |
| `RATE <1-20>` | Telemetry rate in Hz |
| `INJECT <FAULT> <0-1>` | `TEMP_BIAS`, `SENSOR_STUCK`, `VIB_SPIKE`, `GYRO_DRIFT`, `BROWNOUT`, `OVERCURRENT`, `MOTOR_STALL`, `DROPOUT` |
| `CLEAR` | Clear the injected fault |
| `ACT <ACTION> [arg]` | `ISOLATE_WHEEL`, `RESTART_WHEEL`, `WHEEL_SPEED <pct>`, `SAFE_MODE`, `NOMINAL_MODE`, `POWER_SAVE`, `REDUNDANT_SENSOR`, `RECAL_GYRO` |
| `MOTOR <0-255>`, `LED <0\|1>` | Raw actuator control |
