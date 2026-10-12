# Drone Project — Dev Notes

Running log of progress, findings, bugs, and approach changes. Newest entries
first. This is the working log for the code in this folder; see the
"Drone Project + Geometry-Aware RL Research — Status"
---

## 2026-10-11 — Video corruption root-caused and fixed; camera switch found; CV direction set

**Video corruption, fully root-caused.** The noisy/corrupted camera images
mentioned in the 2026-10-09 entry below were NOT RF/radio-layer corruption as
assumed at the time. Root cause, confirmed directly from the decompiled app's
real video-reassembly method (`s2.l.a()`, the method our drone's video
callback actually dispatches to): the drone XORs exactly one byte of every
video UDP packet's payload with `0xFF` before sending it — the app's own log
line calls this step "video transmission decryption" ("图传解密"). We were
never undoing it, so every frame we ever received had one deliberately-wrong
byte in it, at a position that shifts per-packet
(`offset = ((frame_id*frag_idx+10)*6666) % (len-8) + 6`). That single flipped
byte, depending on where it landed, explained every corruption pattern seen:
hard JPEG decode failures (Huffman/marker byte hit), and the sheared/banded
images (DC-coefficient byte hit, desyncing that MCU row). `main.py` now
undoes this (`undo_video_xor()`) before reassembly. **User-confirmed: output
is clean now.**

Two smaller real bugs were also fixed along the way, found before the XOR
root cause: (1) a stale frame_id reuse bug — a frame_id is a single byte
reused constantly, and if a frame's final packet was lost its dict entry
never got cleared, risking silent merging of a new frame's fragments into an
old abandoned one once the ID wrapped around; fixed with a wall-clock
staleness timeout. (2) the app's separate "optical flow" packet type
(header `0x5A 0xCC`, the reverse of the usual video/control magic) was being
fed into frame reassembly as garbage; now filtered out before reassembly.
Forensic per-frame dump tooling built to catch real evidence of the
corruption (raw bytes + fragment arrival manifests) did its job and has
since been removed from `main.py` now that the real cause is fixed and
confirmed — it was diagnostic scaffolding, not something to keep.

**Image orientation fixed.** The existing code did a horizontal-only mirror
flip. Flying the drone physically inverted "fixed" the image, which means
the sensor is mounted rotated 180 degrees, not just mirrored. Changed to a
true 180° rotate (`cv2.rotate(bgr, cv2.ROTATE_180)`) so the drone can fly
right-side up.

**Second (downward-facing) camera — confirmed and wired up.** Traced the
app's "change lens" button (`btChangeJT`, present across all of this app's
product activities) to `r2.i.e()` — literally named `sendSW_CMD` in source.
It sends a fixed 7-byte UDP command (`cc 5a 01 82 02 38 b9`) over the exact
same channel/socket as the existing video start command. It's a **toggle**,
not a two-value select — there's no argument, so sending it once switches to
the downward camera and sending it again switches back. Both cameras share
one video stream (port 7070) — no simultaneous dual feed. `main.py` now has
a `send_camera_switch()` helper wired to the `c` key.

**Decided operating plan:** switch to the downward camera during takeoff and
landing (for ground-distance awareness), switch back to the forward camera
for the rest of the flight (for obstacle avoidance / general FPV state).
This confirms the project cannot have a single unified always-on camera
state input — it's a planned, deliberate camera-switching schedule tied to
flight phase, not a limitation to hide from the RL design.

**Next CV questions, open:**
1. *Height/distance-to-ground from the downward camera.* Pure monocular
   vision has no metric scale without a reference, so vision-only height
   estimation will be rough. There is a real, separate lead worth chasing
   first: the app's "optical flow" UDP packets (`0x5A 0xCC` header, seen on
   the same port) are logged but never decoded past the first 9 bytes in
   the decompiled source we have — the class that actually parses them
   wasn't present in the extracted decompile. Cheap drones that pair
   optical flow with a downward camera almost always also carry a sonar/ToF
   height sensor in the same data path, specifically because optical flow
   alone is scale-ambiguous (can't tell height from velocity without one of
   them known already). Worth testing empirically: capture the raw bytes of
   these packets while moving the drone up/down by hand over known
   distances and look for a byte that tracks height directly — same
   evidence-first approach that found the XOR bug. `optical_flow_capture.py`
   written for this (see below).
2. *Front-camera CV for obstacle avoidance.* Decided direction: object
   detection + segmentation to identify what's ahead and treat it as
   something to avoid, not full scene understanding/mapping. Implementation
   not started yet.

**New file: `optical_flow_capture.py`** — standalone script (binds port
7070, sends the same video-start MAGIC, since the optical-flow UI toggle
only matters once the video view is open) that dumps every `0x5A 0xCC`
packet's full raw hex with a timestamp, for the height-sensor hypothesis
test in (1) above. Not meant to run at the same time as `main.py` (same UDP
port).

---

## 2026-10-09 (cont'd) — Telemetry conclusively absent; back to camera-only state

**Context for why this was even being chased:** the original plan was to use
the onboard camera feed as the drone's state input for the RL policy at
deployment. The video pipeline works, but the images are noisy/poor quality
(cheap sensor, WiFi video path has its own corruption issues already dealt
with separately) — noisy enough that camera-only state looked shaky as the
sole feedback source. That's what motivated checking whether the drone's
sensors (gyro/accel/attitude — confirmed to exist via the app's data-model
classes, see above) could be pulled over WiFi as a second, cleaner state
source alongside or instead of vision.

**Result: they can't. Confirmed conclusively, not just "untested."** Built
`flight_with_telemetry.py` (merges `keyboard_control.py`'s real flight logic
with `telemetry_probe.py`'s listener, both on the same socket bound to local
port 7060 — matching the real app's own `r2.h` exactly). Ran a full session
through it: armed (throttle up), the full quick-launch gesture, actual
liftoff, throttle changes, and landing. **Zero bytes received at any point**
— disarmed, armed-not-flying, and actually airborne. The receive thread
(`r2.g` case 0) genuinely never gets anything from the firmware, under any
tested flight state. This closes the telemetry question — not deferred,
closed: there is no sensor data coming over this UDP channel, period.

**Implication:** camera/vision is not a nice-to-have alongside telemetry —
it's the only possible source of real-world state feedback this drone has.
The noisy-image problem that originally motivated this detour is now back
to being the main problem to solve directly (better preprocessing/filtering,
or a CV approach robust to the noise), rather than something telemetry could
have been a fallback for.

**Also means:** mapping the physical drone's response into the sim can't be
grounded against real telemetry (there isn't any). Will have to be done the
cruder way — measured physical params (mass, arm length) into the URDF, plus
empirical response testing (known command in, observed/timed reaction out,
by eye or video) to fit sim dynamics, rather than a clean sim-vs-telemetry
comparison.

## 2026-10-09 — Correction: actual UDP path, telemetry data found, probe built

**Priorities shifted:** the original Oct 1 target has passed with no formal
new deadline — decided to prioritize actually understanding the problem
properly over shipping fast. Treat remaining work as "as fast as reasonable"
rather than racing a date.

**Correction to the 2026-09-16 decompile notes below:** the earlier claim
that this drone uses the `protocolparse`/`ProtocolAnalysisUtil` native path
was wrong. Traced `MainActivityDecode`'s actual send/receive code (it never
imports `ProtocolAnalysisUtil` at all) and found the real sender/receiver:
plain-Java classes `r2.h` (owns the socket, binds **local port 7060**, sends
to `192.168.28.1:7080`) and `r2.g` (two background threads — one send loop,
one receive loop). No native library involved for this drone at all. The
`protocolparse`/`trinityprotocolparse`/`native_socket` native libs are for
the app's *other* product lines only.

**Big finding: the drone very likely already sends back real telemetry, and
nobody has ever looked at it.** `r2.g`'s receive thread opens a 20-byte
buffer, calls `socket.receive()`, logs the length + source port of whatever
arrives — and then discards it. Never parsed. But the app ships real
telemetry data-model classes (used by its other product lines, same shared
codebase):
- `PlaneSensor`: `Gyr`, `Acc`, `Mag`, `baro`, `BaroTemp`, `ImuTemp`, `Roll`,
  `Pitch`, `Yaw`, `Thor`, `Motor1`-`Motor4`
- `PlaneTestInfo`: raw `gyro_x/y/z`, `acc_x/y/z`, `mag_x/y/z`, battery
  voltage, GPS, barometric altitude, computed roll/pitch/yaw
- `PlaneInforXL`: `AttitudeRoll/Pitch/Yaw`, `Armed`, `FlyMode`, `BatVal`,
  `Takeoff`/`AutoLand`/`LandFinish`, calibration flags

So the flight controller clearly has a full IMU (gyro+accel+mag) and
barometer and computes its own attitude — the models exist in the app, they
were just never wired up for this drone's own screen. The 20-byte reply
buffer size is a good hint about payload size to expect.

**Why this matters for sim-to-real:** if that reply packet can be decoded,
it gives real onboard attitude/gyro/accel/battery state — both for
comparing sim vs. real during dynamics characterization, and potentially as
the actual observation feed at deployment (much stronger than vision-only
perception). This is the answer to "we need to map the real drone's
behavior into the sim" — rather than a separate measurement campaign, decode
what the drone is already telling us.

**Built `telemetry_probe.py`:** binds local port 7060 (matching the real
app exactly), sends `NEUTRAL` continuously, listens for replies, prints a
line whenever the received bytes change, heartbeats every 3s so a true
"nothing ever arrives" result is just as visible as a changing one, and logs
every packet to a timestamped file for later review. Not yet run — next
step is to try it props-off first (tilt the drone by hand, watch for
changes) before trying it while actually flying.

**RL algorithm decision:** going with PPO over SAC for the eventual policy,
specifically for this project. Reasons: PPO (not SAC) is the dominant,
best-proven approach in the actual sim-to-real literature for dynamic
underactuated systems (legged robots, quadrotors) — its clipped updates
give more predictable, bounded policy changes, which pairs well with heavy
domain randomization. SAC's sample-efficiency advantage barely matters here
since training happens in cheap simulation, not on hardware. And there's
already a `ppo_pytorch` implementation running (see below) plus prior
curriculum-PPO experience from the Spot FYP work — zero new
algorithm-learning curve. Action-space note for later: add an action-rate /
smoothness penalty in the reward regardless of algorithm — that's the real
lever against a jerky policy destabilizing the already-twitchy real flight
controller, more so than the PPO-vs-SAC choice itself.

**Spinning Up environment setup (OpenAI Spinning Up, cloned locally)** —
done in an earlier working session, logged here for the first time:
- Untangled an embedded git submodule issue: removed stray macOS `Icon\r`
  files, untracked the `spinningup` submodule, deleted its internal `.git`,
  committed it as a normal directory in the personal GitHub repo.
- Abandoned an initial pip-based `RLenv` virtualenv (built for the wrong
  platform) and created a fresh Conda env (`conda_rl_env`, Python 3.8) to
  handle the dependency set better.
- Hit a real blocker: legacy TensorFlow 1.x (Spinning Up's original
  backend) crashes on Apple Silicon with an AVX instruction error — TF 1.x
  simply doesn't support this CPU architecture.
- Workaround: uninstalled TensorFlow entirely, used `unittest.mock` in
  Spinning Up's `__init__.py` to silently stub out its hardcoded TF imports
  (rather than rewriting the 2018-era codebase), and got the modern
  **PyTorch** algorithm variant (`ppo_pytorch`) running successfully. This is
  the implementation to build on going forward — don't reintroduce the TF
  variants, they're dead on this machine.

## 2026-09-16 (cont'd) — Live flight test of corrected keyboard control

**Result: mostly clean.** Corrected roll/pitch/throttle/yaw mapping (see
below) flown for real and confirmed **both axis identity AND sign** —
roll/pitch/yaw all moved the drone the expected direction on first try. No
further mapping corrections needed.

**Finding — no spring-return on roll/pitch/yaw (expected given the script's
design, worth remembering):** pressing `A`/`D` (yaw) or an arrow key
(pitch/roll) makes the drone hold that motion continuously — it does not
self-center back to neutral when you let go of the key, only when you press
the *opposite* key or hit `N`. Root cause: `keyboard_control.py` simulates
the app's **discrete tap-to-nudge buttons** (±60 per tap, holds until
changed), not a real spring-loaded analog stick — a keyboard has no
"key released, spring back to center" concept the way a physical stick does,
and our background sender just keeps re-transmitting whatever value was last
set.

concern: if the RL agent's action space works this way, it's a bad
fit (hard to train — turns a continuous control problem into an
awkward "pick a direction and commit" bang-bang problem).

**Resolution / why this isn't actually a problem for RL:** the *protocol
itself* is fully analog — byte6/7/8/9 each accept any value 0-255, not just
"neutral or ±60 from last tap." The ±60 increment behavior is purely a
property of this keyboard-testing script (chosen so a human typing on a
keyboard has a sensible discrete way to test each axis), and of the app's
own tap-button UI. **The RL policy does not need to go through this
increment-and-hold logic at all** — it can just output an absolute value
per axis per control tick directly (e.g. a continuous action in [-1, 1]
mapped straight to [0, 255]), which is a normal continuous action space and
trains fine. This whole caveat is specific to *manual keyboard testing*, not
to the eventual RL interface.

*Optional later improvement, not required:* could add an auto-return-to-neutral
timer on roll/pitch/yaw (but not throttle) if smoother manual flying is
wanted — not pursued for now since it doesn't block anything.

**Empirically found launch sequence (works reliably):**
1. Right after connecting to the drone's WiFi: press `W` once. Rotors spin
   up but the drone does **not** take off (this is the "arm" stage).
   Pressing `W` again at this point does **not** cause takeoff.
2. Press `SPACE` once. The drone takes off cleanly. **Order matters — `W`
   before `SPACE`.**
3. If you press `SPACE` without pressing `W` first, you need to press
   `SPACE` **twice** to get the same clean liftoff (the first press seems to
   only get it to the "armed" state, same as a lone `W`; the second actually
   lifts it).

**Separate, only partly understood finding:** if the drone has been
connected for a while (idle, rotors off, *not* immediately after connecting)
rather than fresh off a connect, a single `W` tap doesn't spin the rotors —
it took roughly 3 taps before the rotors responded. Throttle value jumps in
steps of 60 (128 → 188 → 248 → ...), so there's no way to land exactly on
120; observed was that rotors respond once the value crosses somewhere in
the 120–180 band, but the previous "one-tap-and-it's-armed" scenario is
suspected (not confirmed) to have landed differently — possibly a
state-dependent arming threshold in the drone's own flight controller
(e.g. it may debounce or require the signal to persist/repeat before
treating it as a real command if it's been idle a while). **Not fully
explained — logged for now, revisit if it causes real problems** (e.g. if
the RL agent's very first action after connecting behaves inconsistently
depending on how long the drone's been sitting idle, this is the reason to
look here first).

**Files touched:** `keyboard_control.py` (axis mapping fixed from an earlier
guess to the confirmed roll/pitch swap; `quick_launch()` fixed to force
roll/pitch/yaw to neutral before touching throttle, so a leftover axis value
from earlier testing can't sneak into a launch — this was also caught live,
see previous entry below).

**Status: flight-control mapping is now considered finalized and confirmed** —
axis identity, sign, checksum, and launch sequence are all verified against
real hardware. Next step (Korede's, not a code task): autonomous control via
RL — he's doing background reading first before starting implementation.

---

## 2026-09-16 — Full jadx decompile, confirmed axis mapping, quick_launch bug

- asked to review the app's own code himself. Claude's cloud sandbox
  can't reach GitHub/PyPI to fetch a decompiler, but Mac isn't
  behind that restriction — `brew install jadx` + `jadx -d ... <xapk>` there
  produced a full, real, readable Java source tree at
  `app_decompiled/sources/com/lg/`.
- Found the exact roll/pitch/throttle/yaw byte mapping directly in
  `MainActivityDecode.java`, via two independent confirmations:
  - The **voice-command handler** maps paired English/Chinese strings to
    byte writes: `FORWARD`/`向前进` → `byte7 += 60`, `BACKWARD`/`向后退` →
    `byte7 -= 60`, `RIGHT`/`右飞` → `byte6 += 60`, `LEFT`/`左飞` →
    `byte6 -= 60`.
  - The joystick-drag handlers carry their own Chinese debug labels:
    `右杆y` ("right stick Y") on the byte7 handler, `右杆x` ("right stick X")
    on byte6, `发送控制油门` ("send control throttle") on byte8, `转向`
    ("steering/yaw") on byte9.
  - **Corrected mapping: byte6 = roll, byte7 = pitch** (this is the
    *reverse* of the original Mode-2-convention guess used in the first
    version of `keyboard_control.py`). byte8 = throttle and byte9 = yaw were
    already right.
- The app's own checksum code (`MainActivityDecode.x()`) matches our
  reverse-engineered formula byte-for-byte: `byte11 = byte6^byte7^byte8^byte9`.
- Confirmed the app supports 3 different product lines via 3 different
  native libs (`native_socket`/`JNICMD`, `trinityprotocolparse`/
  `TrinityProtocolUtil`, `protocolparse`/`ProtocolAnalysisUtil`) — our drone
  uses the `protocolparse`/`ProtocolAnalysisUtil`/`MainActivityDecode` path,
  confirming the earlier native-string analysis was reading the right code.
- `protocol_en.txt`/`protocol_ch.txt` assets bundled in the app turned out
  to just be the privacy policy — dead end, don't re-investigate.
- **Bug caught live by Korede:** pressing `SPACE` (quick launch) caused the
  drone to tilt back on liftoff. Root cause: `quick_launch()` only ever
  touched the throttle byte, so a leftover pitch/roll value from testing
  arrow keys earlier in the same session got sent along with the throttle
  spike. **Fixed:** `quick_launch()` now zeroes roll/pitch/yaw to neutral
  before touching throttle.
- Extracted app contents (raw DEX + native libs, pre-jadx) packaged as
  `app_source_for_review.zip` and sent to Korede for his own review, in
  addition to the full jadx tree.

## 2026-09-16 — Pivot from packet-capture to keyboard control tool

- rejected the planned WiFi-packet-capture approach for nailing down
  yaw/pitch/roll ("a waste of time and tokens because we will have to read
  through the bytes which grows as the recording grows"). Requested instead:
  build directly on what the decompiled app's code already told us, bound to
  keyboard keys, to test live over WiFi.
- Built `keyboard_control.py`: 14-byte packet
  `cc5a01830966 | b6 b7 b8 b9 | 00 | checksum | 9974`, checksum =
  `b6^b7^b8^b9`, center = 128, ±60/tap (matches the app's own increment).
  Background thread sends state continuously at ~50Hz — this also handles
  priming automatically (neutral is being sent from the moment the script
  starts). `W`/`S`=throttle, `A`/`D`=yaw, arrows=pitch/roll, `SPACE`=quick
  launch (automates the confirmed two-stage arm/lift gesture), `N`=neutral,
  `Q`=quit (lands first).
- At this point pitch/roll labels were still a guess (Mode-2 convention) —
  corrected the same day once the jadx decompile came back (see above).

## 2026-09-16 — App decompiled (first pass, hand-rolled tools)
 
- downloaded the Android build (`.xapk` via APKPure, since he's on
  iPhone). Claude's cloud sandbox couldn't install jadx/apktool/androguard
  (egress allowlist blocks github.com/pypi.org for tool downloads), so wrote
  a small handwritten DEX bytecode parser/disassembler instead
  (`dex_probe.py` + driver scripts) to read specific methods by hand.
- Findings from this pass: two independent virtual joysticks
  (`mLeftRocker`/`mRightRocker`), ±60-per-tap nudge behavior, center=128
  confirmed in-app, and a "these are telemetry, not commands" theory for the
  native `Armed`/`Takeoff`/`AutoLand`/`LandFinish`/`Attitude*` strings (zero
  outgoing Java references found for any of them) — this theory still holds
  after the full decompile.
- Also found: camera module removal kills the drone's WiFi entirely (proves
  the physical remote uses a separate, non-WiFi RF link) and that the app's
  own on-screen controls genuinely fly the drone as long as the physical
  remote isn't connected at the same time (they were fighting for control).
- Takeoff imbalance scare (one rotor behaving oddly) diagnosed and resolved
  as a stale level-calibration reference, fixed by a power cycle on a flat
  surface. **Standing practice: power-cycle on a flat surface before each
  flight session**, especially after handling.

## 2026-09-10/11 — Flight control confirmed working over WiFi

- Found [hsbofficial1/fpv-drone-hack-lab](https://github.com/hsbofficial1/fpv-drone-hack-lab)
  (same OEM drone family — its arm payload matches our own camera
  registration command byte-for-byte), which supplied 29 candidate UDP
  payloads.
- Discovered a two-stage arm/throttle gesture (spin-no-lift, then a second
  throttle-up after returning to center actually lifts off) matching how the
  physical remote itself behaves — standard toy-drone safety pattern.
  Replicated over WiFi: **real liftoff achieved.**
- Decoded the movement-packet byte structure from the 29-candidate list:
  `cc 5a 01 83 09 66 | B6 B7 | B8 B9 | 00 | checksum | 99 74`, checksum =
  `byte6^byte7^byte8^byte9`, verified against all 29 candidates (later
  reconfirmed byte-for-byte against the app's real source, see above).
- This superseded the original nRF24/RF-hardware fallback plan — no longer
  needed. Also ruled out sniffing the physical remote's own RF link with a
  Windows laptop/Wireshark or a Flipper Zero — it's on a proprietary 2.4GHz
  link, not WiFi/802.11 and not in Flipper's Sub-GHz range.

## 2026-09-07 — Camera protocol cracked, video pipeline done

- Reverse-engineered via WiFi packet capture (macOS Wireless Diagnostics →
  pcap → tcpdump/scapy). Drone gateway `192.168.28.1`; registration command
  is a 7-byte UDP packet `cc 5a 01 82 02 36 b7` from local port 7070 to
  drone port 7080; video replies stream back on 7070, ~1472-byte packets,
  4-byte custom header + intact JPEG bytes.
- Two real corruption modes found and fixed in `main.py`: libjpeg
  partial-recovery masking failures (fixed via PIL's strict `img.load()`
  gate) and silent mid-scan Huffman desync with no restart markers (fixed
  with a `looks_corrupted()` heuristic on flat horizontal strips). Root
  cause of corruption is signal strength/distance, not a hardware ceiling.
- **Video pipeline considered done.**

---

## Current status / open items

- Flight control (throttle/roll/pitch/yaw, checksum, launch sequence):
  **confirmed and finalized**, both from decompiled source and real flight
  testing.
- Minor unresolved curiosity, not blocking: the "3 taps needed to spin
  rotors if not fresh-connected" behavior above — revisit only if it causes
  real problems later.
- **Active: decode the drone's telemetry reply packet.** Run
  `telemetry_probe.py`, props off first, tilt by hand, see what (if
  anything) changes. This is the current priority before building the sim.
- RL algorithm: **PPO**, via the already-working `ppo_pytorch` from Spinning
  Up (TensorFlow variants are dead on this machine, don't reintroduce them).
- Next after telemetry decode: build the sim, mapping real drone behavior
  (ideally including decoded telemetry, not just guessed constants) into it.
- Camera/video pipeline: done, not revisited since 2026-09-07.
- No hard deadline currently — prioritizing understanding over speed.
