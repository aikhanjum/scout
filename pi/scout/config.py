"""Settings. Environment variables override the defaults; scout.service sets them on the Pi."""
import os


def _env(name, default, cast=str):
    v = os.environ.get(name)
    return default if v is None or v == "" else cast(v)


VERSION = "0.1.0"
PORT = _env("SCOUT_PORT", 8080, int)
ESP32_PORT = _env("SCOUT_ESP32_PORT", None)          # unset: probe. "none": disabled. Else a device path.
LIDAR_PORT = _env("SCOUT_LIDAR_PORT", None)
LIDAR_OFFSET_DEG = _env("SCOUT_LIDAR_OFFSET_DEG", 0.0, float)  # the lidar angle that points straight ahead
TELEM_HZ = 10
RUN_BUFFER_TELEM = 1200   # 2 Hz decimated telemetry kept in the run buffer: 10 minutes. Events are never dropped.

# Boot defaults of the runtime config (PROTOCOL.md section 4). The dashboard changes limits and scale;
# width_offset_mm is a robot calibration (0 when the lidar sits at the body's centre).
CONFIG = {
    "slope_limit_deg": 4.76,
    "width_limit_mm": 860.0,
    "scale": 1.0,
    "width_offset_mm": _env("SCOUT_WIDTH_OFFSET_MM", 0.0, float),
}
