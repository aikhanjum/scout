"""Score the room fit against both fixtures at once.

    cd pi && .venv/bin/python tools/check_fit.py

Two environments that fail in opposite directions, so any change to pose.py must be checked
against both:

  * tools/fixtures/table-pen-scan.json -- one real RPLIDAR scan taken inside an arena of four
    tables on their sides, in an open lab. Returns escape through every gap, so 43% of them land
    outside the arena. Its true size is NOT known: measure the pen with a tape.
  * data/runs/room-scan.ndjson -- the simulated closed room, 4210 x 5090 mm, carrying the true
    pose in every frame. A ramp against the far wall leaves it about eleven returns, which is the
    case that defeats peak-finding.
"""
import os, sys, json, math, time
sys.path.insert(0, '.')
from scout.pose import Pose, fit_room, scan_points
SP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

sc = json.load(open(os.path.join(SP, 'table-pen-scan.json')))['scan']
f = fit_room(scan_points(sc))
if f:
    cx, cy, th, a, b, on = f
    p = Pose(); ok = p.update(sc, moving=True)
    print(f"REAL table pen : {2*a:7.0f} x {2*b:7.0f} mm  {on:4.0%} on walls  locks={ok}")
else:
    print("REAL table pen : NO FIT")

frames = [json.loads(l) for l in open('../data/runs/room-scan.ndjson') if l.strip()]
tel = [x for x in frames if x['type'] == 'telem']
p = Pose(); ref = None; errs = []
t0 = time.time()
for x in tel:
    mv = abs(x['v']) > 0.01 or abs(x['w']) > 0.01
    if not p.update(x['scan'], moving=mv):
        continue
    if ref is None:
        dth = math.radians(x['heading_deg'] - p.heading); c, s = math.cos(dth), math.sin(dth)
        ref = (c, s, x['x_mm'] - (p.x*c - p.y*s), x['y_mm'] - (p.x*s + p.y*c))
    c, s, ox, oy = ref
    errs.append(math.hypot(ox + (p.x*c - p.y*s) - x['x_mm'], oy + (p.x*s + p.y*c) - x['y_mm']))
dt = (time.time() - t0) / len(tel) * 1000
if errs:
    errs.sort(); q = lambda A, fr: A[int(len(A)*fr)-1]
    print(f"SIM course     : {p.room}  truth (4210, 5090)   poses {100*len(errs)/len(tel):3.0f}%  "
          f"median {q(errs,.5):5.1f}  p95 {q(errs,.95):5.1f}  max {errs[-1]:6.1f} mm  {dt:.0f} ms/scan")
    print(f"                 target: ~88% poses, median <5, max <20")
else:
    print(f"SIM course     : NO POSES AT ALL  room={p.room}")
