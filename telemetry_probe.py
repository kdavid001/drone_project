"""
Telemetry probe: listens for whatever the drone sends back on the UDP
control channel, so we can find out what's actually in its reply packets.

Background: the stock app (RCFPVPro) opens a DatagramSocket bound to LOCAL
port 7060, sends control packets to the drone at 192.168.28.1:7080, and also
listens on that same local port 7060 for replies (class r2.h/r2.g in the
decompiled app) -- but the app's own receive handler just logs that
*something* arrived (length + source port) and then throws the bytes away.
It never decodes them. The app DOES ship real telemetry data-model classes
though -- PlaneSensor (Gyr/Acc/Mag/baro/BaroTemp/ImuTemp/Roll/Pitch/Yaw/Thor/
Motor1-4), PlaneTestInfo (raw gyro_x/y/z, acc_x/y/z, mag_x/y/z, battery,
GPS, baro altitude), PlaneInforXL (AttitudeRoll/Pitch/Yaw, Armed, FlyMode,
BatVal, Takeoff/AutoLand/LandFinish, calibration flags) -- those are just
wired up for OTHER product lines in this shared app, not this drone's own
screen. So there's a good chance the drone is already sending back real
IMU/attitude/battery data on this same channel every time we send it a
command; nobody has ever looked at what's actually in it. That's what this
script is for.

What it does:
  - Binds locally to port 7060 (matching the real app exactly, in case the
    drone replies specifically to that port rather than to whatever port
    sent the command).
  - Sends a NEUTRAL control packet every 20ms the whole time (keeps the
    channel "primed" the same way flying does, in case the drone only
    replies to active commands -- same safe default keyboard_control.py
    already uses).
  - Listens continuously for incoming packets, prints each one as hex with
    a timestamp whenever the bytes differ from the last one seen (so the
    terminal doesn't flood if the drone echoes back constantly), and prints
    a heartbeat every few seconds either way so it's obvious if truly
    nothing is arriving.
  - Logs EVERY packet (not just changes) to a timestamped .txt file in this
    folder, so the full raw capture is there to review afterward even if
    something is missed live.

How to use it (safest first):
  1. Props OFF. Power-cycle the drone on a flat surface (usual calibration
     practice), connect to its WiFi.
  2. Run: python3 telemetry_probe.py
  3. With the drone sitting still, gently tilt it by hand in each direction
     (nose up/down, roll left/right -- slow, small amounts) and watch
     whether any printed bytes change when you do. If the flight controller
     streams attitude/gyro data even while disarmed, values should move
     with the tilt and settle back when you let go.
  4. If NOTHING changes while disarmed, the FC may only report once armed --
     in that case run this in one terminal and keyboard_control.py in
     another, and fly normally while watching this window.
  5. Ctrl+C to stop. The full log file is kept either way (a "nothing ever
     arrived" result is still a useful, real finding -- it means this
     particular reply channel isn't where telemetry lives, if anywhere).

This is read-only/diagnostic on top of what keyboard_control.py already
does -- it does not change flight control behavior, and defaults to sending
NEUTRAL the whole time it runs.
"""
import socket
import threading
import time
import datetime
import os

DRONE_IP = "192.168.28.1"
DRONE_PORT = 7080
LOCAL_PORT = 7060  # matches the real app's own client port exactly

HEADER = bytes.fromhex("cc5a01830966")
FOOTER = bytes.fromhex("9974")

LOG_DIR = os.path.dirname(os.path.abspath(__file__)) or "."
LOG_PATH = os.path.join(
    LOG_DIR, f"telemetry_log_{datetime.datetime.now():%Y%m%d_%H%M%S}.txt"
)

running = True
received_count = 0
count_lock = threading.Lock()


def build_neutral_packet():
    b6 = b7 = b8 = b9 = 128
    checksum = b6 ^ b7 ^ b8 ^ b9
    return HEADER + bytes([b6, b7, b8, b9, 0x00, checksum]) + FOOTER


def now():
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def sender_loop(sock):
    pkt = build_neutral_packet()
    while running:
        try:
            sock.sendto(pkt, (DRONE_IP, DRONE_PORT))
        except OSError:
            pass
        time.sleep(0.02)


def receiver_loop(sock, log_file):
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
        log_file.write(
            f"[{now()}] RECV from {addr}  len={len(data)}  hex={hex_str}\n"
        )
        log_file.flush()
        if data != last_seen:
            print(f"[{now()}] CHANGED  len={len(data):3d}  hex={hex_str}")
            last_seen = data


def main():
    global running
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", LOCAL_PORT))
    except OSError as e:
        print(f"Could not bind to local port {LOCAL_PORT} ({e}).")
        print("Only one process can hold that port at a time -- close any other")
        print("running control/telemetry script first, then try again.")
        return

    print(f"Telemetry probe running. Sending NEUTRAL to {DRONE_IP}:{DRONE_PORT},")
    print(f"listening on local port {LOCAL_PORT}. Logging full capture to:")
    print(f"  {LOG_PATH}")
    print("Tilt the drone gently by hand (props off) and watch for CHANGED lines.")
    print("A heartbeat prints every 3s either way. Ctrl+C to stop.\n")

    with open(LOG_PATH, "w") as log_file:
        send_thread = threading.Thread(target=sender_loop, args=(sock,), daemon=True)
        recv_thread = threading.Thread(
            target=receiver_loop, args=(sock, log_file), daemon=True
        )
        send_thread.start()
        recv_thread.start()
        try:
            while True:
                time.sleep(3)
                with count_lock:
                    c = received_count
                if c == 0:
                    print(f"[{now()}] heartbeat: 0 packets received so far")
                else:
                    print(f"[{now()}] heartbeat: {c} packets received so far")
        except KeyboardInterrupt:
            pass
        finally:
            running = False
            sock.close()
            print("\nStopped. Full capture saved at:")
            print(f"  {LOG_PATH}")


if __name__ == "__main__":
    main()
