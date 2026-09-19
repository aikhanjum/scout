# firmware

> **SUPERSEDED, 2026-09-19.** The ESP32 never arrived from the hardware desk. Scout's motor
> controller is a **SparkFun RedBoard** running `06_serial_drive.ino`, flashed from the Arduino
> IDE and owned by the firmware workstream; `docs/PROTOCOL.md` section 9 describes the board that
> is actually fitted. **Nothing below is flashed to Scout, and `npm run flash` has been removed.**
> This directory is kept only because the code compiles clean and costs nothing to leave alone.

ESP32 bridge: motors, 500 ms watchdog, beep, LEDs. Speaks `docs/PROTOCOL.md` section 9 over USB serial. No wifi and no IMU — Scout senses with the lidar and camera on the Pi. Pins and sign flips are in `src/config.h` and mirror `hardware/PINMAP.md`.

## Build and flash

```
pipx install platformio          # once. pio lands in ~/.local/bin
cd firmware
pio run                          # compile only (first run downloads the toolchain, a few minutes)
pio run -t upload                # flash the board on USB
pio device monitor -b 115200     # watch the JSON lines. Type T + Enter for the self-test.
```

(`npm run flash` was removed from `package.json`: it would flash a board Scout does not have.)

## Bring-up (hello hardware)

1. Flash. The monitor should show `{"hello":"scout-esp32","fw":"0.2.0"}` then `{"t":...,"v":...,"w":...}` at 10 Hz.
2. Type `T`: left side forward then back, right side forward then back, a chirp, both LEDs. A side running backwards means flip `LEFT_SIGN` or `RIGHT_SIGN` in `config.h`.
3. `D 0.4 0` drives forward; stop typing and it halts by itself after 500 ms. `S` stops now. `B 1` is the fail beep, `L 1 0` the red LED.
4. If the ESP32 resets when the motors start, the motors are sharing the ESP32's supply. See PINMAP.md, power.

Note that no motor driver is sourced yet; the firmware assumes a two-channel H-bridge with each side's pair ganged. See `hardware/PINMAP.md`.
