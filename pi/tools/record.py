#!/usr/bin/env python3
"""Record the full-rate WebSocket stream to data/runs/<space>-<utc time>.ndjson, protocol v2.1 format.

    .venv/bin/python tools/record.py --space "E5 corridor" [--url ws://localhost:8080/ws]

Sends run start with that space so Scout labels its events, writes every frame as it arrives
(flushed, so nothing is lost if the process is killed), and on Ctrl-C / SIGTERM sends run stop,
waits a moment for the run_stop event, and prints where the file is.
"""
import argparse
import asyncio
import json
import os
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parents[2]


async def record(url, space, out_dir):
    http = url.replace("ws://", "http://").replace("wss://", "https://").rsplit("/ws", 1)[0]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    async with aiohttp.ClientSession() as s:
        fw, cfg = "unknown", None
        try:
            async with s.get(f"{http}/status", timeout=aiohttp.ClientTimeout(total=3)) as r:
                st = await r.json()
                fw, cfg = st.get("fw", fw), st.get("config")
        except Exception:
            pass
        async with s.ws_connect(url) as ws:
            started_at = datetime.now(timezone.utc)
            slug = re.sub(r"\W+", "-", space).strip("-").lower() or "run"
            path = out_dir / f"{slug}-{started_at:%Y-%m-%d-%H-%M-%S}.ndjson"
            await ws.send_str(json.dumps({"cmd": "run", "action": "start", "space": space}))
            print(f"recording {space!r} -> {path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}", flush=True)
            print("stop with Ctrl-C or: kill -INT <pid>", flush=True)
            frames = events = 0
            first_t = None
            fh = None
            stopping_at = None
            while True:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=0.5)
                except asyncio.TimeoutError:
                    msg = None
                if stop.is_set() and stopping_at is None:
                    stopping_at = loop.time()
                    await ws.send_str(json.dumps({"cmd": "run", "action": "stop"}))
                if msg is not None:
                    if msg.type != aiohttp.WSMsgType.TEXT:
                        break
                    f = json.loads(msg.data)
                    if "t" not in f:
                        continue
                    if fh is None:
                        first_t = f["t"]
                        header = {"type": "run", "space": space, "fw": fw, "started_t": first_t,
                                  "started_at": started_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"), "config": cfg}
                        fh = open(path, "w")
                        fh.write(json.dumps(header) + "\n")
                    fh.write(msg.data.strip() + "\n")
                    fh.flush()
                    frames += 1
                    if f.get("type") == "event":
                        events += 1
                        if stopping_at is not None and f.get("kind") == "run_stop":
                            break
                if stopping_at is not None and loop.time() - stopping_at > 1.5:
                    break
            if fh:
                fh.close()
                last_t = f.get("t", first_t)
                print(f"saved {frames} frames ({events} events), {(last_t - first_t) / 1000:.1f} s -> {path}", flush=True)
            else:
                print("no frames arrived, nothing saved", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True)
    ap.add_argument("--url", default="ws://localhost:8080/ws")
    ap.add_argument("--out", default=str(ROOT / "data" / "runs"))
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    asyncio.run(record(a.url, a.space, out))


if __name__ == "__main__":
    main()
