# Scout pin map and power

> **Superseded in part.** The ESP32 never arrived. The robot that ran at the demo uses a SparkFun RedBoard
> (ATmega328P) with a DK Electronics motor shield (2x L293D), so the "no motor driver" item below is closed.
> Current wiring and power are in [`docs/HANDOFF-REDBOARD.md`](../docs/HANDOFF-REDBOARD.md) sections 4 and 6
> and [`docs/PROTOCOL.md`](../docs/PROTOCOL.md) section 9. The ESP32 pinout below is kept for reference.

Frozen with `firmware/src/config.h`. Change both together.

## Two things are still open

Neither is decided, and both block a driving robot. Flagged here rather than designed around.

1. **No motor driver is sourced.** Four DAGU 48:1 gearmotors need an H-bridge. The firmware is written for a two-channel driver with the left pair ganged on channel A and the right pair on channel B. A TB6612FNG (1.2 A per channel continuous) is adequate for a light chassis on a smooth floor and will thermal-shutdown if stalled repeatedly; an L298N takes more abuse and wastes about 2 V. Whichever is used, the pinout below assumes PWM + two direction pins per channel.
2. **No 5 V regulation is sourced.** Four 18650s are 7.4 V or 8.4 V and cannot feed a Pi 4 directly. See the power section: this is the failure that looks like a software bug.

## ESP32 (dev board, 3.3 V logic)

| Signal | GPIO | Notes |
| --- | --- | --- |
| Driver PWMA, AIN1, AIN2 | 25, 26, 27 | Left side: both left motors on this channel |
| Driver PWMB, BIN1, BIN2 | 32, 33, 14 | Right side: both right motors |
| Driver STBY | 3.3 V | Tied high, saves a pin (TB6612 only) |
| Driver VCC (logic) | 3.3 V | |
| Driver VM (motors) | Battery + | Never the ESP32's regulator |
| Buzzer | 4 | |
| Red LED, green LED | 16, 17 | 330 ohm series resistors |
| Free | 13, 21, 22, 23, 35 | 21 and 22 were the IMU's I2C and are now spare |

Avoid GPIO 0, 2, 12 and 15 for anything that could be pulled at boot. GPIO 34, 35, 36, 39 are input only.

Gone in v2: the MPU6050. Scout has no IMU and measures no slope. Also gone since v1.1: the servo, the HC-SR04 and its echo divider.

## Pi 4 and lidar

- **RPLIDAR A2M8** on the Pi by USB (its CP2102 adapter). Its motor does not spin on power alone; the driver starts it by PWM through the adapter, so a healthy-looking lidar that streams nothing means the motor command did not take. Mount it level, at the top, with a clear 360 degree view: nothing of the robot above the beam plane, or that part of the robot becomes a permanent wall in every scan and the room fit fails. Note which way the A2M8's 0 degree mark points; if it is not straight ahead, set `SCOUT_LIDAR_OFFSET_DEG`.
- **ESP32** on the Pi by USB. That cable is also the ESP32's power.
- The Pi needs a true 5 V / 3 A source. Budget: Pi 4 up to 3 A peak, lidar about 0.4 A, ESP32 about 0.2 A.

## Power, separate rails, one ground

| Rail | Source | Feeds |
| --- | --- | --- |
| Motors | 18650 pack | Driver VM only |
| Pi 5 V | Its own regulator, or a USB power bank | Pi 4, which feeds the lidar and ESP32 |
| Logic 3.3 V | ESP32's own regulator | Driver VCC, LEDs |

All grounds connected: pack minus, driver GND, ESP32 GND. The Pi and ESP32 share ground through USB.

**The two rules that matter more than the parts.**

1. **Never run the Pi from the same rail as the motors.** Four motors stalling pull about 2.8 A in a step; the pack dips and the Pi resets.
2. **Use protected cells or a BMS.** Unprotected 18650s and loose hookup wire on a hackathon table is a fire risk, and over-discharge quietly kills the cells.

**Sizing the buck converter.** Realistic peak on the 5 V rail is about 2.5 A, so 3 A is the floor and 5 A is the target. The common LM2596 module is the wrong part: its "3 A" is a peak figure and without a heatsink it sags around 1.5 to 2 A, which is exactly the Pi-4-brownout zone. An XL4015 or any honest 5 A buck is right.

**The symptom to know.** An undersized 5 V rail does not crash the Pi cleanly. It browns out the USB ports first, so the lidar drops out and reconnects at random and it looks like a software bug. The Pi will tell you the truth: `vcgencmd get_throttled` returns `0x0` when healthy, bit 0 means under-voltage right now, bit 16 means it happened since boot. Check it before blaming the code.

**The zero-risk shortcut.** Put the Pi and lidar on a USB power bank and give the 18650s to the motors alone. No new parts, and the whole class of problem disappears.

Symptom: the ESP32 resets when the motors start. Cause: the motors are on the ESP32's supply. Fix: the table above.

## Bring-up order

1. ESP32 on USB, no motors connected: flash, see `hello` and the 10 Hz echo lines.
2. Connect the driver and motors, wheels off the ground: `T` self-test, fix side signs with `LEFT_SIGN` / `RIGHT_SIGN`.
3. Check `vcgencmd get_throttled` on the Pi with everything powered and the motors stalled.
4. Lidar on the Mac: `cd pi && .venv/bin/python -m scout` must log both devices found, and the dashboard must draw a scan ring that looks like the room.
5. Put Scout on the floor in the test room and press ROAM. The map should close a room outline within a lap.
6. Same on the Pi.
