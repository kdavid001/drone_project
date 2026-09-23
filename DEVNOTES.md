# Drone Project — Dev Notes

Running log of progress, findings, bugs, and approach changes. Newest entries
first. This is the working log for the code in this folder; see the
"Drone Project + Geometry-Aware RL Research — Status" doc in the
"Korede's Life" claude.ai project for the fuller narrative/context version.

---

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
- Next: autonomous control via RL
- Camera/video pipeline: done, not revisited since 2026-09-07.
