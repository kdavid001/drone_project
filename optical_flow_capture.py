"""Standalone diagnostic: dump the drone's "optical flow" UDP packets raw.

Purpose: test whether these packets already carry a usable height/distance-
to-ground reading. Cheap drones that pair a downward camera with optical
flow almost always also carry a sonar/ToF height sensor feeding the same
data path -- pure optical flow is scale-ambiguous (can't tell height from
apparent motion without height or velocity already known from somewhere
else), so a paired height sensor is the normal way these are made to work
at all. The decompiled app logs these packets' first 9 bytes but the class
that actually parses them (byte offsets -> real values) wasn't present in
our extracted decompile -- so this is the "get real evidence" approach that
also cracked the video XOR bug, applied here.

How to use it:
  1. Run this script alone (NOT at the same time as main.py -- both bind
     the same UDP port 7070, only one can receive at a time).
  2. Power on the drone, connect to its WiFi, let the script run.
  3. By hand (no flight needed), hold the drone level and move it straight
     up/down over KNOWN distances -- e.g. 10cm steps against a tape measure
     or marked wall -- pausing ~1-2 seconds at each height so the packets at
     that height are easy to pick out in the log by timestamp.
  4. Compare the hex dumps across heights. If one or two bytes (or a 16-bit
     value spanning two bytes) change monotonically with height, that's
     very likely the real sensor reading -- note the byte offset and
     whether it looks like raw distance in mm/cm or needs a scale factor.
  5. If nothing tracks height at all, this channel is probably pure optical
     flow (velocity/motion only, no absolute height), and the answer is a
     vision-only approach instead (see DEVNOTES.md for the fallback plan).

UPDATE: first run (hand-held, forward camera active, drone sitting idle)
produced ZERO optical-flow packets at all -- script connects fine (prints
the "Listening..." line), so the UDP channel itself is fine, the drone is
just never sending this packet type under those conditions. Two live
hypotheses this version tests:
  (a) Optical flow is tied to the DOWNWARD camera specifically (it's
      physically what you'd expect -- you need to see the ground to compute
      ground-relative flow), and it only gets computed/sent once that
      camera is the active one. This script now sends the camera-switch
      command (SWITCH_CAMERA_CMD) ~1s after starting video, same as pressing
      'c' in main.py, so it's actively on the downward camera while
      capturing.
  (b) The sensor/firmware only runs this computation while actually
      armed and flying (common for position-hold hardware -- no reason to
      burn power on it while sitting on a desk). If this version (with the
      camera switched) STILL shows zero packets, the next test is to run
      this script and actually arm + take off with the drone (e.g. run this
      alongside a flight, or add a quick launch sequence) rather than just
      hand-holding it -- same "flown, not just handled" standard the dead
      telemetry channel was confirmed against.

Packets are identified by the same header the decompiled app checks for:
data[0] == 0x5A and data[1] == 0xCC (reverse of the usual 0xCC 0x5A video/
control magic).
"""

import socket
import time

DRONE_IP = "192.168.28.1"
CONTROL_PORT = 7080
VIDEO_PORT = 7070
MAGIC = bytes.fromhex("cc5a01820236b7")        # video-start command
SWITCH_CAMERA_CMD = bytes.fromhex("cc5a01820238b9")  # toggle forward<->downward

LOG_FILE = "optical_flow_capture.log"


def main():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", VIDEO_PORT))
    sock.settimeout(1.0)
    sock.sendto(MAGIC, (DRONE_IP, CONTROL_PORT))

    print("Sent video-start. Switching to downward camera in 1s "
          "(hypothesis: optical flow only runs on that camera)...")
    time.sleep(1.0)
    sock.sendto(SWITCH_CAMERA_CMD, (DRONE_IP, CONTROL_PORT))
    print("Sent camera switch. If your drone has a screen/light that shows "
          "which camera is active, confirm it actually switched.")

    start = time.monotonic()
    count = 0
    other_pkts = 0

    print(f"Listening on UDP :{VIDEO_PORT} for optical-flow packets "
          f"(0x5A 0xCC header). Logging to {LOG_FILE}. Ctrl+C to stop.")

    with open(LOG_FILE, "a") as log:
        log.write(f"\n--- capture started {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        try:
            while True:
                try:
                    data, addr = sock.recvfrom(65536)
                except socket.timeout:
                    continue

                t = time.monotonic() - start

                if len(data) >= 2 and data[0] == 0x5A and data[1] == 0xCC:
                    count += 1
                    line = f"[{t:8.3f}s] #{count:4d} len={len(data):3d} hex={data.hex()}"
                    print(line)
                    log.write(line + "\n")
                    log.flush()
                else:
                    other_pkts += 1  # video fragments etc. -- not logged, just counted

        except KeyboardInterrupt:
            pass

    print(f"\nStopped. Captured {count} optical-flow packets, "
          f"saw {other_pkts} other (video) packets.")
    print(f"Full log in {LOG_FILE} -- compare hex at different known heights.")
    sock.close()


if __name__ == "__main__":
    main()
