"""For video Display"""

import socket
import threading
import io
import numpy as np
import cv2
from PIL import Image

DRONE_IP = "192.168.28.1"
CONTROL_PORT = 7080
VIDEO_PORT = 7070
MAGIC = bytes.fromhex("cc5a01820236b7")

latest_frame = None
frame_lock = threading.Lock()
running = True

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
    return cv2.flip(bgr, 1), size  # correct the mirrored camera sensor

def looks_corrupted(bgr_img, n_strips=12, min_std=8.0, max_bad_strip_frac=0.35):
    h = bgr_img.shape[0]
    strip_h = max(1, h // n_strips)
    flat_strips = 0
    for i in range(0, h, strip_h):
        strip = bgr_img[i:i + strip_h]
        if strip.size and strip.std() < min_std:
            flat_strips += 1
    return (flat_strips / n_strips) > max_bad_strip_frac

def receive_loop():
    global latest_frame, running

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", VIDEO_PORT))
    sock.settimeout(1.0)
    sock.sendto(MAGIC, (DRONE_IP, CONTROL_PORT))

    frames = {}
    good = 0
    bad_incomplete = 0   # fragments missing (real packet loss)
    bad_decode = 0       # PIL couldn't decode at all (truncated/broken JPEG)
    bad_banding = 0      # decoded fine, but flagged by the flat-band heuristic
    bad_resolution = 0   # decoded fine, but wrong locked resolution
    size_votes = {}
    expected_size = None

    while running:
        try:
            data, addr = sock.recvfrom(65536)
        except socket.timeout:
            continue

        if len(data) < 4:
            continue

        frame_id, end_flag, frag_idx, stream_id = data[0], data[1], data[2], data[3]
        payload = data[4:]

        if frag_idx == 1:
            frames[frame_id] = {}
        frames.setdefault(frame_id, {})[frag_idx] = payload

        if end_flag == 1:
            frag_dict = frames.pop(frame_id, {})
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
                      f"banding={bad_banding} wrong_res={bad_resolution}  "
                      f"({100*good/total:.0f}% clean)")

    sock.close()

def main():
    global running

    recv_thread = threading.Thread(target=receive_loop, daemon=True)
    recv_thread.start()

    print("Listening for video... press 'q' to quit")

    while True:
        with frame_lock:
            frame = latest_frame

        if frame is not None:
            cv2.imshow("Drone Camera", frame)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            running = False
            break

    recv_thread.join(timeout=2)
    cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
