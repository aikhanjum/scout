#!/usr/bin/env bash
# Build src/main.cpp and flash it to the RedBoard on the Pi. Edit, run this, drive.
#
#   ./firmware/redboard/build-and-flash.sh            # uses PI below
#   PI=172.20.10.9 ./firmware/redboard/build-and-flash.sh
#
# PlatformIO's CLI is not installed on this Mac, though its toolchain is, so this calls avr-g++
# directly and links against the Arduino core archive already built under .pio/. If you ever
# reinstall PlatformIO, "pio run" does the same job.
set -euo pipefail

PI="${PI:-172.20.10.12}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
T="$HOME/.platformio/packages/toolchain-atmelavr/bin"
F="$HOME/.platformio/packages/framework-arduino-avr"
B="$HERE/.pio/build/uno"

[ -x "$T/avr-g++" ]            || { echo "no avr-g++ at $T"; exit 1; }
[ -f "$B/libFrameworkArduino.a" ] || { echo "no Arduino core archive at $B"; exit 1; }

echo "== compile =="
"$T/avr-g++" -o "$B/src/main.cpp.o" -c -std=gnu++17 -fno-exceptions -fno-threadsafe-statics \
  -fpermissive -Wno-error=narrowing -Os -Wall -ffunction-sections -fdata-sections -flto \
  -mmcu=atmega328p -DF_CPU=16000000L -DARDUINO=10808 -DARDUINO_AVR_UNO -DARDUINO_ARCH_AVR \
  -DPLATFORMIO=60118 -I"$F/cores/arduino" -I"$F/variants/standard" "$HERE/src/main.cpp"

"$T/avr-g++" -o "$B/firmware.elf" -Os -mmcu=atmega328p -Wl,--gc-sections -flto -fuse-linker-plugin \
  "$B/src/main.cpp.o" -L"$B" -Wl,--start-group -lFrameworkArduino -lFrameworkArduinoVariant -lm -Wl,--end-group

"$T/avr-objcopy" -O ihex -R .eeprom "$B/firmware.elf" "$B/firmware.hex"
"$T/avr-size" --mcu=atmega328p -C "$B/firmware.elf" | grep -E "Program|Data"

echo "== copy to $PI =="
scp -q -o ConnectTimeout=10 "$B/firmware.hex" "$PI:/tmp/firmware.hex"

# The service holds the serial port, so it has to let go before the bootloader can be reached.
echo "== flash =="
ssh -o ConnectTimeout=10 "$PI" '
  sudo systemctl stop scout && sleep 1
  cd ~/scout/pi && .venv/bin/python -u tools/flash_redboard.py /tmp/firmware.hex \
    | grep -v "^write :\|^verify: [0-9]"
  sudo systemctl start scout
'
echo "== done, service restarted =="
