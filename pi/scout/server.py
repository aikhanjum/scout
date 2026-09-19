"""Protocol v1 over HTTP and WebSocket (aiohttp), the 10 Hz telemetry loop, and the run buffer."""
import asyncio
import json
import logging
import socket
import time
from collections import deque

from aiohttp import WSMsgType, web

from . import config

log = logging.getLogger("scout.server")

SHORT = {
    "forward": {"cmd": "drive", "v": 0.5, "w": 0}, "back": {"cmd": "drive", "v": -0.5, "w": 0},
    "left": {"cmd": "drive", "v": 0, "w": 0.5}, "right": {"cmd": "drive", "v": 0, "w": -0.5},
    "stop": {"cmd": "stop"}, "beep": {"cmd": "beep"},
}
CONFIG_KEYS = ("slope_limit_deg", "width_limit_mm", "scale", "width_offset_mm")


def _clamp(x):
    return max(-1.0, min(1.0, float(x)))


def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


class Scout:
    """Everything the protocol exposes. Called from the aiohttp loop only; device threads just leave snapshots."""

    def __init__(self, esp32, lidar, audit, cfg):
        self.esp32, self.lidar, self.audit, self.cfg = esp32, lidar, audit, cfg
        self.mode = "idle"
        self.run = {"active": False, "space": ""}
        self.seq = 0
        self.t0 = time.monotonic()
        self.run_started_t = 0
        self.buffer = deque()       # run log frames; telemetry decimated to 2 Hz, events never dropped
        self.pending = []           # event frames waiting for the next broadcast
        self.clients = set()
        self.tick = 0
        self.led_off_at = None
        self.tilted = False

    def t(self):
        return int((time.monotonic() - self.t0) * 1000)

    # ---- protocol ----
    def fw(self):
        return f"pi {config.VERSION} / esp32 {self.esp32.fw or 'n/a'}"

    def status(self):
        imu = self.esp32.imu
        return {
            "proto": 1, "fw": self.fw(), "mode": self.mode, "measuring": self.audit.measuring,
            "run": dict(self.run),
            "devices": {"esp32": self.esp32.connected, "imu": bool(imu and imu.ok and self.esp32.connected),
                        "lidar": self.lidar.connected},
            "config": dict(self.cfg), "uptime_ms": self.t(), "heap": 0, "ip": _ip(),
        }

    def command(self, c):
        if not isinstance(c, dict) or not isinstance(c.get("cmd"), str):
            return {"ok": False, "err": "no cmd"}
        k = c["cmd"]
        try:
            if k == "drive":
                if self.audit.measuring:
                    return {"ok": True}                       # ignored while measuring, motors stay stopped
                if not self.esp32.connected:
                    return {"ok": False, "err": "esp32 not connected"}
                self.mode = "teleop"
                self.esp32.send(f"D {_clamp(c.get('v', 0)):.2f} {_clamp(c.get('w', 0)):.2f}")
            elif k == "stop":
                self.esp32.send("S")
                self.mode = "idle"
            elif k == "mode":
                if c.get("mode") not in ("idle", "teleop"):
                    return {"ok": False, "err": f"unknown mode {c.get('mode')}"}
                if c["mode"] == "idle":
                    self.esp32.send("S")
                self.mode = c["mode"]
            elif k == "run":
                if c.get("action") == "start":
                    self.buffer.clear()
                    self.run = {"active": True, "space": str(c.get("space", ""))}
                    self.run_started_t = self.t()
                    self.emit({"kind": "run_start"})
                elif c.get("action") == "stop":
                    self.emit({"kind": "run_stop"})
                    self.run = {"active": False, "space": self.run["space"]}
                else:
                    return {"ok": False, "err": f"unknown action {c.get('action')}"}
            elif k == "mark":
                self.emit({"kind": "mark", "label": str(c.get("label", ""))})
            elif k == "zero":
                self.esp32.send("Z")
            elif k == "beep":
                self.esp32.send("B 0")
            elif k == "config":
                for key in CONFIG_KEYS:
                    if isinstance(c.get(key), (int, float)) and not isinstance(c.get(key), bool):
                        self.cfg[key] = float(c[key])
                log.info("config -> %s", self.cfg)
            else:
                return {"ok": False, "err": f"unknown cmd {k}"}
        except (TypeError, ValueError) as e:
            return {"ok": False, "err": f"bad {k}: {e}"}
        return {"ok": True}

    def emit(self, ev):
        frame = {"type": "event", "t": self.t(), "seq": self.seq, **ev, "space": self.run["space"] if self.run["active"] else ""}
        self.seq += 1
        self.buffer.append(frame)
        self.pending.append(frame)
        kind = ev["kind"]
        if kind.endswith("_fail"):
            self.esp32.send("L 1 0"); self.esp32.send("B 1"); self.led_off_at = time.monotonic() + 3
        elif kind.endswith("_pass"):
            self.esp32.send("L 0 1"); self.esp32.send("B 0"); self.led_off_at = time.monotonic() + 3
        log.info("event %s", {k: v for k, v in frame.items() if k not in ("type",)})

    def telem(self):
        imu = self.esp32.imu if self.esp32.connected else None
        imu_ok = bool(imu and imu.ok)
        sweep = self.lidar.sweep if self.lidar.connected else ()
        sides = dict(sweep)
        right, left = sides.get(-90, 0), sides.get(90, 0)
        width = int(round(right + left + float(self.cfg["width_offset_mm"]))) if 0 < right < 2000 and 0 < left < 2000 else 0
        return {
            "type": "telem", "t": self.t(), "mode": self.mode, "measuring": self.audit.measuring,
            "imu": imu_ok, "lidar": bool(sweep),
            "pitch_deg": round(imu.pitch, 2) if imu_ok else 0, "roll_deg": round(imu.roll, 2) if imu_ok else 0,
            "yaw_deg": round(imu.yaw, 1) if imu_ok else 0,
            "sweep": [{"a": a, "mm": mm} for a, mm in sweep], "width_mm": width,
            "bump": [0, 0], "stuck": False,
            "v": round(imu.v, 2) if imu else 0, "w": round(imu.w, 2) if imu else 0,
        }

    def run_log(self):
        header = {"type": "run", "space": self.run["space"], "fw": self.fw(), "started_t": self.run_started_t, "config": dict(self.cfg)}
        return "\n".join(json.dumps(x) for x in [header, *self.buffer]) + "\n"

    # ---- the loop ----
    async def telemetry_loop(self):
        period = 1.0 / config.TELEM_HZ
        while True:
            frame = self.telem()
            now = time.monotonic()
            pitch = frame["pitch_deg"] if frame["imu"] else None
            events, stop = self.audit.step(now, pitch, frame["width_mm"])
            if stop:
                self.esp32.send("S")
            if frame["imu"] and (abs(frame["pitch_deg"]) > 20 or abs(frame["roll_deg"]) > 15):
                if not self.tilted:
                    self.tilted = True
                    self.esp32.send("S")
                    self.mode = "idle"
                    self.emit({"kind": "tilt_cutoff", "value": max(abs(frame["pitch_deg"]), abs(frame["roll_deg"])), "unit": "deg"})
            else:
                self.tilted = False
            for ev in events:
                self.emit(ev)
            if self.led_off_at and now >= self.led_off_at:
                self.esp32.send("L 0 0")
                self.led_off_at = None
            self.tick += 1
            if self.tick % (config.TELEM_HZ // 2) == 0:      # 2 Hz into the run log
                self.buffer.append(frame)
                n_telem = sum(1 for f in self.buffer if f["type"] == "telem")
                if n_telem > config.RUN_BUFFER_TELEM:
                    for i, f in enumerate(self.buffer):
                        if f["type"] == "telem":
                            del self.buffer[i]
                            break
            for f in [*self.pending, frame]:
                await self.broadcast(json.dumps(f))
            self.pending.clear()
            await asyncio.sleep(period)

    async def broadcast(self, text):
        for ws in list(self.clients):
            try:
                await ws.send_str(text)
            except Exception:
                self.clients.discard(ws)


# ---- aiohttp app ----
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST", "Access-Control-Allow-Headers": "Content-Type"}


@web.middleware
async def cors(request, handler):
    if request.method == "OPTIONS":
        return web.Response(status=204, headers=CORS)
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        resp = e
    for k, v in CORS.items():
        resp.headers[k] = v
    return resp


def make_app(scout):
    async def status(req):
        return web.json_response(scout.status())

    async def cmd_post(req):
        try:
            c = json.loads(await req.text())
        except ValueError:
            return web.json_response({"ok": False, "err": "bad json"}, status=400)
        return web.json_response(scout.command(c))

    async def cmd_get(req):
        c = SHORT.get(req.query.get("c", ""))
        return web.json_response(scout.command(c) if c else {"ok": False, "err": "unknown c"}, status=200 if c else 400)

    async def runs_latest(req):
        return web.Response(text=scout.run_log(), content_type="application/x-ndjson")

    async def ws(req):
        sock = web.WebSocketResponse(heartbeat=10)
        await sock.prepare(req)
        scout.clients.add(sock)
        log.info("ws client connected (%d)", len(scout.clients))
        try:
            async for msg in sock:
                if msg.type == WSMsgType.TEXT:
                    try:
                        scout.command(json.loads(msg.data))
                    except ValueError:
                        log.info("ws: bad json")
        finally:
            scout.clients.discard(sock)
            log.info("ws client left (%d)", len(scout.clients))
        return sock

    async def start_loop(app):
        app["loop_task"] = asyncio.create_task(scout.telemetry_loop())

    app = web.Application(middlewares=[cors])
    app.add_routes([web.get("/status", status), web.post("/cmd", cmd_post), web.get("/cmd", cmd_get),
                    web.get("/runs/latest", runs_latest), web.get("/ws", ws)])
    app.on_startup.append(start_loop)
    return app
