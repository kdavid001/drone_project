"""
Live keyboard control for the drone. Axis mapping is CONFIRMED (not guessed)
from the app's actual decompiled Java (jadx output), specifically the voice
command handler in MainActivityDecode, which maps paired English/Chinese
command strings to byte writes:
  FORWARD / 向前进 (forward)  -> byte7 += 60
  BACKWARD / 向后退 (backward) -> byte7 -= 60
  RIGHT / 右飞 (fly right)    -> byte6 += 60
  LEFT / 左飞 (fly left)      -> byte6 -= 60
and the joystick-drag handlers, whose own Chinese debug labels confirm the
same thing directly ("右杆y" = right-stick Y = byte7 = pitch, "右杆x" =
right-stick X = byte6 = roll; left-stick handlers are labeled "发送控制油门"
= "send control throttle" for byte8, and "转向" = "steering/yaw" for byte9).

  - packet = HEADER + [byte6 byte7 byte8 byte9] + 0x00 + checksum + FOOTER
  - checksum (byte11) = byte6 ^ byte7 ^ byte8 ^ byte9   (matches the app's own
    checksum computation in MainActivityDecode.x() exactly, byte for byte)
  - neutral/center for every axis byte = 128 (0x80)
  - the app's own buttons/voice-commands nudge one byte at a time by +/-60,
    clamped to 0-255
  - two independent virtual sticks: left = throttle(byte8)/yaw(byte9),
    right = roll(byte6)/pitch(byte7)

Runs a background thread that continuously sends the CURRENT axis state at
50Hz (this is what keeps the drone "holding" a value, and also means priming
happens automatically from the moment the script starts, since neutral is
being sent immediately).

Controls:
  W / S   -> throttle up / down      (byte8)
  A / D   -> yaw left / right        (byte9)
  Up/Down -> pitch forward / back    (byte7, confirmed)
  Left/Right -> roll left / right    (byte6, confirmed)
  SPACE   -> quick launch shortcut (replicates the confirmed W,S,W arm+lift gesture)
  N       -> snap all axes back to neutral immediately (safe fallback / "let it settle")
  Q       -> quit (goes neutral first, then stops and exits cleanly)

Safety: test with propellers OFF first to confirm which key moves which motor
expectation before flying for real. Power-cycle the drone on a flat surface
before starting, per the calibration lesson from earlier. The +/- direction
for yaw (byte9) and the exact sign for roll/pitch are confirmed as to WHICH
byte is which axis, but double-check the direction (e.g. does Right arrow
really roll right, not left) the first time you fly, since the app's UI
could apply its own sign flip that isn't visible from the byte writes alone.
"""
import socket
import sys
import termios
import tty
import threading
import time

DRONE_IP = "192.168.28.1"
DRONE_PORT = 7080

HEADER = bytes.fromhex("cc5a01830966")   # bytes 0-5, constant
FOOTER = bytes.fromhex("9974")           # bytes 12-13, constant
STEP = 60                                 # matches the app's own per-tap increment

# axis state: byte6 (roll), byte7 (pitch), byte8 (throttle), byte9 (yaw)
state = {"b6": 128, "b7": 128, "b8": 128, "b9": 128}
state_lock = threading.Lock()
running = True

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def build_packet(b6, b7, b8, b9):
    checksum = b6 ^ b7 ^ b8 ^ b9
    return HEADER + bytes([b6, b7, b8, b9, 0x00, checksum]) + FOOTER

def clamp(v):
    return max(0, min(255, v))

def sender_loop():
    while running:
        with state_lock:
            pkt = build_packet(state["b6"], state["b7"], state["b8"], state["b9"])
        sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        time.sleep(0.02)  # ~50Hz

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
    # Replicates the confirmed working gesture: throttle up, back to center, up again.
    # Forces roll/pitch/yaw to neutral first so a leftover value from earlier
    # key tests (e.g. arrow keys) can't sneak into the launch and tilt the
    # drone — this is a pure throttle-only maneuver.
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
    """Read one keypress (handles arrow-key escape sequences) without needing Enter."""
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
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)

    sender_thread = threading.Thread(target=sender_loop, daemon=True)
    sender_thread.start()

    print("Keyboard control running. W/S=throttle  A/D=yaw  Arrows=pitch/roll  SPACE=quick launch  N=neutral  Q=quit")
    print("Sending neutral continuously right now (priming). Ready when you are.")
    print("Note: you do NOT need to hold a key down — one tap moves that axis by 60 and")
    print("the background sender keeps transmitting it continuously until you tap again.")
    print("Tap repeatedly to keep climbing/turning further.\n")

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
        print("\nStopped.")

if __name__ == "__main__":
    main()
