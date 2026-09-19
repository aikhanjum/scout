"""Start the Scout brain: find the devices, start their threads, serve protocol v2."""
import json
import logging
import os

from aiohttp import web

from . import config, ports
from .audit import Audit
from .camera import Camera
from .redboard import RedBoard
from .lidar import Lidar
from .mapping import Grid
from .pose import Pose
from .server import Scout, make_app, _ip
from .wallfollow import WallFollow

log = logging.getLogger("scout")


def _labels():
    """The label set the camera scores against lives in data/rules.json (PROTOCOL.md section 8),
    so changing what Scout can name is a data edit, not a code change."""
    path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "rules.json")
    try:
        with open(path) as f:
            return json.load(f).get("labels") or []
    except (OSError, ValueError) as e:
        log.warning("could not read data/rules.json (%s): the camera will have no labels", e)
        return []


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    log.info("scout pi %s starting. USB serial ports: %s", config.VERSION, ports.seen())

    motor = RedBoard(config.MOTOR_PORT)
    lidar = Lidar(config.LIDAR_PORT, config.LIDAR_OFFSET_DEG)
    # One probe pass now, so the startup log says what is connected. The threads take these as a
    # first hint and re-probe by content whenever a device goes away, so an unplug and replug onto
    # a new device name still works.
    if motor.enabled and motor.fixed_port is None:
        motor.port_hint, banner = ports.find("motor")
        if motor.port_hint:
            motor.fw = banner
            log.info("MOTOR  found on %s (%s)", motor.port_hint, banner)
        else:
            log.error("MOTOR  NOT FOUND: driving disabled. Ports seen: %s", ports.seen())
    if lidar.enabled and lidar.fixed_port is None:
        lidar.port_hint, info = ports.find("lidar")
        if lidar.port_hint:
            log.info("LIDAR  found on %s (model %s fw %s)", lidar.port_hint, info.model, info.firmware)
        else:
            log.error("LIDAR  NOT FOUND: mapping, width and roaming disabled. Ports seen: %s", ports.seen())
    motor.start()
    lidar.start()

    camera = None
    if config.CAMERA != "none":
        camera = Camera(_labels())
    else:
        log.warning("CAMERA DISABLED (SCOUT_CAMERA=none): obstacles will be reported as 'unknown'")

    cfg = dict(config.CONFIG)
    scout = Scout(motor, lidar, camera, Pose(), Grid(), Audit(cfg), WallFollow(cfg), cfg)
    log.info("serving http://%s:%d  ws://%s:%d/ws  (also localhost)", _ip(), config.PORT, _ip(), config.PORT)
    try:
        web.run_app(make_app(scout), host="0.0.0.0", port=config.PORT, print=None, access_log=None)
    finally:
        if camera:
            camera.close()
