> ## Read this first — three items below are superseded
>
> Annotated 2026-09-19 after the RedBoard was merged into the Pi service. **`CLAUDE.md`,
> `docs/SPEC.md`, `docs/PROTOCOL.md`, `docs/HANDOFF.md` and `RUNBOOK.md` are the source of truth
> where this document disagrees with them.** Confirmed with the team:
>
> 1. **No IMU, and no ramp incline.** §7's MPU6050 plan and §8's "ramp incline from pitch angle"
>    are **not being built**. Nothing on Scout measures slope, so **ramps are labelled by the
>    camera and never judged**, and clearance width is the only building-code verdict. Protocol v2
>    removed `pitch_deg`, `slope_pass`/`slope_fail` and the `Z` command to make that true. Do not
>    reintroduce a slope number. See `docs/HANDOFF.md` §1 and §9.
> 2. **The RPLIDAR was not denied.** Scout has an **RPLIDAR A2M8** and its USB adapter, and the
>    lidar is the whole sensing story: geometry, pose, the occupancy map and every width verdict.
>    The ultrasonic-on-a-servo fallback in §7 is not needed and is not being built.
> 3. **Networking is the phone hotspot**, 2.4 GHz with Maximize Compatibility on — not a Pi-hosted
>    hotspot. The Pi joins it as `scout.local`. See `RUNBOOK.md` §1 and §10.
>
> **What in here is authoritative and was used as written:** §4 (the serial interface), §6 (power)
> and §10 (the gotchas). Those are tested bench facts about a board that works, and the Pi service
> was adapted to them exactly — see `docs/PROTOCOL.md` section 9 and `pi/scout/redboard.py`.
>
> Two smaller corrections: §5's udev rule is **not needed**, because `pi/scout/ports.py` finds both
> devices by probing what answers rather than by device path or vendor ID; and §7's note on
> odometry is already settled — `pi/scout/pose.py` reads position off the room's fitted rectangle,
> which is why the test room must be oblong and closed.

# Scoutable — Raspberry Pi Handoff

**Project:** Scoutable, an accessibility-auditing robot. It drives through a venue and
records accessibility data: ramp inclines, doorway widths, obstacles and blockages.
Two modes are wanted — remote control by an operator, and onboard autonomous path
finding. Hackathon build, September 19 2026.

**Split of work:** Aidan is continuing on motor control and the RedBoard firmware.
This document is for whoever picks up the Raspberry Pi side: lidar, mapping,
networking and the measurement logic.

---

## 1. Architecture

Two computers, one USB cable between them.

```
  ┌─────────────────────────┐         ┌──────────────────────────┐
  │  Raspberry Pi 4 (2 GB)  │  USB-A  │  SparkFun RedBoard       │
  │  - lidar driver         │────────▶│  (ATmega328P, Uno clone) │
  │  - SLAM / mapping       │  serial │  - motor PWM + direction │
  │  - path planning        │ 115200  │  - 600 ms watchdog       │
  │  - Wi-Fi / operator UI  │◀────────│  - replies "ok L R"      │
  │  - accessibility logic  │         └──────────┬───────────────┘
  └─────────────────────────┘                    │ shield headers
                                                 ▼
                                   ┌──────────────────────────────┐
                                   │ DK Electronics motor shield  │
                                   │ (Adafruit v1 clone)          │
                                   │ 2× L293D + SN74HC595         │
                                   └──────────┬───────────────────┘
                                              ▼
                                   4× DAGU 48:1 mini gearmotors
```

**Why the split:** the RedBoard handles hard real-time work (PWM generation, an
immediate stop on watchdog timeout) that Linux cannot guarantee. The Pi does
everything that needs memory and CPU. The RedBoard has 2 KB of RAM, so keep all
mapping, the occupancy grid and path planning on the Pi. The RedBoard should only
ever execute motor commands and report back.

**Note:** an ESP32 was originally planned for the motor-control role. It never
arrived from the hardware desk, so the RedBoard took that role. No functional loss —
the Pi is the network host anyway.

---

## 2. Hardware inventory

| Part | Status | Notes |
|---|---|---|
| Raspberry Pi 4, 2 GB | have | main computer |
| SparkFun RedBoard | **working** | motor controller, USB to Pi |
| DK Electronics motor shield (Adafruit v1 clone) | **working** | 2× L293D, SN74HC595 |
| 4× DAGU mini gearmotor, 48:1 | **working** | ~3–6 V rated |
| 4× wheels | have | |
| RPLidar | **uncertain — see §7** | reservation was denied; may be Aidan's own unit |
| 16 GB microSD | have | |
| 4× 18650 cells + 2 holders | have | not yet in use |
| 4×AA pack | **in use** | current motor supply for bench testing |
| Tower Pro micro servo | have | camera pan, not yet implemented |
| Pi camera | have | not yet implemented |
| 3D-printed two-part chassis | have | |
| TB6612FNG breakout | **abandoned** | see §6 |

---

## 3. Current state — what works

- All four motors run under RedBoard control via the shield.
- Individual motor control, both directions, variable speed.
- Serial command protocol with a watchdog failsafe (firmware `06_serial_drive.ino`).
- A Python teleop script (`drive.py`) is written but **not yet tested on the Pi**.

**Not started:** lidar, SLAM, camera, servo, Pi hotspot, the accessibility
measurement logic, autonomous navigation.

---

## 4. The serial interface — this is your API

The RedBoard runs `06_serial_drive.ino`. It appears on the Pi as `/dev/ttyUSB0`
(its CH340 USB chip enumerates as `ttyUSB*`, not `ttyACM*`).

**Port:** 115200 baud, 8N1, newline-terminated ASCII.

### Commands (Pi → RedBoard)

| Send | Meaning |
|---|---|
| `<left> <right>\n` | drive; each value −255…255, e.g. `200 200`, `-150 150` |
| `s\n` | stop immediately |
| `?\n` | status: prints current L, R and ms since last command |

Positive is forward. Skid steer: `200 200` drives straight, `-200 200` spins in
place. The two motors on each side are driven together; there is no per-wheel
control, and none is needed.

### Replies (RedBoard → Pi)

| Reply | Meaning |
|---|---|
| `scoutable-motor-v1 ready` | banner, sent once on boot |
| `ok <L> <R>` | command accepted, with the clamped values applied |
| `err` | command not parseable |
| `watchdog stop` | no command for 600 ms, motors stopped automatically |

### The watchdog — important

**If no command arrives for 600 ms, the motors stop by themselves.** Your code must
resend the current command continuously (10 Hz is plenty) for as long as the robot
should keep moving. This is deliberate: if the Pi crashes, the script dies, or the
USB cable pops out, the robot halts instead of driving into a wall.

Design your control loop around this. Do not raise the timeout to avoid resending.

### Two behaviours that will confuse you if unexpected

1. **Opening the serial port resets the RedBoard.** It reboots in about 1.5 s.
   Sleep 2 s after opening the port before sending anything, or the first commands
   are lost. `drive.py` already does this.
2. **Duty below 70 is raised to 70 in firmware.** Below that the gearboxes hum
   without turning. So `drive(30, 30)` behaves as `drive(70, 70)`. If you need
   finer low-speed control, use timed pulses rather than lower duty.

---

## 5. Pi setup

```bash
sudo apt install python3-serial
sudo usermod -a -G dialout $USER     # then log out and back in
ls /dev/ttyUSB*                      # expect /dev/ttyUSB0
```

The `dialout` group membership is required, or you get
`cannot open port: permission denied`. A temporary fix while testing is
`sudo chmod a+rw /dev/ttyUSB0`, but it resets on every replug.

`raspi-config` serial settings are **not** relevant here — those govern the Pi's
GPIO UART pins, and this link is over USB.

### Using `drive.py` as a library

```python
from drive import Rover

with Rover() as rover:            # auto-detects the port
    rover.drive(200, 200)         # forward
    rover.drive(-200, 200)        # spin left
    rover.stop()
```

The `with` block guarantees the motors stop and the port closes even if your code
raises. `Rover.readline()` reads one reply line.

Run it directly (`python3 drive.py`) for keyboard teleop: `w/a/s/d`, space to stop,
`+`/`-` for speed, `q` to quit.

### Port naming once the lidar is attached

With both the RedBoard and an RPLidar plugged in, you get `/dev/ttyUSB0` and
`/dev/ttyUSB1`, **and the numbering can swap between boots.** Pin them with a udev
rule before this bites you:

```
# /etc/udev/rules.d/99-scoutable.rules
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", SYMLINK+="scoutable_motor"
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", SYMLINK+="scoutable_lidar"
```

Confirm the actual vendor IDs with `lsusb` first — `1a86` is CH340 (the RedBoard)
and `10c4` is CP210x (common on RPLidar adapters), but verify rather than assume.
Then use `/dev/scoutable_motor` instead of `/dev/ttyUSB0`.

---

## 6. Power

Three separate supplies. **Keep them separate.**

| Rail | Source | Feeds |
|---|---|---|
| Motor power | 4×AA now, 2×18650 later | shield EXT_PWR terminal only |
| Pi | power bank or wall adapter | Pi only |
| RedBoard logic | Pi USB cable | RedBoard + shield logic |

Grounds tie together through the USB cable. Nothing extra to wire.

**Never power the Pi from the motor battery** through the shield, and never connect
the motor pack to the RedBoard's barrel jack or 5 V pin. Motor current spikes cause
resets and brownouts.

**The shield's PWR jumper is removed and must stay removed.** With it fitted, the
motor supply is tied to the RedBoard's 5 V rail, which would push 6 V into a 5 V rail.

**Voltage note:** the L293D drops about 2–2.5 V internally, so the 4×AA pack (≈6 V)
leaves the motors only 3.5–4 V and they run slowly. That is expected and not a
fault. Moving to 2×18650 in series (7.4 V) gives them a proper 5–6 V. The shield's
L293Ds are 0.6 A continuous per channel; a stalled DAGU motor exceeds that and the
chip thermally shuts down, so avoid pinning the robot against obstacles.

---

## 7. Open items and unknowns

**Lidar availability is the biggest unknown.** The RPLidar hardware request was
**denied** at the reservation desk. Confirm with Aidan whether a personal unit is on
hand before building around it. If there is no lidar, the fallback is an ultrasonic
sensor sweeping on a servo, which changes the sensing architecture substantially.

If the lidar is present: it connects via its own USB adapter and appears as a serial
device (115200 baud for the A1, 256000 for the A2). The `rplidar_ros` driver supports
it under ROS 2.

**Also not yet done:**

- **Pi hotspot.** Campus networks such as eduroam usually block device-to-device
  traffic, so have the Pi host its own network rather than joining one:
  `sudo nmcli device wifi hotspot ssid scoutable password <yourpassword>`.
  Set this up and verify it early; it is the sort of thing that fails at demo time.
- **Odometry.** The DAGU motors have **no encoders**, so the robot cannot measure how
  far it has travelled from the motors. Dead reckoning from commanded speeds will
  drift badly. Options: rely on lidar scan matching for pose, or add wheel encoders
  (the RedBoard still has D2 and D3 free, which are its two interrupt pins).
- **Camera and pan servo.** Hardware on hand, nothing implemented. The shield's
  SER1/SER2 headers (D10/D9) can drive the servo if it ends up on the RedBoard.
- **IMU.** An MPU6050 was picked up. It is the intended source of ramp-angle data —
  its accelerometer measures the direction of gravity, so the tilt of that vector is
  the ramp incline. Mount it rigidly and level on the chassis, not on any swivel.
  Not yet wired. It is an I²C device at address 0x68.

---

## 8. Measurement approach (as designed, not yet built)

- **Ramp incline:** pitch angle from the MPU6050 while driving up the ramp. Smooth
  with the gyro. A commonly cited limit is 1:12, about 4.8° — verify against the
  applicable local building code rather than trusting that figure.
- **Doorway width:** left distance + right distance + the sensor spacing, taking the
  **minimum** across the pass, since the frame is the narrowest point. Ontario's
  barrier-free clear width is around 850 mm — again, verify.
- **Obstacles:** occupancy grid from lidar (or from a servo-swept range sensor),
  then A* over the grid for path planning.
- **Steps and drop-offs:** a downward-facing range sensor sees the floor distance
  jump suddenly. Useful both as a finding and as an emergency stop.

**Known blind spot: glass.** Both lidar and IR see through glass doors and walls.
Log glass as a known limitation rather than reporting an open doorway.

---

## 9. Files

| File | Runs on | Purpose |
|---|---|---|
| `06_serial_drive.ino` | RedBoard | **current firmware** — serial protocol + watchdog |
| `drive.py` | Pi | teleop script and `Rover` class |
| `05_shield_nolib.ino` | RedBoard | standalone motor test, no library needed |
| `04_shield_test.ino` | RedBoard | same, but needs the AFMotor v1 library |
| `01`–`03` sketches | RedBoard | pin-level diagnostics from the TB6612 debugging |

**Library note:** `05` and `06` deliberately include no library. They drive the
shield's SN74HC595 shift register directly, which is all `AFMotor.h` does. The
Arduino Library Manager on this machine only offered an R4-compatible fork of
AFMotor, which does not ship an `AFMotor.h` header, so the library route was dropped.
Do not reintroduce a library dependency without a reason.

---

## 10. Hard-won gotchas

Things that cost time today and will cost it again:

1. **Serial permission denied** on Linux → `dialout` group, then log out and back in.
2. **`brltty` hijacks CH340 devices** on Ubuntu 22.04+, making the port appear then
   vanish. `sudo apt remove brltty`.
3. **The shield's D7 is output-enable and is active LOW.** Held high, every motor
   output is dead while everything else looks correct.
4. **Battery order:** upload with the motor battery OFF, confirm the sketch is
   running, then switch the battery on. The board's pins float during reset and a
   wheel can lurch.
5. **The TB6612 route was abandoned** after a control signal could not be traced
   through a breadboard. If anyone revisits it: SparkFun prints that breakout's pin
   labels on the **bottom** silkscreen, and on the control row **AIN2 comes before
   AIN1**, which is reversed from the B side. Both are easy to get wrong.
6. **Confirm a sketch is actually running** before debugging hardware. Every sketch
   here blinks pin 13 in a distinctive pattern and prints a banner for this reason.
   A failed upload produces symptoms that look exactly like broken wiring.
