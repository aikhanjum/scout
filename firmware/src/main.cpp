// Scout ESP32 bridge. Speaks docs/PROTOCOL.md section 9 over USB serial at 115200.
// In:  D v w | S | B [p] | L r g | T           Out: JSON lines, echo at 10 Hz.
// Owns the motors and the 500 ms watchdog, and nothing else. No wifi, no IMU: Scout senses with
// the lidar on the Pi. The four motors are ganged as two sides.
#include <Arduino.h>
#include "config.h"

// ---------------------------------------------------------------- motors
static const int CH_A = 0, CH_B = 1, CH_BUZZ = 2;
static float cmdV = 0, cmdW = 0;          // last command, for telemetry
static float targetL = 0, targetR = 0;    // wheel targets after mixing, -1..1
static float curL = 0, curR = 0;          // after slew
static bool driving = false;
static uint32_t lastDriveCmd = 0;

static void motorWrite(int ch, int in1, int in2, float x) {
  if (fabsf(x) < 0.05f) { digitalWrite(in1, LOW); digitalWrite(in2, LOW); ledcWrite(ch, 0); return; }
  digitalWrite(in1, x > 0 ? HIGH : LOW);
  digitalWrite(in2, x > 0 ? LOW : HIGH);
  ledcWrite(ch, MIN_DUTY + (int)(fabsf(x) * (255 - MIN_DUTY)));
}

static void driveSet(float v, float w) {  // w positive = left: left wheel slower, right faster
  cmdV = constrain(v, -1.0f, 1.0f);
  cmdW = constrain(w, -1.0f, 1.0f);
  targetL = constrain(cmdV - cmdW, -1.0f, 1.0f) * LEFT_SIGN;
  targetR = constrain(cmdV + cmdW, -1.0f, 1.0f) * RIGHT_SIGN;
  driving = true;
  lastDriveCmd = millis();
}

static void driveStop() {  // immediate, no slew: E-STOP and watchdog
  cmdV = cmdW = targetL = targetR = curL = curR = 0;
  driving = false;
  motorWrite(CH_A, PIN_AIN1, PIN_AIN2, 0);
  motorWrite(CH_B, PIN_BIN1, PIN_BIN2, 0);
}

static void driveTick() {  // every 20 ms: slew toward the targets
  curL += constrain(targetL - curL, -SLEW_PER_TICK, SLEW_PER_TICK);
  curR += constrain(targetR - curR, -SLEW_PER_TICK, SLEW_PER_TICK);
  motorWrite(CH_A, PIN_AIN1, PIN_AIN2, curL);
  motorWrite(CH_B, PIN_BIN1, PIN_BIN2, curR);
}

// ---------------------------------------------------------------- buzzer and LEDs (non-blocking patterns)
// pattern 0: one high chirp (pass). pattern 1: two low beeps (fail).
static uint32_t beepUntil = 0, beepGapUntil = 0;
static int beepLeft = 0, beepFreq = 0;

static void beep(int pattern) {
  if (pattern == 1) { beepFreq = 400; beepLeft = 2; } else { beepFreq = 2000; beepLeft = 1; }
  beepGapUntil = millis();  // start now
}

static void beepTick(uint32_t now) {
  if (beepUntil && now >= beepUntil) { ledcWriteTone(CH_BUZZ, 0); beepUntil = 0; beepGapUntil = now + 100; }
  if (beepLeft && !beepUntil && now >= beepGapUntil) {
    ledcWriteTone(CH_BUZZ, beepFreq);
    beepUntil = now + (beepFreq < 1000 ? 150 : 120);
    beepLeft--;
  }
}

// ---------------------------------------------------------------- serial commands
static void selfTest() {  // T: blocks about 3 s, for the hardware bring-up
  Serial.println("{\"test\":\"start\"}");
  digitalWrite(PIN_LED_RED, HIGH); digitalWrite(PIN_LED_GREEN, HIGH);
  motorWrite(CH_A, PIN_AIN1, PIN_AIN2, 0.5f);  delay(400); motorWrite(CH_A, PIN_AIN1, PIN_AIN2, -0.5f); delay(400); motorWrite(CH_A, PIN_AIN1, PIN_AIN2, 0);
  delay(200);
  motorWrite(CH_B, PIN_BIN1, PIN_BIN2, 0.5f);  delay(400); motorWrite(CH_B, PIN_BIN1, PIN_BIN2, -0.5f); delay(400); motorWrite(CH_B, PIN_BIN1, PIN_BIN2, 0);
  ledcWriteTone(CH_BUZZ, 2000); delay(150); ledcWriteTone(CH_BUZZ, 0);
  digitalWrite(PIN_LED_RED, LOW); digitalWrite(PIN_LED_GREEN, LOW);
  Serial.println("{\"test\":\"done\"}");
}

static void handleLine(char* line) {
  float v, w; int a, b;
  switch (line[0]) {
    case 'D': if (sscanf(line + 1, "%f %f", &v, &w) == 2) driveSet(v, w); else Serial.println("{\"err\":\"D needs v w\"}"); break;
    case 'S': driveStop(); break;
    case 'B': beep(sscanf(line + 1, "%d", &a) == 1 ? a : 0); break;
    case 'L': if (sscanf(line + 1, "%d %d", &a, &b) == 2) { digitalWrite(PIN_LED_RED, a ? HIGH : LOW); digitalWrite(PIN_LED_GREEN, b ? HIGH : LOW); } break;
    case 'T': selfTest(); break;
    case '\0': break;
    default: Serial.printf("{\"err\":\"unknown cmd %c\"}\n", line[0]);
  }
}

static void readSerial() {
  static char buf[64];
  static size_t n = 0;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') { buf[n] = 0; if (n) handleLine(buf); n = 0; }
    else if (n < sizeof(buf) - 1) buf[n++] = c;
  }
}

// ---------------------------------------------------------------- main
void setup() {
  Serial.begin(115200);
  pinMode(PIN_AIN1, OUTPUT); pinMode(PIN_AIN2, OUTPUT); pinMode(PIN_BIN1, OUTPUT); pinMode(PIN_BIN2, OUTPUT);
  pinMode(PIN_LED_RED, OUTPUT); pinMode(PIN_LED_GREEN, OUTPUT);
  ledcSetup(CH_A, 20000, 8); ledcAttachPin(PIN_PWMA, CH_A);   // 20 kHz: inaudible, fine for the TB6612
  ledcSetup(CH_B, 20000, 8); ledcAttachPin(PIN_PWMB, CH_B);
  ledcSetup(CH_BUZZ, 2000, 8); ledcAttachPin(PIN_BUZZER, CH_BUZZ);
  driveStop();
  Serial.printf("{\"hello\":\"scout-esp32\",\"fw\":\"%s\"}\n", FW_VERSION);
}

void loop() {
  static uint32_t lastDrive = 0, lastTelem = 0;
  uint32_t now = millis();
  readSerial();
  if (now - lastDrive >= 20) { driveTick(); lastDrive = now; }
  if (driving && now - lastDriveCmd > WATCHDOG_MS) driveStop();
  beepTick(now);
  if (now - lastTelem >= TELEM_MS) {
    lastTelem = now;
    Serial.printf("{\"t\":%lu,\"v\":%.2f,\"w\":%.2f}\n", (unsigned long)now, cmdV, cmdW);
  }
}
