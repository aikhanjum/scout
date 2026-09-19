# Scout pin map and power

Frozen with `firmware/src/config.h`. Change both together.

## ESP32 (dev board, 3.3 V logic)

| Signal | GPIO | Notes |
| --- | --- | --- |
| TB6612 PWMA, AIN1, AIN2 | 25, 26, 27 | Left motor |
| TB6612 PWMB, BIN1, BIN2 | 32, 33, 14 | Right motor |
| TB6612 STBY | 3.3 V | Tied high, saves a pin |
| TB6612 VCC | 3.3 V | Logic supply of the driver |
| TB6612 VM | 4xAA + | Motor supply, 6 V |
| MPU6050 SDA, SCL | 21, 22 | Check whether the breakout wants 3.3 V or 5 V on VCC. Mount flat and rigid, X arrow forward, chip up. No foam tape: wobble becomes slope noise. |
| Buzzer | 4 | |
| Red LED, green LED | 16, 17 | 330 ohm series resistors |
| Reserved | 13, 23 (bumpers), 35 (start button) | Not used by v1 firmware |

Avoid GPIO 0, 2, 12 and 15 for anything that could be pulled at boot. GPIO 34, 35, 36, 39 are input only.

Gone since v1.1: the servo, the HC-SR04 and its echo divider. Width comes from the lidar on the Pi.

## Pi 4 and lidar

- RPLIDAR A1 on the Pi by USB (its CP2102 adapter). Mount it level, at the top, with a clear 360 degree view: nothing of the robot above the lidar's beam plane. Note which way the A1's 0 degree mark points; if it is not straight ahead, set `SCOUT_LIDAR_OFFSET_DEG` for the Pi service.
- ESP32 on the Pi by USB. That cable is also the ESP32's power.
- The Pi needs a true 5 V / 3 A source (USB-C). Budget: Pi 4 up to 3 A peak, lidar about 0.4 A, ESP32 about 0.2 A. A weak power bank shows up as the Pi's lightning-bolt icon and random lidar dropouts.

## Power, three separate rails, one ground

| Rail | Source | Feeds |
| --- | --- | --- |
| Motors 6 V | 4xAA holder | TB6612 VM only |
| Pi 5 V | USB power bank, 5 V / 3 A port | Pi 4 by USB-C, which feeds the lidar and the ESP32 over USB |
| Logic 3.3 V | ESP32's own regulator | TB6612 VCC, MPU6050 (if 3.3 V), LEDs |

All grounds connected together: AA pack minus, TB6612 GND, ESP32 GND. The Pi and ESP32 share ground through USB.

Symptom: the ESP32 resets when the motors start. Cause: the motors are on the ESP32's supply. Fix: the table above. Fresh AAs before judging; spares in the field kit.

## Bring-up order

1. ESP32 on USB, no motors connected: flash, see `hello` and telemetry, `imu:true`.
2. Connect the TB6612 and motors, wheels off the ground: `T` self-test, fix wheel signs.
3. Tilt test for pitch, roll, yaw signs.
4. Lidar on the Mac: `cd pi && .venv/bin/python -m scout` must log both devices found.
5. Same on the Pi.
