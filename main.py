"""For video Display"""

import socket
import threading
import io
import time
import numpy as np
import cv2
from PIL import Image

DRONE_IP = "192.168.28.1"
CONTROL_PORT = 7080
VIDEO_PORT = 7070
MAGIC = bytes.fromhex("cc5a01820236b7")

# Camera switch ("sendSW_CMD" in the decompiled app, bound to its btChangeJT
# button -- "JT" = jing tou / lens -- confirmed from r2.i.e()). This toggles
# which physical camera feeds the SAME video channel above: forward-facing
# by default, or the downward-facing one after sending this once. There's no
# separate "select camera N" command -- it's a toggle, so sending it twice
# returns to the forward camera. Both cameras share one video stream, so you
# cannot view/process both at once; you have to switch back and forth.
SWITCH_CAMERA_CMD = bytes.fromhex("cc5a01820238b9")

# A frame_id is a single byte (0-255) and gets reused constantly. If a
# frame's final packet (the one carrying end_flag) is lost on the wire, its
# entry would otherwise never get cleared -- once the ID wraps around and a
# new frame reuses it, out-of-order fragment arrival could silently merge
# the new frame's bytes into the old abandoned one. This timeout forces a
# reset of any frame_id's entry that's been open too long.
STALE_FRAME_TIMEOUT = 0.5  # seconds

latest_frame = None
frame_lock = threading.Lock()
running = True

# Height/optical-flow overlay state. We don't have a confirmed byte->height
# formula yet (no packets were ever actually captured in testing so far) --
# this shows the RAW bytes of whatever arrives, live, directly on the video,
# so you can watch them while moving the drone and we figure out the mapping
# together, instead of reading a separate log file.
latest_of_hex = None
latest_of_time = 0.0
of_count = 0
of_lock = threading.Lock()
OF_STALE_AFTER = 2.0  # seconds -- how long before the overlay says "no data"

# There's no physical indicator light for which camera is active, so this is
# just our own assumption based on how many times we've sent the toggle --
# not a confirmed readout from the drone. Trust the picture itself over this.
camera_mode = "FORWARD"
camera_lock = threading.Lock()


def undo_video_xor(data):
    """The drone XORs exactly one byte of every video packet's payload with
    0xFF before sending (confirmed from the decompiled app's own video
    reassembly code, which calls this step "video transmission decryption").
    The byte position shifts per-packet based on frame_id/frag_idx/length.
    This mirrors the app's own formula to undo it:
        offset = ((frame_id * frag_idx + 10) * 6666) % (len(data) - 8) + 6
        data[offset] ^= 0xFF
    """
    if len(data) <= 8:
        return data
    frame_id = data[0]
    frag_idx = data[2]
    offset = ((frame_id * frag_idx + 10) * 6666) % (len(data) - 8) + 6
    fixed = bytearray(data)
    fixed[offset] ^= 0xFF
    return bytes(fixed)


def decode_strict(jpeg_bytes):
    """Return (bgr_array, size) only if the JPEG is fully intact, else (None, None)."""
    try:
        img = Image.open(io.BytesIO(jpeg_bytes))
        img.load()  # forces full decode; raises on truncated/corrupt data
    except Exception:
        return None, None
    size = img.size
    arr = np.array(img.convert("RGB"))
    bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    # Camera sensor is mounted rotated 180 degrees (confirmed: flying the
    # drone inverted "corrected" the image). A full 180 rotate -- not a
    # horizontal mirror flip -- is the right correction.
    return cv2.rotate(bgr, cv2.ROTATE_180), size


def looks_corrupted(bgr_img, n_strips=12, min_std=8.0, max_bad_strip_frac=0.35):
    """Cheap sanity check: flags frames with too many near-flat (texture-less)
    horizontal strips, which is what remaining packet-loss corruption looks like."""
    h = bgr_img.shape[0]
    strip_h = max(1, h // n_strips)
    flat_strips = 0
    for i in range(0, h, strip_h):
        strip = bgr_img[i:i + strip_h]
        if strip.size and strip.std() < min_std:
            flat_strips += 1
    return (flat_strips / n_strips) > max_bad_strip_frac


def receive_loop():
    global latest_frame, running, latest_of_hex, latest_of_time, of_count

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", VIDEO_PORT))
    sock.settimeout(1.0)
    sock.sendto(MAGIC, (DRONE_IP, CONTROL_PORT))

    frames = {}
    frame_started_at = {}
    good = 0
    bad_incomplete = 0   # fragments missing (real packet loss)
    bad_decode = 0       # PIL couldn't decode at all (truncated/broken JPEG)
    bad_banding = 0      # decoded fine, but flagged by the flat-band heuristic
    bad_resolution = 0   # decoded fine, but wrong locked resolution
    optical_flow_pkts = 0  # packets on this same port that are NOT video fragments
    size_votes = {}
    expected_size = None

    while running:
        try:
            data, addr = sock.recvfrom(65536)
        except socket.timeout:
            continue

        if len(data) < 4:
            continue

        # The app has a separate packet type on this same port (7070) for
        # "optical flow" data, marked by a 0x5A 0xCC header (the reverse of
        # the usual 0xCC 0x5A video/control magic). Keep it out of frame
        # reassembly, but record it so the display loop can overlay it live.
        if data[0] == 0x5A and data[1] == 0xCC:
            optical_flow_pkts += 1
            with of_lock:
                latest_of_hex = data.hex()
                latest_of_time = time.monotonic()
                of_count = optical_flow_pkts
            continue

        data = undo_video_xor(data)

        frame_id, end_flag, frag_idx, stream_id = data[0], data[1], data[2], data[3]
        payload = data[4:]

        now = time.monotonic()
        if frame_id in frames and (now - frame_started_at.get(frame_id, now)) > STALE_FRAME_TIMEOUT:
            frames[frame_id] = {}
        if frag_idx == 1 or frame_id not in frames:
            frames[frame_id] = {}
            frame_started_at[frame_id] = now
        frames[frame_id][frag_idx] = payload

        if end_flag == 1:
            frag_dict = frames.pop(frame_id, {})
            frame_started_at.pop(frame_id, None)
            max_idx = frag_idx

            if not (len(frag_dict) == max_idx and all(i in frag_dict for i in range(1, max_idx + 1))):
                bad_incomplete += 1
            else:
                jpeg_bytes = b"".join(frag_dict[i] for i in range(1, max_idx + 1))
                img, size = decode_strict(jpeg_bytes)

                if img is None:
                    bad_decode += 1
                elif looks_corrupted(img):
                    bad_banding += 1
                elif expected_size is not None and size != expected_size:
                    bad_resolution += 1
                else:
                    if expected_size is None:
                        size_votes[size] = size_votes.get(size, 0) + 1
                        if sum(size_votes.values()) >= 20:
                            expected_size = max(size_votes, key=size_votes.get)
                            print(f"Locked expected resolution: {expected_size}")
                    with frame_lock:
                        latest_frame = img
                    good += 1

            total = good + bad_incomplete + bad_decode + bad_banding + bad_resolution
            if total % 50 == 0:
                print(f"good={good} incomplete={bad_incomplete} decode_fail={bad_decode} "
                      f"banding={bad_banding} wrong_res={bad_resolution} "
                      f"optical_flow_seen={optical_flow_pkts}  "
                      f"({100*good/total:.0f}% clean)")

    sock.close()


def send_camera_switch():
    """Fire-and-forget UDP send of the camera-toggle command. Plain send, no
    reply expected -- same as how the start/stop MAGIC command works."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(SWITCH_CAMERA_CMD, (DRONE_IP, CONTROL_PORT))
    finally:
        sock.close()


def draw_overlay(frame):
    """Draws a banner on the video frame showing the camera mode (our own
    best guess -- no indicator light exists to confirm it) and whatever raw
    optical-flow bytes have arrived most recently. We don't have a confirmed
    byte->height formula yet, so this shows the raw hex rather than a made-up
    number -- watch it while moving the drone up/down to find which byte(s)
    actually track height, then that gets turned into a real "Height: Xcm"
    readout here."""
    h, w = frame.shape[:2]
    banner_h = 48
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, banner_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    with camera_lock:
        mode = camera_mode
    cv2.putText(frame, f"Camera (assumed): {mode}", (8, 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)

    with of_lock:
        of_hex, of_time, count = latest_of_hex, latest_of_time, of_count
    age = time.monotonic() - of_time if of_time else None

    if age is not None and age < OF_STALE_AFTER:
        text = f"Optical-flow #{count}: {of_hex[:40]}"
        color = (0, 255, 0)
    else:
        text = "Optical-flow: NO DATA received"
        color = (0, 0, 255)
    cv2.putText(frame, text, (8, 38),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    return frame


def main():
    global running, camera_mode

    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()

    print("Listening for video... press 'c' to toggle forward/downward camera, 'q' to quit")

    while True:
        with frame_lock:
            frame = latest_frame

        if frame is not None:
            cv2.imshow("Drone Camera", draw_overlay(frame.copy()))

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            running = False
            break
        elif key == ord('c'):
            send_camera_switch()
            with camera_lock:
                camera_mode = "DOWNWARD" if camera_mode == "FORWARD" else "FORWARD"
            print(f"Sent camera switch command -- now assuming: {camera_mode} "
                  f"(no indicator light to confirm; check the picture itself)")

    recv_thread.join(timeout=2)
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
