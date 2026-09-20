"""Protocol v2 over HTTP and WebSocket (aiohttp), the 10 Hz loop, and the run buffer."""
import asyncio
from datetime import datetime, timezone
import json
import logging
import math
import socket
import time
from collections import deque

from aiohttp import WSMsgType, web

from . import config
from .audit import clearance
from .mapping import MERGE_MM

log = logging.getLogger("scout.server")

SHORT = {
    "forward": {"cmd": "drive", "v": 0.5, "w": 0}, "back": {"cmd": "drive", "v": -0.5, "w": 0},
    "left": {"cmd": "drive", "v": 0, "w": 0.5}, "right": {"cmd": "drive", "v": 0, "w": -0.5},
    "stop": {"cmd": "stop"}, "beep": {"cmd": "beep"}, "roam": {"cmd": "mode", "mode": "wall_follow"},
}
CONFIG_KEYS = ("width_limit_mm", "robot_width_mm", "wall_target_mm", "cruise")
MODES = ("idle", "teleop", "wall_follow")
LOOK_COOLDOWN_S = 4.0       # after naming one obstacle, do not stop for another this soon
WALL_TOL_MM = 250           # a pinch edge this close to a wall was made by the wall


def _clamp(x):
    return max(-1.0, min(1.0, float(x)))


def _ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Scout:
    """Everything the protocol exposes. Called from the aiohttp loop only; device threads just
    leave snapshots behind."""

    def __init__(self, motor, lidar, camera, pose, grid, audit, follower, cfg):
        self.motor, self.lidar, self.camera = motor, lidar, camera
        self.pose, self.grid, self.audit, self.follower = pose, grid, audit, follower
        self.cfg = cfg
        self.mode = "idle"
        self.run = {"active": False, "space": ""}
        self.seq = 0
        self.t0 = time.monotonic()
        self.run_started_t = 0
        self.run_started_at = _utc_now()   # wall clock for started_t, so an uploader can place the run in time
        self.buffer = deque()       # run log frames; telemetry at 2 Hz, one map, events never dropped
        self.pending = []           # event frames waiting for the next broadcast
        self.clients = set()
        self.tick = 0
        self.measuring = False
        self.led_off_at = None
        self._look_task = None
        self._looked_at = -1e9
        self._last_scan = []

    def t(self):
        return int((time.monotonic() - self.t0) * 1000)

    # ---- protocol ----
    def fw(self):
        return f"pi {config.VERSION} / motor {self.motor.fw or 'n/a'}"

    def status(self):
        return {
            "proto": 2, "fw": self.fw(), "mode": self.mode, "measuring": self.measuring,
            "run": dict(self.run),
            # "esp32" is the wire key from protocol v2 and the dashboard still reads it; the
            # board behind it is now a RedBoard. "motor" is the same flag under its real name.
            "devices": {"esp32": self.motor.connected, "motor": self.motor.connected,
                        "lidar": self.lidar.connected,
                        "camera": bool(self.camera and self.camera.connected)},
            "config": dict(self.cfg), "uptime_ms": self.t(), "ip": _ip(),
        }

    def command(self, c):
        if not isinstance(c, dict) or not isinstance(c.get("cmd"), str):
            return {"ok": False, "err": "no cmd"}
        k = c["cmd"]
        try:
            if k == "drive":
                if self.measuring:
                    return {"ok": True}                       # ignored while looking, motors stay stopped
                if not self.motor.connected:
                    return {"ok": False, "err": "motor board not connected"}
                self.mode = "teleop"                          # a human taking over cancels roaming
                self.follower.reset()
                self.motor.send(f"D {_clamp(c.get('v', 0)):.2f} {_clamp(c.get('w', 0)):.2f}")
            elif k == "stop":
                self.motor.send("S")
                self.mode = "idle"
                self.follower.reset()
            elif k == "mode":
                m = c.get("mode")
                if m not in MODES:
                    return {"ok": False, "err": f"unknown mode {m}"}
                if m == "wall_follow" and not self.lidar.connected:
                    return {"ok": False, "err": "lidar not connected"}
                if m != "wall_follow":
                    self.motor.send("S")
                self.follower.reset()
                self.mode = m
            elif k == "run":
                if c.get("action") == "start":
                    self.buffer.clear()
                    self.reset_map()
                    self.run = {"active": True, "space": str(c.get("space", ""))}
                    self.run_started_t = self.t()
                    self.run_started_at = _utc_now()
                    self.emit({"kind": "run_start"})
                elif c.get("action") == "stop":
                    self.emit({"kind": "run_stop"})
                    self.run = {"active": False, "space": self.run["space"]}
                else:
                    return {"ok": False, "err": f"unknown action {c.get('action')}"}
            elif k == "mark":
                ev = {"kind": "mark", "label": str(c.get("label", ""))}
                if self.pose.ok:
                    ev["x_mm"], ev["y_mm"] = round(self.pose.x), round(self.pose.y)
                self.emit(ev)
            elif k == "map":
                if c.get("action") != "clear":
                    return {"ok": False, "err": f"unknown action {c.get('action')}"}
                self.reset_map()
            elif k == "beep":
                self.motor.send("B 0")
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

    def reset_map(self):
        self.pose.clear()
        self.grid.clear()
        self.audit.reset()
        log.info("map and room frame cleared")

    def emit(self, ev):
        frame = {"type": "event", "t": self.t(), "seq": self.seq, **ev,
                 "space": self.run["space"] if self.run["active"] else ""}
        self.seq += 1
        self.buffer.append(frame)
        self.pending.append(frame)
        kind = ev["kind"]
        # Only verdicts light up. An obstacle or a ramp is an observation, not a judgement
        # (PROTOCOL.md section 6).
        if kind == "width_fail":
            self.motor.send("L 1 0"); self.motor.send("B 1"); self.led_off_at = time.monotonic() + 3
        elif kind == "width_pass":
            self.motor.send("L 0 1"); self.motor.send("B 0"); self.led_off_at = time.monotonic() + 3
        log.info("event %s", {k: v for k, v in frame.items() if k != "type"})

    # ---- the room frame ----
    def to_room(self, pt):
        """A robot-frame (x, y) to (x_mm, y_mm, kind) in the room frame, or None without a pose.
        `kind` says whether that point is part of a wall or something standing in the room."""
        if not (self.pose.ok and self.grid.ready()):
            return None
        a = math.radians(self.pose.heading)
        x = self.pose.x + pt[0] * math.cos(a) - pt[1] * math.sin(a)
        y = self.pose.y + pt[0] * math.sin(a) + pt[1] * math.cos(a)
        if self.grid.room:
            w, l = self.grid.room
            on_wall = min(abs(x), abs(x - w), abs(y), abs(y - l)) <= WALL_TOL_MM
        else:
            # Slam fits no rectangle, so "near a wall" cannot be a distance to one. Turn the
            # question around: a pinch edge sitting on something already reported as an obstacle
            # was made by that obstacle, and anything else is the building. Only the wording of
            # the verdict rides on this -- "between two walls" or "between a wall and a chair".
            near = self.grid.nearest_reported(x, y)
            on_wall = not (near and math.hypot(near[0] - x, near[1] - y) <= MERGE_MM)
        return round(x), round(y), on_wall

    def telem(self, scan, width_mm):
        p = self.pose
        d = self.motor.drive
        return {
            "type": "telem", "t": self.t(), "mode": self.mode, "measuring": self.measuring,
            "lidar": bool(scan),
            "scan": scan if scan else [],
            "gaps": list(self.lidar.gaps) if scan else [],
            "pose": bool(p.ok), "x_mm": round(p.x) if p.ok else 0, "y_mm": round(p.y) if p.ok else 0,
            "heading_deg": round(p.heading, 1) if p.ok else 0,
            "room": {"w_mm": p.room[0], "l_mm": p.room[1]} if (p.ok and p.room) else None,
            "clearance_mm": width_mm,
            "bump": [0, 0], "stuck": False,
            "v": round(d.v, 2) if d else 0, "w": round(d.w, 2) if d else 0,
        }

    def map_frame(self):
        return self.grid.frame(self.t(), self.pose.ok)

    def run_log(self):
        header = {"type": "run", "space": self.run["space"], "fw": self.fw(),
                  "started_t": self.run_started_t, "started_at": self.run_started_at, "config": dict(self.cfg)}
        return "\n".join(json.dumps(x) for x in [header, *self.buffer]) + "\n"

    # ---- looking at what is in front ----
    async def look_and_name(self, loop):
        """Stop, photograph what is ahead, name it, emit one obstacle or ramp event.

        The capture and the CLIP forward pass are seconds of blocking work, so they go to a thread;
        the 10 Hz loop keeps serving telemetry throughout with `measuring` true.
        """
        self.measuring = True
        self.motor.send("S")
        try:
            label, conf, photo, is_ramp = await loop.run_in_executor(None, self.camera.look) \
                if self.camera else ("unknown", 0.0, "", False)
            got = self.grid.claim_ahead(self.pose.x, self.pose.y, self.pose.heading,
                                        int(self.cfg["wall_target_mm"]) * 4) if self.pose.ok else None
            if got:
                x, y, _ = got
                self.emit({"kind": "ramp" if is_ramp else "obstacle", "label": label,
                           "confidence": conf, "photo": photo, "x_mm": x, "y_mm": y})
        finally:
            self.measuring = False
            self._looked_at = time.monotonic()
            self._look_task = None

    # ---- the loop ----
    async def telemetry_loop(self):
        loop = asyncio.get_running_loop()
        period = 1.0 / config.TELEM_HZ
        map_every = max(1, config.TELEM_HZ // config.MAP_HZ)
        while True:
            now = time.monotonic()
            scan = list(self.lidar.scan) if self.lidar.connected else []
            # Whether Scout is moving decides if a failed fit may hold the last pose. With no ESP32
            # there is nothing reporting wheel speed, so assume it is moving: holding a pose that
            # is only valid while stationary, for a lidar someone is carrying across the room, puts
            # returns metres from where they were taken and writes them into the map as fact.
            d = self.motor.drive
            moving = True if d is None else (abs(d.v) > 0.01 or abs(d.w) > 0.01)

            if scan:
                self.pose.update(scan, moving=moving)
                if self.pose.take_relock():
                    # the old map was in a frame that is gone
                    self.grid.clear(self.pose.room, config.MAP_SPAN_MM)
                    self.audit.reset()
                if self.pose.ok:
                    if not self.grid.ready():
                        self.grid.clear(self.pose.room, config.MAP_SPAN_MM)
                    self.grid.integrate(scan, self.pose.x, self.pose.y, self.pose.heading)
            else:
                self.pose.ok = False

            width_mm, left, right = clearance(scan)
            frame = self.telem(scan, width_mm)

            # width verdicts
            place = None
            if left and right:
                a, b = self.to_room(left), self.to_room(right)
                if a and b:
                    place = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2,
                             "wall-wall" if a[2] and b[2] else "wall-obstacle")
            for ev in self.audit.step(now, width_mm, place, self.lidar.gaps if scan else ()):
                self.emit(ev)

            # wall following, and stopping to name whatever blocks the way
            if self.mode == "wall_follow" and not self.measuring:
                v, w = self.follower.step(now, scan)
                if self.follower.blocked and self._look_task is None \
                        and now - self._looked_at > LOOK_COOLDOWN_S:
                    self._look_task = asyncio.create_task(self.look_and_name(loop))
                elif self.motor.connected:
                    self.motor.send(f"D {_clamp(v):.2f} {_clamp(w):.2f}")

            if self.led_off_at and now >= self.led_off_at:
                self.motor.send("L 0 0")
                self.led_off_at = None

            self.tick += 1
            out = [*self.pending, frame]
            self.pending.clear()
            if self.tick % map_every == 0 and self.grid.ready():
                mf = self.map_frame()
                out.append(mf)
                self.buffer.append(mf)          # so a saved run replays with the map filling in
            if self.tick % (config.TELEM_HZ // 2) == 0:      # 2 Hz into the run log
                self.buffer.append(frame)
            if out or self.tick % (config.TELEM_HZ // 2) == 0:
                self._trim_buffer()
            for f in out:
                await self.broadcast(json.dumps(f))
            await asyncio.sleep(period)

    def _trim_buffer(self):
        """Events are never dropped. Telemetry goes first, and only the newest map is kept: a grid
        is a hundred times a telemetry frame, so keeping every one would crowd out the run."""
        maps = [i for i, f in enumerate(self.buffer) if f["type"] == "map"]
        for i in reversed(maps[:-1]):
            del self.buffer[i]
        n_telem = sum(1 for f in self.buffer if f["type"] == "telem")
        while n_telem > config.RUN_BUFFER_TELEM:
            for i, f in enumerate(self.buffer):
                if f["type"] == "telem":
                    del self.buffer[i]
                    n_telem -= 1
                    break
            else:
                break

    async def broadcast(self, text):
        for ws in list(self.clients):
            try:
                await ws.send_str(text)
            except Exception:
                self.clients.discard(ws)


# ---- aiohttp app ----
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET, POST",
        "Access-Control-Allow-Headers": "Content-Type"}


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
        return web.json_response(scout.command(c) if c else {"ok": False, "err": "unknown c"},
                                 status=200 if c else 400)

    async def get_map(req):
        if not scout.grid.ready():
            return web.json_response({"ok": False, "err": "no map yet"}, status=404)
        return web.json_response(scout.map_frame())

    async def get_photo(req):
        jpeg = scout.camera.photo(req.match_info["pid"]) if scout.camera else None
        if not jpeg:
            return web.json_response({"ok": False, "err": "no photo"}, status=404)
        return web.Response(body=jpeg, content_type="image/jpeg")

    async def runs_latest(req):
        return web.Response(text=scout.run_log(), content_type="application/x-ndjson")

    async def ws(req):
        sock = web.WebSocketResponse(heartbeat=10, max_msg_size=0)
        await sock.prepare(req)
        scout.clients.add(sock)
        log.info("ws client connected (%d)", len(scout.clients))
        if scout.grid.ready():
            await sock.send_str(json.dumps(scout.map_frame()))   # never show a blank map
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
                    web.get("/map", get_map), web.get("/photo/{pid}", get_photo),
                    web.get("/runs/latest", runs_latest), web.get("/ws", ws)])
    app.on_startup.append(start_loop)
    return app
