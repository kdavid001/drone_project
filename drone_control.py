"""Direct continuous control interface for the drone -- for the RL policy,
NOT for keyboard testing.

Why this exists: `keyboard_control.py` works by nudging an axis +/-60 per
keypress and holding that value until the opposite key is pressed (no
spring-return-to-center). That's a property of making a physical keyboard
usable for a human -- it is NOT a limitation of the drone's actual protocol.
The real UDP packet just carries four raw bytes (roll/pitch/throttle/yaw,
0-255 each, center=128) and the drone acts on whatever value it's currently
being sent. There's no reason to route the RL policy's output through the
"tap to nudge, hold until told otherwise" abstraction at all -- that would
be solving a problem that only exists for human fingers on a keyboard.

This module skips that layer entirely: the policy's action each timestep
goes straight into `set_axes()` (raw 0-255) or `set_normalized()` ([-1, 1],
which matches what a tanh-squashed continuous-action SB3 policy outputs
directly, with zero manual rescaling needed in your own code). A background
thread keeps re-sending the current axis state at ~50Hz (same rate
keyboard_control.py uses) so the drone keeps receiving commands between
your env's step() calls, same as it did under keyboard control.

Packet format, checksum, and the empirically-confirmed arm/launch gesture
are unchanged from keyboard_control.py -- this is the same protocol, just a
different (and for RL, more correct) way of driving it.
"""

import socket
import threading
import time

DRONE_IP = "192.168.28.1"
DRONE_PORT = 7080

HEADER = bytes.fromhex("cc5a01830966")   # bytes 0-5, constant
FOOTER = bytes.fromhex("9974")           # bytes 12-13, constant
NEUTRAL = 128
SEND_RATE_HZ = 50


def clamp(v):
    return max(0, min(255, int(round(v))))


def build_packet(roll, pitch, throttle, yaw):
    """roll/pitch/throttle/yaw are raw bytes, 0-255, center=128."""
    b6, b7, b8, b9 = clamp(roll), clamp(pitch), clamp(throttle), clamp(yaw)
    checksum = b6 ^ b7 ^ b8 ^ b9
    return HEADER + bytes([b6, b7, b8, b9, 0x00, checksum]) + FOOTER


class DroneLink:
    """Thread-safe continuous control link. One instance per flight session.

    Typical RL usage, inside your env's step():
        action = policy(obs)                    # e.g. np.array in [-1, 1], shape (4,)
        link.set_normalized(*action)             # roll, pitch, throttle, yaw
        time.sleep(YOUR_CONTROL_PERIOD)          # if you're pacing steps yourself
        obs = get_next_observation()

    No nudge/hold/tap logic anywhere in this path -- every call sets the
    actual axis value the drone will fly at, immediately.
    """

    def __init__(self, ip=DRONE_IP, port=DRONE_PORT, rate_hz=SEND_RATE_HZ):
        self.ip = ip
        self.port = port
        self.period = 1.0 / rate_hz
        self._state = {"roll": NEUTRAL, "pitch": NEUTRAL, "throttle": NEUTRAL, "yaw": NEUTRAL}
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._running = False
        self._thread = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._sender_loop, daemon=True)
        self._thread.start()

    def stop(self):
        if not self._running:
            return
        self.neutral()
        time.sleep(0.1)  # let a couple of neutral packets actually go out
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._sock.close()

    def _sender_loop(self):
        while self._running:
            with self._lock:
                pkt = build_packet(**self._state)
            self._sock.sendto(pkt, (self.ip, self.port))
            time.sleep(self.period)

    def set_axes(self, roll=None, pitch=None, throttle=None, yaw=None):
        """Raw bytes, 0-255, center=128. None = leave that axis unchanged."""
        with self._lock:
            if roll is not None:
                self._state["roll"] = clamp(roll)
            if pitch is not None:
                self._state["pitch"] = clamp(pitch)
            if throttle is not None:
                self._state["throttle"] = clamp(throttle)
            if yaw is not None:
                self._state["yaw"] = clamp(yaw)

    def set_normalized(self, roll=None, pitch=None, throttle=None, yaw=None):
        """Same as set_axes but takes values in [-1, 1] (clamped), centered
        at 0 -- matches a tanh-squashed continuous SB3 action head directly,
        no rescaling needed in your policy/env code."""
        def to_byte(v):
            return None if v is None else NEUTRAL + v * 127
        self.set_axes(to_byte(roll), to_byte(pitch), to_byte(throttle), to_byte(yaw))

    def neutral(self):
        self.set_axes(NEUTRAL, NEUTRAL, NEUTRAL, NEUTRAL)

    def arm_and_launch(self):
        """The empirically-confirmed working gesture from keyboard_control.py:
        zero roll/pitch/yaw, throttle up (arm), back to center, throttle up
        again (liftoff). Optional convenience for your env's reset() -- call
        it, or implement your own arming logic (e.g. let the policy learn to
        arm itself) if that's a better fit for your action-space design."""
        self.set_axes(roll=NEUTRAL, pitch=NEUTRAL, yaw=NEUTRAL)
        self.set_axes(throttle=NEUTRAL + 60)
        time.sleep(1.0)
        self.set_axes(throttle=NEUTRAL)
        time.sleep(0.5)
        self.set_axes(throttle=NEUTRAL + 60)

    def land(self):
        """Neutral does NOT hold altitude and does NOT cut power -- it's a
        gentle, controlled descent down to a low hover (confirmed in
        earlier testing), not a true "land" command. Good enough to end a
        flight; treat it as a soft landing, not a flight-termination switch."""
        self.neutral()


if __name__ == "__main__":
    # Smoke test: no keyboard involved at all -- drives the drone the exact
    # way an RL env would (a scripted sequence of set_axes/set_normalized
    # calls over time), to prove the module works end-to-end on its own.
    # Propellers OFF first if you just want to confirm packets are going out
    # without flying.
    link = DroneLink()
    link.start()
    try:
        print("Arming and launching...")
        link.arm_and_launch()
        time.sleep(2)

        print("Normalized pitch-forward nudge (0.15) for 1s...")
        link.set_normalized(pitch=0.15)
        time.sleep(1)

        print("Back to neutral pitch, holding for 2s...")
        link.set_normalized(pitch=0.0)
        time.sleep(2)

        print("Landing (neutral)...")
        link.land()
        time.sleep(2)
    finally:
        link.stop()
        print("Stopped.")
