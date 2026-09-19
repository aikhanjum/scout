// Pins and calibration. Mirrors hardware/PINMAP.md. Change both together.
#pragma once

#define FW_VERSION "0.1.0"

// TB6612FNG motor driver. STBY is tied to 3.3 V. VM from the 4xAA pack, VCC from 3.3 V, all grounds common.
#define PIN_PWMA 25   // left motor
#define PIN_AIN1 26
#define PIN_AIN2 27
#define PIN_PWMB 32   // right motor
#define PIN_BIN1 33
#define PIN_BIN2 14

// MPU6050, I2C address 0x68. Mount flat and rigid, X arrow forward, chip facing up.
#define PIN_SDA 21
#define PIN_SCL 22

#define PIN_BUZZER 4
#define PIN_LED_RED 16
#define PIN_LED_GREEN 17

// Reserved for later (not read by this firmware): bumper left 13, bumper right 23, start button 35.

// If a wheel runs backwards, flip its sign here instead of rewiring.
#define LEFT_SIGN 1
#define RIGHT_SIGN 1

// Send T (self-test) and tilt the robot. If pitch goes negative when the nose goes up, flip PITCH_SIGN.
// roll positive = right side down. yaw positive = turning left.
#define PITCH_SIGN 1
#define ROLL_SIGN 1
#define YAW_SIGN 1

#define MIN_DUTY 60          // of 255. Hobby gearmotors stall below this. Raise it if the robot hums without moving.
#define SLEW_PER_TICK 0.08f  // speed change per 20 ms tick: stopped to full in about 250 ms
#define WATCHDOG_MS 500
#define TELEM_MS 100
#define GYRO_CAL_SAMPLES 200 // at 100 Hz: 2 s still after boot
