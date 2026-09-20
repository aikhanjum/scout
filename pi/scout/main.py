"""Start the Scout brain: find the devices, start their threads, serve protocol v2."""
import logging

from aiohttp import web

from . import config, ports
from .audit import Audit
from .redboard import RedBoard
from .lidar import Lidar
from .mapping import Grid
from .pose import Pose
from .server import Scout, make_app, _ip
from .wallfollow import WallFollow

log = logging.getLogger("scout")


def _pose_engine():
    """Slam or the rectangle fitter, whichever SCOUT_POSE asks for (config.POSE_MODE).

    Slam needs numpy. A missing library disables its own feature and says so loudly rather than
    taking the service down with it (rule 9), and here the fallback is a fitter that works, so the
    only thing lost is rooms that are not rectangles.
    """
    if config.POSE_MODE == "rect":
        log.info("POSE: rectangle fit (SCOUT_POSE=rect). One closed rectangular room, no drift.")
        return Pose()
    try:
        from .slam import Slam
    except ImportError as e:
        log.error("POSE: numpy is missing (%s), so slam cannot run. Falling back to the rectangle "
                  "fit, which needs one closed rectangular room. pip install numpy", e)
        return Pose()
    log.info("POSE: slam, scan matching against the map so far. Any room shape, and it drifts: "
             "check the map against a tape measure before trusting it. SCOUT_POSE=rect for the "
             "rectangle fit instead.")
    return Slam(span_mm=config.MAP_SPAN_MM)


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

    cfg = dict(config.CONFIG)
    if config.MASK_BEHIND_DEG:
        log.info("MASK: ignoring returns within %d degrees of straight behind (a carrier's body). "
                 "SCOUT_MASK_BEHIND_DEG=0 when Scout drives itself.", config.MASK_BEHIND_DEG)
    scout = Scout(motor, lidar, _pose_engine(), Grid(), Audit(cfg), WallFollow(cfg), cfg)
    log.info("serving http://%s:%d  ws://%s:%d/ws  (also localhost)", _ip(), config.PORT, _ip(), config.PORT)
    web.run_app(make_app(scout), host="0.0.0.0", port=config.PORT, print=None, access_log=None)
