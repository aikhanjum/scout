// Pins and calibration. Mirrors hardware/PINMAP.md. Change both together.
#pragma once

#define FW_VERSION "0.2.0"

// Motor driver, two channels. The four gearmotors are ganged as two sides: both left motors on
// channel A, both right on channel B. STBY tied high. VM from the battery pack, never from the
// ESP32's regulator. See hardware/PINMAP.md for the driver and the 5 V rail, both still open.
#define PIN_PWMA 25   // left side
#define PIN_AIN1 26
#define PIN_AIN2 27
#define PIN_PWMB 32   // right side
#define PIN_BIN1 33
#define PIN_BIN2 14

#define PIN_BUZZER 4
#define PIN_LED_RED 16
#define PIN_LED_GREEN 17

// Free for later (not read by this firmware): 13, 23, 35. GPIO 21/22 were the IMU's I2C and are
// now spare. A servo, if one is ever fitted, wants a PWM-capable pin such as 18.

// If a wheel runs backwards, flip its sign here instead of rewiring.
#define LEFT_SIGN 1
#define RIGHT_SIGN 1

#define MIN_DUTY 60          // of 255. Hobby gearmotors stall below this. Raise it if the robot hums without moving.
#define SLEW_PER_TICK 0.08f  // speed change per 20 ms tick: stopped to full in about 250 ms
#define WATCHDOG_MS 500
#define TELEM_MS 100
