# Devpost text, corrected 2026-09-20 05:20

Paste over the current entry. Changes from the old text: RedBoard not ESP32, 600 ms not 500, no "Scout
Measured seal" (there is a dated run report, no seal), no "live floor map" claim (position is inferred
and lives on its own tab), and the Tiger Data section now has the measured numbers.

Built with: aiohttp, arduino, c++, cloudflare-tunnel, postgresql, python, pyserial, raspberry-pi-4, react,
rplidar-a2m8, sparkfun-redboard, tiger-data, timescaledb, typescript, vite, web-speech-api, websockets

---

## This robot gets stuck so you don't have to.

**Scout is a lidar rover that goes in first and measures whether a wheelchair can get through.**

Ontario's building code is specific. A doorway on an accessible route needs **860 mm** of clear opening.

Most places that call themselves accessible have never held a tape measure to it. A wheelchair user finds out at the door. And a room that meets the code on paper can still be blocked by chairs and backpacks.

_totally not referring to E7 6th floor._

"Too narrow between a wall and an obstacle. 51 centimeters. A wheelchair needs 86."

That is what Scout says, out loud, the moment it finds a barrier.

**Measures** the clear width in front of it, 360 degrees, ten times a second, and checks it against 860 mm.

**Shows you** exactly what it sees. A Bloomberg-style terminal on the laptop and, on any phone that scans the QR code, Scout's eyes: one bar per lidar beam, colour is distance, nothing inferred.

**Remembers** every single beam. All of it streams into Tiger Data while it drives, so a room can be asked about hours later with plain SQL.

**Honest about** what it could not verify. Gaps it cannot see through are labelled unverified and never counted. Its position is a guess, so the map is on its own tab, labelled inferred, and no verdict uses it.

### What it does

Scout drives through a space with a 360 degree lidar. In every rotation it finds the two nearest solid edges to its left and right inside a 90 degree window ahead, measures the perpendicular distance between them, and compares it to 860 mm. A pass is spoken and logged. A fail is spoken with the number.

On a real doorway Scout read **878 mm** where the tape measure said **880** (docs/ACCURACY.md in the repo).

You run it from a browser. The terminal has fourteen live items: Scout's eyes, the radar, the clearance verdict, the rule it is checking, link and lidar health, every gap in the current scan with its evidence tag, the Tiger Data stream, the events feed, the session, the room over time, a drive pad whose keys light up, and the QR code for phones. Navigation is Bloomberg style, a mnemonic and a number per screen and a command line with GO.

| Scout checks | Scout does not check |
| --- | --- |
| Clear width between anything solid, at lidar height | Slopes and ramps (no IMU) |
| Gaps made by furniture and bags, which change daily | Door thresholds and steps |
| The same room again, hours later | Door weight, handle type, reach heights |

A person still judges the right hand column. Scout tells them where to look first.

### How we built it

```
Browser (laptop, or any phone through a Cloudflare tunnel)
   | WebSocket: 10 telemetry frames a second up, drive commands down
   v
Raspberry Pi 4 ....... lidar reader, gap finder, width audit, protocol server
   | USB serial, one text line per message
   v
SparkFun RedBoard .... four motors as two sides, stops itself after 600 ms of silence
   |
   +--> laptop tailer --> Tiger Data, 3,600 rows a second while it drives
```

We froze the message format in the first hour and wrote a fake Scout that speaks it. All four of us built against the fake before a robot existed, and the real robot slotted in without changing the dashboard.

### How we used Tiger Data

Scout's data is a time series by nature. One distance per degree, ten rotations a second:

$$ 360 \times 10 = 3600 \text{ rows per second} $$

We put every one of those rows into Tiger Cloud, live, not after the run.

**Standard SQL on big data.** A tailer on the laptop listens to the robot's WebSocket and every second COPYs the last ten rotations into a hypertable called `scan`, one row per beam, using TimescaleDB's direct-compress COPY so the rows land already in the columnstore. The table holds **3.78 million** beam rows as we write this, and `SELECT count(*) FROM scan` over all of it comes back in **127 ms** on wifi (about 600 ms when the laptop is on a phone hotspot). The terminal has a read-only SQL console with presets so a judge can type their own query at the table while the robot is still driving.

**Unified stack.** One Postgres holds the relational table (`runs`: space, firmware, start time), the verdicts (`events`), the ten-times-a-second robot state (`telem`) and the raw beams (`scan`). No second store, no export step. Simulated runs are tagged in every table and hidden by default, so a fake robot can never inflate a real number.

**Instant dashboards from continuous aggregates.** Five continuous aggregates, all real-time: `telem_1s` and `scan_1s` feed the "last second" line on the terminal, and `telem_15m`, `events_15m`, `scan_15m` feed "room over time", the minimum clearance and the fail count per space per quarter hour. The dashboard never touches the raw rows for those panels.

**90%+ compression on the free tier.** The recorded runs, 2,089,080 rows and about 117 MB raw, take **4.16 MB** on disk after a full columnstore repack, 28x smaller, 96% saved. While streaming live the batches are smaller and the ratio sits around 21x, still 95% saved. The terminal prints the on-disk number as it climbs.

Everything is on a 0.5 CPU, 2 GB Tiger Cloud service, the free one.

### Challenges we ran into

#### The lidar said it was healthy and sent nothing

It answered every status query, accepted the scan command, and then streamed zero bytes. We had planned for an RPLIDAR A1. Ours was an A2M8, whose motor only spins after an explicit command that the A1 does not have.

#### An opening and a blind spot look identical

A beam that goes through a doorway and hits nothing reports the same as a beam that hits glass or matte black. Our first gap finder treated every blind arc as an opening. Now every gap carries its evidence:

| Tag | Meaning | Can it fail a room? |
| --- | --- | --- |
| `see_through` | A later return was seen through the gap, so it is open | Yes |
| `step` | The two edges are neighbouring samples, like a corner | No |
| `unverified` | Nothing came back at all | Never |

#### Scout failed a doorway because it was sitting crooked

Our first real doorway read 850 mm, 10 mm under the limit, because Scout was 30 degrees off square. Square to the frame it reads 878 mm against a tape measure's 880. The audit now takes the perpendicular distance to each edge, so a crooked approach cannot fail a legal door.

#### The map was confidently wrong

Scout has no wheel encoders and no IMU. Matching each scan to the last works on a simulated room and drifted metres on every real walk we recorded. At 3 am we stopped pretending. Position got its own tab, labelled inferred, and every number that matters was rebuilt to need no position at all. The lidar view, the verdict and the Tiger stream are all in the robot's own frame.

#### The operator was a wall

Pushing the sensor on a chair, the person behind it showed up as an object 22 cm away in about one scan out of four, right on the edge of the zone Scout measures. The rear cone is masked now.

### What we learned

Reading a sensor took 10 minutes. Knowing when the sensor was lying took 10 hours.

The most useful thing Scout can say is "unverified". A tool that always gives an answer is easy to build and impossible to trust.

One real rotation beat a day of simulated ones. Our fake lidar never produced the bug where a single object 180 mm away swallowed 35 wall segments. The first live scan did it immediately.

Storing the raw beams instead of the verdicts is what let us fix the gap finder at 4 am and re-judge every stored scan with the new code.

### What's next

Wheel encoders, so position stops being a guess. Slope, thresholds and door weight. Signing each run on the robot so nobody can edit a number afterwards.

Then the bigger idea. A map where every building starts grey, meaning nobody has measured it. A place turns green or red only after a Scout has been through, and the colour carries a date, because a room changes every time someone moves a chair. First, though, we put Scout in front of wheelchair users, because a building code is only a stand-in for a person.

## Next time a building says it's _accessible_, ask who measured.
