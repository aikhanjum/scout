"""Settings. Environment variables override the defaults; scout.service sets them on the Pi."""
import os


def _env(name, default, cast=str):
    v = os.environ.get(name)
    return default if v is None or v == "" else cast(v)


VERSION = "0.2.0"
PORT = _env("SCOUT_PORT", 8080, int)
# The motor board. SCOUT_ESP32_PORT is the old name for it and still works, because it is what
# RUNBOOK.md, the systemd override recipe and anyone's shell history already say.
MOTOR_PORT = _env("SCOUT_MOTOR_PORT", None) or _env("SCOUT_ESP32_PORT", None)   # unset: probe. "none": disabled.
LIDAR_PORT = _env("SCOUT_LIDAR_PORT", None)
LIDAR_OFFSET_DEG = _env("SCOUT_LIDAR_OFFSET_DEG", 0.0, float)  # the lidar angle that points straight ahead
CAMERA = _env("SCOUT_CAMERA", "on")                            # "none" disables the camera
TELEM_HZ = 10
RUN_BUFFER_TELEM = 1200   # 2 Hz decimated telemetry kept in the run buffer: 10 minutes. Events are never dropped.

MAP_HZ = 1                # map frames per second on /ws

# Boot defaults of the runtime config (PROTOCOL.md section 5). The dashboard sends the limit;
# the rest are robot calibration and come from the environment or scout.service.
CONFIG = {
    "width_limit_mm": 860.0,
    "robot_width_mm": _env("SCOUT_ROBOT_WIDTH_MM", 260.0, float),
    "wall_target_mm": _env("SCOUT_WALL_TARGET_MM", 300.0, float),
    "cruise": _env("SCOUT_CRUISE", 0.4, float),
}
