# firmware

ESP32 bridge: motors, IMU, 500 ms watchdog, beep, LEDs. Speaks `docs/PROTOCOL.md` section 8 over USB serial. No wifi. Pins and sign flips are in `src/config.h` and mirror `hardware/PINMAP.md`.

## Build and flash

```
pipx install platformio          # once. pio lands in ~/.local/bin
cd firmware
pio run                          # compile only (first run downloads the toolchain, a few minutes)
pio run -t upload                # flash the board on USB
pio device monitor -b 115200     # watch the JSON lines. Type T + Enter for the self-test.
```

Or from the repo root: `npm run flash`.

## Bring-up (hello hardware)

1. Flash. The monitor should show `{"hello":"scout-esp32",...}` then telemetry at 10 Hz. `"imu":false` means the MPU6050 is not answering on SDA 21 / SCL 22.
2. Keep the robot still for 2 s after boot (gyro bias).
3. Type `T`: left wheel forward then back, right wheel forward then back, a chirp, both LEDs. A wheel running backwards means flip `LEFT_SIGN` or `RIGHT_SIGN` in `config.h`.
4. Lift the nose: `pitch` must go positive. Tip the right side down: `roll` positive. Turn left: `yaw` up. Flip the matching `*_SIGN` if not.
5. `D 0.4 0` drives forward; stop typing and it halts by itself after 500 ms. `S` stops now. `Z` zeroes on a flat floor.
6. If the ESP32 resets when the motors start, the motors are sharing the ESP32's supply. See PINMAP.md, power.
