# LATER

Everything cut from the plan, with the reason. New ideas go here, not in code. Nothing here is P0 or P1.

## Cut on 2026-09-19 when the plan shrank to 4 hours

- **Hub server** (Fastify, SQLite, relay, inbox folder): no P0 needed it. The dashboard talks to Scout directly; JSON files and localStorage hold the data.
- **ElevenLabs voice**: browser `speechSynthesis` speaks the same template strings with no key and no network.
- **Gemini "Ask Scout" and the AI report**: beat 4 of the demo, first thing in the original cut order.
- **Public read-only viewer and QR code**: needs a hosted build; P2 wow.
- **EYES iPhone app** (ARKit poses streamed to the Hub, auto pins): 5-hour timebox, no Xcode on the dev Mac.
- **Wall follow and bounce modes**: teleop is the live demo. Wall follow becomes easier with the lidar (S3 in SPEC.md if time).
- **Pin placement UI** (click an event, click the mesh): five pins typed into `spaces.json` beats a raycast UI at hour 3.
- **Clearance gate overlay in the 3D viewer**, **run path trail**, **photo attachments on pins**, **gamepad**: P2.
- **Radar fan on the HUD**: cut for time. The lidar now sends all five angles, so it is cheap to add back.
- **Sonar turret, servo, echo divider**: replaced by the lidar.

## Cut on 2026-09-19 when the Pi became the brain

- **Fallback access point `SCOUT-AP`**: the ESP32 had it for free; on the Pi it needs hostapd. If the hotspot dies, use another phone's hotspot with the same name and password.
- **Phone remote page `GET /`**: S2 in SPEC.md if time. Would be a static page served by the Pi.
- **Bumper reflex, stuck detection, `bump` and `stuck` telemetry**: keys reserved, always `[0,0]` / `false`.
- **Front-distance obstacle stop**: the lidar sees ahead now; a "stop if anything is under 250 mm ahead while driving forward" rule is 10 lines in `pi/scout/server.py` and worth doing before the phone rides on Scout.
- **Wheel encoders, mapping, SLAM, floor plans from the lidar**: no odometry by design. The A1 could do a floor-plan slice per space one day.

## Cut on 2026-09-19 after the lidar went in

- **A widening gate ahead in the fake Scout**: its scan world has one corridor width per frame, so it can only draw a gate that is narrower than the lane Scout is in. A gate that opens out is not drawn, and the fake does not claim a gap its own points do not show. Fine for the table course, which only narrows.
- **Tuning the gap audit on the real course**: `GAP_AHEAD_DEG` and `GAP_MAX_RANGE_MM` in `pi/scout/audit.py` were set by eye. On a desk at scale 1.0 the gap source fires a few width events a minute, because a 230 mm gap between two objects really is under 860 mm. On the clean lane it should be quiet. Verify it there, then tighten if it chatters.
- **`step` gaps in the audit**: a range step is the corner of an object, not an opening, so it is drawn and never audited. If a door frame ever reads as `step` rather than `see_through`, revisit.
- **Express scan for the other models**: only the standard and capsule formats are decoded. Ultra capsules (A3 boost, S series) fall back to the standard stream, which works but at half the rate.
- **Resolving an `unverified` gap by moving**: driving past changes the incidence angle, so a shiny or grazing surface starts returning while a real opening stays open. Two rotations from different positions would settle most of them. Needs position, which Scout does not have.
- **Lidar view extras**: no scrubbing, no history, no measuring two points. The range selector and the mm toggle are all of it.

## Ideas parked

- Auto-pin: match event timestamps to the phone's ARKit pose (needs EYES).
- Threshold height (13 mm rule) from the lidar tilted down, or from the IMU bump signature.
- Battery voltage on an ADC pin, shown on the HUD.
- Record the run video from the phone and attach it to the space.
- Replay speed control and scrubbing.
