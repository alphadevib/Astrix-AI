/*
  ASTRIX HIL — hardware-in-the-loop firmware for Arduino-class boards
  ===================================================================

  Turns a bench prototype into a spacecraft stand-in that ASTRIX can monitor,
  command and fault-inject in real time.

  Boards: Arduino Uno / Nano / Mega (5 V, ATmega), ESP32 and RP2040 also build.
  Transport: USB serial, 115200 baud, newline-delimited.

  OUT (board -> host), one JSON object per line, at RATE Hz (default 5):
    {"dev":"astrix-hil","fw":"1.0.0","seq":12,"ms":2400,"temp_c":24.61,
     "bus_v":5.012,"current_a":0.412,"light":0.713,"vib_g":0.084,
     "gyro":[0.12,-0.03,0.01],"rpm":2950.0,"fault":"NONE","sev":0.00,"mode":"NOMINAL"}
    Acknowledgements: {"ack":"INJECT VIB_SPIKE 0.8","ok":true}  /  {"err":"..."}

  IN (host -> board), one command per line:
    PING | STATUS | CAL | CLEAR | RATE <1-20> | LED <0|1> | MOTOR <0-255>
    INJECT <TEMP_BIAS|SENSOR_STUCK|VIB_SPIKE|GYRO_DRIFT|BROWNOUT|OVERCURRENT|MOTOR_STALL|DROPOUT> <0.0-1.0>
    ACT <ISOLATE_WHEEL|RESTART_WHEEL|WHEEL_SPEED <pct>|SAFE_MODE|NOMINAL_MODE|POWER_SAVE|REDUNDANT_SENSOR|RECAL_GYRO>

  Wiring (every sensor is optional; missing channels report null):
    A0  NTC 10k thermistor + 10k resistor divider (thermistor to GND)
    A1  Bus voltage divider, R1=10k (top) R2=10k (bottom)  -> BUS_DIVIDER 2.0
    A2  ACS712-05B current sensor output (185 mV/A, 2.5 V zero)
    A3  LDR + 10k divider — "solar array" illumination
    A4/A5 (SDA/SCL)  MPU-6050 IMU at 0x68 — vibration and gyro
    D2  Hall/encoder pulse from the wheel motor (1 pulse/rev) — RPM
    D5  PWM to a logic-level MOSFET or motor driver — the "reaction wheel"
    D7  Relay or MOSFET for the payload load — shed in POWER_SAVE / SAFE
    D8  Backup temperature sensor (same divider as A0) on A6 if present
    LED_BUILTIN  status: solid = nominal, blink = fault/safe mode

  Injected faults modify the reported readings exactly as the named failure
  would, ramping over ~20 s, so ASTRIX sees a realistic onset rather than a step.
  Recovery actions change real actuators (motor off, loads shed) and clear the
  fault they address — the loop closes on physical hardware.

  RESULTS ARE FOR PROTOTYPE TESTING. Never connect this firmware to flight
  hardware or to anything whose failure could cause harm.
*/

#include <Wire.h>

// ------------------------------------------------------------------ config
#define FW_VERSION      "1.0.0"
#define DEVICE_ID       "astrix-hil"
#define BAUD            115200

#define PIN_TEMP        A0
#define PIN_BUS         A1
#define PIN_CURRENT     A2
#define PIN_LIGHT       A3
#define PIN_ENCODER     2
#define PIN_MOTOR       5
#define PIN_PAYLOAD     7

#define HAS_TEMP        1
#define HAS_BUS         1
#define HAS_CURRENT     1
#define HAS_LIGHT       1
#define HAS_ENCODER     1

#define ADC_REF_V       5.0
#define ADC_MAX         1023.0
#define BUS_DIVIDER     2.0
#define ACS_MV_PER_A    185.0
#define NTC_BETA        3950.0
#define NTC_R0          10000.0
#define NTC_SERIES_R    10000.0
#define MPU_ADDR        0x68
#define FAULT_RAMP_MS   20000UL
#define VIB_SAMPLES     24

// ------------------------------------------------------------------ state
enum Fault { F_NONE, F_TEMP_BIAS, F_SENSOR_STUCK, F_VIB_SPIKE, F_GYRO_DRIFT, F_BROWNOUT, F_OVERCURRENT, F_MOTOR_STALL, F_DROPOUT };
const char* FAULT_NAMES[] = {"NONE", "TEMP_BIAS", "SENSOR_STUCK", "VIB_SPIKE", "GYRO_DRIFT", "BROWNOUT", "OVERCURRENT", "MOTOR_STALL", "DROPOUT"};
const int FAULT_COUNT = 9;

enum Mode { M_NOMINAL, M_POWER_SAVE, M_SAFE };
const char* MODE_NAMES[] = {"NOMINAL", "POWER_SAVE", "SAFE"};

unsigned long seq = 0;
unsigned long lastReport = 0;
unsigned long reportIntervalMs = 200;   // 5 Hz
Fault fault = F_NONE;
float severity = 0.0;
unsigned long faultStart = 0;
Mode mode = M_NOMINAL;
int motorPwm = 180;
bool ledOverride = false;
bool ledState = true;
bool useBackupTemp = false;
bool imuPresent = false;
float stuckTemp = NAN;
float currentZeroV = 2.5;
float gyroBias[3] = {0, 0, 0};

volatile unsigned long encoderPulses = 0;
unsigned long lastRpmMs = 0;
float rpm = 0.0;

char lineBuf[72];
byte lineLen = 0;

// ------------------------------------------------------------------ helpers
void onEncoder() { encoderPulses++; }

float analogVolts(int pin) {
  long sum = 0;
  for (int i = 0; i < 8; i++) sum += analogRead(pin);
  return (sum / 8.0) * ADC_REF_V / ADC_MAX;
}

float faultRamp() {
  if (fault == F_NONE) return 0.0;
  unsigned long elapsed = millis() - faultStart;
  float p = elapsed >= FAULT_RAMP_MS ? 1.0 : (float)elapsed / FAULT_RAMP_MS;
  return p * severity;
}

void writeMotor(int pwm) {
  motorPwm = constrain(pwm, 0, 255);
  analogWrite(PIN_MOTOR, motorPwm);
}

void applyMode() {
  // Payload load is shed outside nominal operations.
  digitalWrite(PIN_PAYLOAD, mode == M_NOMINAL ? HIGH : LOW);
  if (mode == M_SAFE) writeMotor(min(motorPwm, 90));
}

// ------------------------------------------------------------------ sensors
float readTemperature() {
  // Backup sensor on A6 exists on Nano/Mega; falls back to primary elsewhere.
#if defined(A6)
  int pin = useBackupTemp ? A6 : PIN_TEMP;
#else
  int pin = PIN_TEMP;
#endif
  float v = analogVolts(pin);
  if (v <= 0.01 || v >= ADC_REF_V - 0.01) return NAN;
  float r = NTC_SERIES_R * v / (ADC_REF_V - v);
  float kelvin = 1.0 / (1.0 / 298.15 + log(r / NTC_R0) / NTC_BETA);
  return kelvin - 273.15;
}

bool mpuWrite(byte reg, byte value) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool mpuRead(byte reg, int16_t* out, int count) {
  Wire.beginTransmission(MPU_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  Wire.requestFrom((int)MPU_ADDR, count * 2);
  for (int i = 0; i < count; i++) {
    if (Wire.available() < 2) return false;
    out[i] = (Wire.read() << 8) | Wire.read();
  }
  return true;
}

void initImu() {
  Wire.begin();
  Wire.beginTransmission(MPU_ADDR);
  imuPresent = Wire.endTransmission() == 0 && mpuWrite(0x6B, 0x00);  // wake
  if (imuPresent) {
    mpuWrite(0x1C, 0x10);  // accel ±8 g
    mpuWrite(0x1B, 0x08);  // gyro ±500 dps
  }
}

// RMS of acceleration magnitude about its mean, in g — a vibration proxy.
float readVibration() {
  if (!imuPresent) return NAN;
  float mags[VIB_SAMPLES];
  float mean = 0;
  int16_t a[3];
  for (int i = 0; i < VIB_SAMPLES; i++) {
    if (!mpuRead(0x3B, a, 3)) return NAN;
    float x = a[0] / 4096.0, y = a[1] / 4096.0, z = a[2] / 4096.0;
    mags[i] = sqrt(x * x + y * y + z * z);
    mean += mags[i];
    delay(1);
  }
  mean /= VIB_SAMPLES;
  float acc = 0;
  for (int i = 0; i < VIB_SAMPLES; i++) acc += (mags[i] - mean) * (mags[i] - mean);
  return sqrt(acc / VIB_SAMPLES);
}

bool readGyro(float* out) {
  if (!imuPresent) return false;
  int16_t g[3];
  if (!mpuRead(0x43, g, 3)) return false;
  for (int i = 0; i < 3; i++) out[i] = g[i] / 65.5 - gyroBias[i];
  return true;
}

void calibrate() {
#if HAS_CURRENT
  currentZeroV = analogVolts(PIN_CURRENT);
#endif
  float sum[3] = {0, 0, 0};
  float g[3];
  int n = 0;
  gyroBias[0] = gyroBias[1] = gyroBias[2] = 0;
  for (int i = 0; i < 50 && imuPresent; i++) {
    if (readGyro(g)) { for (int k = 0; k < 3; k++) sum[k] += g[k]; n++; }
    delay(4);
  }
  if (n) for (int k = 0; k < 3; k++) gyroBias[k] = sum[k] / n;
}

// ------------------------------------------------------------------ output
void printFloatOrNull(const char* key, float value, int digits, bool comma = true) {
  Serial.print('"'); Serial.print(key); Serial.print("\":");
  if (isnan(value)) Serial.print("null"); else Serial.print(value, digits);
  if (comma) Serial.print(',');
}

void ack(const char* text, bool ok = true) {
  Serial.print(ok ? "{\"ack\":\"" : "{\"err\":\"");
  Serial.print(text);
  Serial.println(ok ? "\",\"ok\":true}" : "\"}");
}

void report() {
  float ramp = faultRamp();
  seq++;

  float temp = NAN, busV = NAN, current = NAN, light = NAN, vib = NAN;
#if HAS_TEMP
  temp = readTemperature();
#endif
#if HAS_BUS
  busV = analogVolts(PIN_BUS) * BUS_DIVIDER;
#endif
#if HAS_CURRENT
  current = (analogVolts(PIN_CURRENT) - currentZeroV) * 1000.0 / ACS_MV_PER_A;
#endif
#if HAS_LIGHT
  light = analogVolts(PIN_LIGHT) / ADC_REF_V;
#endif
  vib = readVibration();
  float gyro[3];
  bool haveGyro = readGyro(gyro);
  float rpmOut = rpm;

  // ---- on-board fault injection: corrupt readings as the failure would ----
  switch (fault) {
    case F_TEMP_BIAS:   if (!isnan(temp)) temp += 15.0 * ramp; break;
    case F_SENSOR_STUCK:
      if (isnan(stuckTemp)) stuckTemp = temp;
      temp = stuckTemp; break;
    case F_VIB_SPIKE:   if (motorPwm > 0) vib = (isnan(vib) ? 0.1 : vib) + 1.6 * ramp; break;
    case F_GYRO_DRIFT:
      if (!haveGyro) { gyro[0] = gyro[1] = gyro[2] = 0; haveGyro = true; }
      gyro[0] += 4.0 * ramp; gyro[1] -= 3.0 * ramp; gyro[2] += 2.0 * ramp; break;
    case F_BROWNOUT:    if (!isnan(busV)) busV -= 1.2 * ramp; break;
    case F_OVERCURRENT: if (!isnan(current)) current += 0.8 * ramp; break;
    case F_MOTOR_STALL:
      rpmOut *= (1.0 - 0.8 * ramp);
      if (!isnan(current)) current += 0.3 * ramp; break;
    case F_DROPOUT:
      if (random(1000) < (long)(severity * 800)) return;  // packet lost
      break;
    default: break;
  }

  Serial.print("{\"dev\":\"" DEVICE_ID "\",\"fw\":\"" FW_VERSION "\",\"seq\":");
  Serial.print(seq);
  Serial.print(",\"ms\":"); Serial.print(millis()); Serial.print(',');
  printFloatOrNull("temp_c", temp, 2);
  printFloatOrNull("bus_v", busV, 3);
  printFloatOrNull("current_a", current, 3);
  printFloatOrNull("light", isnan(light) ? NAN : constrain(light, 0.0, 1.0), 3);
  printFloatOrNull("vib_g", vib, 3);
  Serial.print("\"gyro\":");
  if (haveGyro) {
    Serial.print('['); Serial.print(gyro[0], 2); Serial.print(','); Serial.print(gyro[1], 2);
    Serial.print(','); Serial.print(gyro[2], 2); Serial.print("],");
  } else {
    Serial.print("null,");
  }
#if HAS_ENCODER
  printFloatOrNull("rpm", rpmOut, 1);
#else
  Serial.print("\"rpm\":null,");
#endif
  Serial.print("\"fault\":\""); Serial.print(FAULT_NAMES[fault]);
  Serial.print("\",\"sev\":"); Serial.print(severity, 2);
  Serial.print(",\"mode\":\""); Serial.print(MODE_NAMES[mode]);
  Serial.println("\"}");
}

// ------------------------------------------------------------------ commands
Fault parseFault(const char* name) {
  for (int i = 1; i < FAULT_COUNT; i++) if (strcmp(name, FAULT_NAMES[i]) == 0) return (Fault)i;
  return F_NONE;
}

void clearFault() {
  fault = F_NONE;
  severity = 0.0;
  stuckTemp = NAN;
}

void handleAction(const char* action, const char* arg) {
  if (strcmp(action, "ISOLATE_WHEEL") == 0) {
    writeMotor(0);
    if (fault == F_VIB_SPIKE || fault == F_MOTOR_STALL) clearFault();
  } else if (strcmp(action, "RESTART_WHEEL") == 0) {
    writeMotor(0); delay(300); writeMotor(180);
  } else if (strcmp(action, "WHEEL_SPEED") == 0 && arg) {
    writeMotor(constrain(atoi(arg), 0, 100) * 255 / 100);
  } else if (strcmp(action, "SAFE_MODE") == 0) {
    mode = M_SAFE; applyMode();
  } else if (strcmp(action, "POWER_SAVE") == 0) {
    mode = M_POWER_SAVE; applyMode();
  } else if (strcmp(action, "NOMINAL_MODE") == 0) {
    mode = M_NOMINAL; applyMode();
  } else if (strcmp(action, "REDUNDANT_SENSOR") == 0) {
    useBackupTemp = true;
    if (fault == F_TEMP_BIAS || fault == F_SENSOR_STUCK) clearFault();
  } else if (strcmp(action, "RECAL_GYRO") == 0) {
    if (fault == F_GYRO_DRIFT) clearFault();
    calibrate();
  } else {
    ack("unknown action", false);
    return;
  }
}

void handleLine(char* line) {
  char original[sizeof(lineBuf)];
  strncpy(original, line, sizeof(original));
  original[sizeof(original) - 1] = 0;
  for (char* p = line; *p; p++) *p = toupper(*p);

  char* cmd = strtok(line, " ");
  char* a1 = strtok(NULL, " ");
  char* a2 = strtok(NULL, " ");
  if (!cmd) return;

  if (strcmp(cmd, "PING") == 0) {
    Serial.println("{\"ack\":\"PING\",\"ok\":true,\"dev\":\"" DEVICE_ID "\",\"fw\":\"" FW_VERSION "\"}");
    return;
  } else if (strcmp(cmd, "STATUS") == 0) {
    Serial.print("{\"ack\":\"STATUS\",\"ok\":true,\"mode\":\""); Serial.print(MODE_NAMES[mode]);
    Serial.print("\",\"fault\":\""); Serial.print(FAULT_NAMES[fault]);
    Serial.print("\",\"motor\":"); Serial.print(motorPwm);
    Serial.print(",\"imu\":"); Serial.print(imuPresent ? "true" : "false");
    Serial.print(",\"rate_ms\":"); Serial.print(reportIntervalMs);
    Serial.println("}");
    return;
  } else if (strcmp(cmd, "CAL") == 0) {
    calibrate();
  } else if (strcmp(cmd, "CLEAR") == 0) {
    clearFault();
  } else if (strcmp(cmd, "RATE") == 0 && a1) {
    int hz = constrain(atoi(a1), 1, 20);
    reportIntervalMs = 1000UL / hz;
  } else if (strcmp(cmd, "LED") == 0 && a1) {
    ledOverride = true; ledState = atoi(a1) != 0;
  } else if (strcmp(cmd, "MOTOR") == 0 && a1) {
    writeMotor(atoi(a1));
  } else if (strcmp(cmd, "INJECT") == 0 && a1 && a2) {
    Fault f = parseFault(a1);
    if (f == F_NONE) { ack("unknown fault", false); return; }
    fault = f;
    severity = constrain(atof(a2), 0.0, 1.0);
    faultStart = millis();
    stuckTemp = NAN;
  } else if (strcmp(cmd, "ACT") == 0 && a1) {
    handleAction(a1, a2);
  } else {
    ack("unknown command", false);
    return;
  }
  ack(original);
}

void pollSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) {
        lineBuf[lineLen] = 0;
        handleLine(lineBuf);
        lineLen = 0;
      }
    } else if (lineLen < sizeof(lineBuf) - 1) {
      lineBuf[lineLen++] = c;
    }
  }
}

// ------------------------------------------------------------------ main
void setup() {
  Serial.begin(BAUD);
  pinMode(LED_BUILTIN, OUTPUT);
  pinMode(PIN_MOTOR, OUTPUT);
  pinMode(PIN_PAYLOAD, OUTPUT);
#if HAS_ENCODER
  pinMode(PIN_ENCODER, INPUT_PULLUP);
  attachInterrupt(digitalPinToInterrupt(PIN_ENCODER), onEncoder, RISING);
#endif
  randomSeed(analogRead(A5));
  initImu();
  calibrate();
  writeMotor(motorPwm);
  applyMode();
  Serial.println("{\"ack\":\"BOOT\",\"ok\":true,\"dev\":\"" DEVICE_ID "\",\"fw\":\"" FW_VERSION "\"}");
}

void loop() {
  pollSerial();
  unsigned long now = millis();

#if HAS_ENCODER
  if (now - lastRpmMs >= 500) {
    noInterrupts();
    unsigned long pulses = encoderPulses;
    encoderPulses = 0;
    interrupts();
    rpm = pulses * 60000.0 / (now - lastRpmMs);
    lastRpmMs = now;
  }
#endif

  // Status LED: solid when nominal, blinking on a fault or outside nominal mode.
  if (ledOverride) {
    digitalWrite(LED_BUILTIN, ledState);
  } else if (fault != F_NONE || mode != M_NOMINAL) {
    digitalWrite(LED_BUILTIN, (now / 250) % 2);
  } else {
    digitalWrite(LED_BUILTIN, HIGH);
  }

  if (now - lastReport >= reportIntervalMs) {
    lastReport = now;
    report();
  }
}
