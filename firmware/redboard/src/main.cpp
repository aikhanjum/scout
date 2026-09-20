/* Scout's motor board: SparkFun RedBoard (ATmega328P) + DK Electronics motor shield.
 * Speaks docs/PROTOCOL.md section 9, the board half.
 *
 *   "<L> <R>\n"  drive. Each -255..255, positive forward. The left pair and the right pair are
 *                ganged; there is no per-wheel control and none is needed.
 *   "s\n"        stop now.
 *   "?\n"        status.
 *   "t\n"        bring-up self-test: each motor in turn, so you can find out which is which.
 *
 * Replies, one line each: "scoutable-motor-v1 ready" once at boot, "ok <L> <R>" on every accepted
 * command, "err" on anything unparseable, "watchdog stop" when it stops itself.
 *
 * THE WATCHDOG IS THE SAFETY FEATURE. If nothing arrives for WATCHDOG_MS the motors stop on their
 * own, so a crashed Pi, a killed process or a yanked USB cable halts the robot instead of letting
 * it drive into a wall. Nothing here may pet it except a real command from the host.
 *
 * DUTY_FLOOR exists because the DAGU gearboxes hum without turning below roughly 70/255. Rather
 * than let a near-zero command sit there buzzing and heating the L293Ds, anything non-zero below
 * the floor is raised to it -- and the Pi, which knows this, sends a true 0 for anything smaller.
 *
 * No library. The shield is two L293Ds whose direction inputs hang off an SN74HC595 shift
 * register, which is all AFMotor.h ever did, and the Library Manager on the build machine only
 * offered an R4 fork with no AFMotor.h header. Do not reintroduce the dependency without a reason.
 */
#include <Arduino.h>

// ---- shield wiring, fixed by the board, mirrors hardware/PINMAP.md ----
const uint8_t DIR_LATCH = 12;
const uint8_t DIR_CLK   = 4;
const uint8_t DIR_SER   = 8;
const uint8_t DIR_EN    = 7;    // output enable, ACTIVE LOW. Held high every motor is dead
                                // while everything else looks perfectly correct.
const uint8_t PWM_M1 = 11, PWM_M2 = 3, PWM_M3 = 6, PWM_M4 = 5;

// Bit positions inside the shift register, as the shield's traces run them.
const uint8_t M1_A = 2, M1_B = 3;
const uint8_t M2_A = 1, M2_B = 4;
const uint8_t M3_A = 5, M3_B = 7;
const uint8_t M4_A = 0, M4_B = 6;

// The four shield terminals, in order M1, M2, M3, M4.
const uint8_t M_A[4]   = {M1_A, M2_A, M3_A, M4_A};
const uint8_t M_B[4]   = {M1_B, M2_B, M3_B, M4_B};
const uint8_t M_PWM[4] = {PWM_M1, PWM_M2, PWM_M3, PWM_M4};

// Which side each motor drives, and which way round it is wired.
//
// ONE SIGN PER MOTOR, not one per side. Nothing forces the two motors on a side to be wired with
// matching polarity -- whoever screwed the leads into the terminal block chose, per motor, and on
// this chassis they did not all choose the same. A per-side sign cannot express that, and the
// symptom is exactly what it looks like: "forward" turns some wheels forward and others backward.
// Flip the offending motor to -1 here and nowhere else; the protocol's sign convention is fixed.
// Find them with the "t" self-test, which runs each motor FORWARD in turn and names it.
// Measured on the real chassis 2026-09-19 with the "t" self-test: every motor already turns the
// way the robot drives forward, so every sign is +1. What was wrong was the SIDES -- M1 and M2
// are the right-hand wheels, not the left. Forward still looked fine (both sides get the same
// duty), so the mirror only showed up on turns, with A steering right and D steering left.
//
//   M1 front right      M3 front left
//   M2 back right       M4 back left
const int8_t M_SIGN[4] = { +1, +1, +1, +1 };            // M1, M2, M3, M4
const bool   M_LEFT[4] = { false, false, true, true };  // M3+M4 left, M1+M2 right

const unsigned long WATCHDOG_MS = 600;
const int DUTY_FLOOR = 70;
const int DUTY_MAX   = 255;

static uint8_t latch_state = 0;
static int cur_l = 0, cur_r = 0;
static unsigned long last_cmd_ms = 0;
static bool stopped_by_watchdog = true;
static char line[32];
static uint8_t line_len = 0;

static void latch_write() {
  digitalWrite(DIR_LATCH, LOW);
  shiftOut(DIR_SER, DIR_CLK, MSBFIRST, latch_state);
  digitalWrite(DIR_LATCH, HIGH);
}

static void set_bit(uint8_t bit, bool on) {
  if (on) latch_state |= (uint8_t)(1 << bit);
  else    latch_state &= (uint8_t)~(1 << bit);
}

/* One motor: duty > 0 forward, < 0 back, 0 coasts with both direction bits low. */
static void motor(uint8_t a, uint8_t b, uint8_t pwm_pin, int duty) {
  if (duty == 0) {
    set_bit(a, false); set_bit(b, false); latch_write();
    analogWrite(pwm_pin, 0);
    return;
  }
  bool fwd = duty > 0;
  set_bit(a, fwd); set_bit(b, !fwd); latch_write();
  int mag = duty > 0 ? duty : -duty;
  if (mag > DUTY_MAX) mag = DUTY_MAX;
  if (mag < DUTY_FLOOR) mag = DUTY_FLOOR;      // below this the gearbox hums and does not turn
  analogWrite(pwm_pin, mag);
}

static void drive(int l, int r) {
  if (l >  DUTY_MAX) l =  DUTY_MAX;
  if (l < -DUTY_MAX) l = -DUTY_MAX;
  if (r >  DUTY_MAX) r =  DUTY_MAX;
  if (r < -DUTY_MAX) r = -DUTY_MAX;
  cur_l = l; cur_r = r;
  for (uint8_t i = 0; i < 4; i++) motor(M_A[i], M_B[i], M_PWM[i], M_SIGN[i] * (M_LEFT[i] ? l : r));
}

static void ack() {
  Serial.print(F("ok ")); Serial.print(cur_l); Serial.print(' '); Serial.println(cur_r);
}

/* Bring-up only: name each motor as it runs, so a human can map motors to wheels. */
static void self_test() {
  // FORWARD ONLY, one motor at a time, with a gap long enough to say which wheel moved out loud.
  // Running each motor both ways told you which wheel was which but not which way round it was
  // wired, which is the thing that actually needs fixing. Every wheel should turn the way the
  // robot drives forward; any that does not gets -1 in M_SIGN above.
  for (uint8_t i = 0; i < 4; i++) {
    Serial.print(F("test M")); Serial.print(i + 1); Serial.println(F(" forward"));
    motor(M_A[i], M_B[i], M_PWM[i], M_SIGN[i] * 150); delay(1500);
    motor(M_A[i], M_B[i], M_PWM[i], 0);               delay(1200);
  }
  drive(0, 0);
  last_cmd_ms = millis();
  Serial.println(F("test done"));
}

static void handle(char *s) {
  if (s[0] == '\0') return;
  if (s[0] == 's' && s[1] == '\0') {
    drive(0, 0); last_cmd_ms = millis(); stopped_by_watchdog = false; ack(); return;
  }
  if (s[0] == '?' && s[1] == '\0') { ack(); return; }
  if (s[0] == 't' && s[1] == '\0') { self_test(); return; }

  char *end = NULL;
  long l = strtol(s, &end, 10);
  if (end == s) { Serial.println(F("err")); return; }
  char *p = end;
  while (*p == ' ' || *p == '\t') p++;
  char *end2 = NULL;
  long r = strtol(p, &end2, 10);
  if (end2 == p) { Serial.println(F("err")); return; }

  drive((int)l, (int)r);
  last_cmd_ms = millis();
  stopped_by_watchdog = false;
  ack();
}

void setup() {
  pinMode(DIR_LATCH, OUTPUT); pinMode(DIR_CLK, OUTPUT); pinMode(DIR_SER, OUTPUT);
  pinMode(DIR_EN, OUTPUT); digitalWrite(DIR_EN, LOW);      // active low: enable the outputs
  pinMode(PWM_M1, OUTPUT); pinMode(PWM_M2, OUTPUT);
  pinMode(PWM_M3, OUTPUT); pinMode(PWM_M4, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT);
  latch_state = 0; latch_write();
  drive(0, 0);
  Serial.begin(115200);
  Serial.println(F("scoutable-motor-v1 ready"));
  last_cmd_ms = millis();
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      line[line_len] = '\0';
      handle(line);
      line_len = 0;
    } else if (line_len < sizeof(line) - 1) {
      line[line_len++] = c;
    }
    // A line longer than the buffer simply keeps its first 31 characters; no command is that
    // long, and dropping the overflow beats blocking or reallocating in the driving path.
  }

  if (!stopped_by_watchdog && millis() - last_cmd_ms > WATCHDOG_MS) {
    drive(0, 0);
    stopped_by_watchdog = true;
    Serial.println(F("watchdog stop"));
  }

  // Heartbeat: a double blink each second says this sketch, specifically, is the one running.
  unsigned long t = millis() % 1000;
  digitalWrite(LED_BUILTIN, (t < 80 || (t > 160 && t < 240)) ? HIGH : LOW);
}
