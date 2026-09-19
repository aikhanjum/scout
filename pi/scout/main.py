"""Start the Scout brain: find the devices, start their threads, serve protocol v1."""
import logging

from aiohttp import web

from . import config, ports
from .audit import Audit
from .esp32 import Esp32
from .lidar import Lidar
from .server import Scout, make_app, _ip

log = logging.getLogger("scout")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    log.info("scout pi %s starting. USB serial ports: %s", config.VERSION, ports.seen())

    esp32 = Esp32(config.ESP32_PORT)
    lidar = Lidar(config.LIDAR_PORT, config.LIDAR_OFFSET_DEG)
    # One probe pass now, so the startup log says what is connected. The threads take these as a first hint
    # and re-probe by content whenever a device goes away, so an unplug/replug on a new name still works.
    if esp32.enabled and esp32.fixed_port is None:
        esp32.port_hint, fw = ports.find("esp32")
        if esp32.port_hint:
            esp32.fw = fw
            log.info("ESP32  found on %s (fw %s)", esp32.port_hint, fw)
        else:
            log.error("ESP32  NOT FOUND: drive and slope disabled. Ports seen: %s", ports.seen())
    if lidar.enabled and lidar.fixed_port is None:
        lidar.port_hint, info = ports.find("lidar")
        if lidar.port_hint:
            log.info("LIDAR  found on %s (model %s fw %s)", lidar.port_hint, info.model, info.firmware)
        else:
            log.error("LIDAR  NOT FOUND: width disabled. Ports seen: %s", ports.seen())
    esp32.start()
    lidar.start()

    cfg = dict(config.CONFIG)
    scout = Scout(esp32, lidar, Audit(cfg), cfg)
    log.info("serving http://%s:%d  ws://%s:%d/ws  (also localhost)", _ip(), config.PORT, _ip(), config.PORT)
    web.run_app(make_app(scout), host="0.0.0.0", port=config.PORT, print=None, access_log=None)
