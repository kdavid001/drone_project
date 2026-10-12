"""
Flight control + telemetry logging, combined into one script.

Why this exists: telemetry_probe.py alone could only ever send NEUTRAL, so
it could confirm the drone sends nothing back while disarmed -- it couldn't
test whether the FC only replies once armed/flying, because it has no way
to actually arm or fly. This script is keyboard_control.py's exact control
logic, unchanged, with telemetry_probe.py's receive-and-log logic bolted
onto the SAME socket (bound to local port 7060, matching the real app's own
r2.h class, which uses one DatagramSocket for both send and receive).

Confirmed so far: zero bytes received on port 7060 while disarmed, across
every orientation tested by hand. This run is to find out whether that
changes once the drone is actually armed and/or flying.

Controls are identical to keyboard_control.py:
  W/S = throttle   A/D = yaw   Arrows = pitch/roll
  SPACE = quick launch   N = neutral   Q = quit (lands first)

What's new on top of that:
  - Binds to local port 7060 (keyboard_control.py used an arbitrary ephemeral
    port before -- if the drone only replies to port 7060 specifically, the
    old script could never have seen a reply even if one was being sent).
  - A background thread listens continuously and prints a line whenever the
    received bytes change from the last packet seen, so real telemetry (if
    any appears once armed) is visible immediately without flooding the
    terminal.
  - Every single received packet (not just changes) is logged with a
    timestamp to telemetry_log_<timestamp>.txt in this folder, for review
    afterward even if something is missed live.
  - A heartbeat prints every 3s regardless, so "still zero" is just as
    visible as a change -- you don't have to guess whether the listener
    is even still running.

Suggested test order (safest first):
  1. Run this. With the drone sitting still, press W once (arms + spins
     rotors, per the known launch sequence) but do NOT press SPACE yet.
     Watch for a few seconds -- does anything arrive just from being armed
     with rotors spinning, before any actual lift?
  2. If still nothing, go ahead and fly normally (W then SPACE, as usual).
     Watch this same window throughout the flight.
  3. Land/quit as normal with Q. The full capture is saved either way --
     "confirmed nothing ever arrives, even mid-flight" is still a real,
     useful answer (it means this reply channel is dead weight in the
     firmware, and telemetry would have to come from somewhere else, e.g.
     decoding IMU data some other way, or just not being available at all).
"""
import socket
import sys
import termios
import tty
import threading
import time
import datetime
import os

DRONE_IP = "192.168.28.1"
DRONE_PORT = 7080
LOCAL_PORT = 7060  # matches the real app's own client port exactly

HEADER = bytes.fromhex("cc5a01830966")
FOOTER = bytes.fromhex("9974")
STEP = 60

LOG_DIR = os.path.dirname(os.path.abspath(__file__)) or "."
LOG_PATH = os.path.join(
    LOG_DIR, f"telemetry_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
)

# axis state: byte6 (roll), byte7 (pitch), byte8 (throttle), byte9 (yaw)
state = {"b6": 128, "b7": 128, "b8": 128, "b9": 128}
state_lock = threading.Lock()
running = True

received_count = 0
count_lock = threading.Lock()

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def build_packet(b6, b7, b8, b9):
    checksum = b6 ^ b7 ^ b8 ^ b9
    return HEADER + bytes([b6, b7, b8, b9, 0x00, checksum]) + FOOTER


def clamp(v):
    return max(0, min(255, v))


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def sender_loop():
    while running:
        with state_lock:
            pkt = build_packet(state["b6"], state["b7"], state["b8"], state["b9"])
        try:
            sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        except OSError:
            pass
        time.sleep(0.02)  # ~50Hz


def receiver_loop(log_file):
    global received_count
    last_seen = None
    while running:
        try:
            data, addr = sock.recvfrom(1024)
        except OSError:
            break
        with count_lock:
            received_count += 1
        hex_str = data.hex()
        log_file.write(f"[{now()}] RECV from {addr}  len={len(data)}  hex={hex_str}\n")
        log_file.flush()
        if data != last_seen:
            print(f"\n[{now()}] CHANGED  len={len(data):3d}  hex={hex_str}")
            last_seen = data


def heartbeat_loop():
    while running:
        time.sleep(3)
        if not running:
            break
        with count_lock:
            c = received_count
        print(f"[{now()}] heartbeat: {c} packets received so far")


def adjust(key_name, field, delta):
    with state_lock:
        state[field] = clamp(state[field] + delta)
        snapshot = dict(state)
    print(f"{key_name:>12} -> b6(roll)={snapshot['b6']:3d}  b7(pitch)={snapshot['b7']:3d}  "
          f"b8(throttle)={snapshot['b8']:3d}  b9(yaw)={snapshot['b9']:3d}")


def go_neutral(label="NEUTRAL"):
    with state_lock:
        state["b6"] = state["b7"] = state["b8"] = state["b9"] = 128
    print(f"{label:>12} -> all axes reset to 128 (center)")


def quick_launch():
    print("  [quick launch] zeroing roll/pitch/yaw...")
    with state_lock:
        state["b6"] = state["b7"] = state["b9"] = 128
    print("  [quick launch] throttle up (arm)...")
    with state_lock:
        state["b8"] = clamp(128 + STEP)
    time.sleep(1.0)
    print("  [quick launch] back to center...")
    with state_lock:
        state["b8"] = 128
    time.sleep(0.5)
    print("  [quick launch] throttle up again (liftoff)...")
    with state_lock:
        state["b8"] = clamp(128 + STEP)
    print("  [quick launch] holding — use W/S to adjust throttle further, N to settle down.")


def read_key():
    ch = sys.stdin.read(1)
    if ch == '\x1b':
        seq = sys.stdin.read(2)
        if seq == '[A': return 'UP'
        if seq == '[B': return 'DOWN'
        if seq == '[C': return 'RIGHT'
        if seq == '[D': return 'LEFT'
        return None
    return ch


def main():
    global running
    try:
        sock.bind(("0.0.0.0", LOCAL_PORT))
    except OSError as e:
        print(f"Could not bind to local port {LOCAL_PORT} ({e}).")
        print("Only one process can hold that port at a time -- make sure")
        print("telemetry_probe.py or another copy of this script isn't still running.")
        return

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    log_file = open(LOG_PATH, "w")

    sender_thread = threading.Thread(target=sender_loop, daemon=True)
    recv_thread = threading.Thread(target=receiver_loop, args=(log_file,), daemon=True)
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    sender_thread.start()
    recv_thread.start()
    hb_thread.start()

    print("Keyboard control + telemetry logging running.")
    print("W/S=throttle  A/D=yaw  Arrows=pitch/roll  SPACE=quick launch  N=neutral  Q=quit")
    print(f"Listening on local port {LOCAL_PORT}. Logging full capture to:\n  {LOG_PATH}")
    print("Watch for 'CHANGED' lines -- a heartbeat prints every 3s either way.\n")

    try:
        tty.setcbreak(fd)
        while True:
            key = read_key()
            if key is None:
                continue
            if key in ('w', 'W'):
                adjust("throttle+", "b8", STEP)
            elif key in ('s', 'S'):
                adjust("throttle-", "b8", -STEP)
            elif key in ('a', 'A'):
                adjust("yaw-", "b9", -STEP)
            elif key in ('d', 'D'):
                adjust("yaw+", "b9", STEP)
            elif key == 'UP':
                adjust("pitch+", "b7", STEP)
            elif key == 'DOWN':
                adjust("pitch-", "b7", -STEP)
            elif key == 'LEFT':
                adjust("roll-", "b6", -STEP)
            elif key == 'RIGHT':
                adjust("roll+", "b6", STEP)
            elif key == ' ':
                quick_launch()
            elif key in ('n', 'N'):
                go_neutral()
            elif key in ('q', 'Q'):
                go_neutral("LANDING")
                time.sleep(1.5)
                break
    finally:
        running = False
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        time.sleep(0.1)
        sock.close()
        log_file.close()
        print("\nStopped. Full capture saved at:")
        print(f"  {LOG_PATH}")


if __name__ == "__main__":
    main()
