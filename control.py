import socket
import time

DRONE_IP = "192.168.28.1"
DRONE_PORT = 7080

NEUTRAL    = bytes.fromhex("cc5a018309668080808000009974")     # stick centered
CANDIDATE4 = bytes.fromhex("cc5a018309668080b27200c09974")     # throttle bump up

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

def send_for(payload, seconds, label):
    print(f"{label} for {seconds}s...")
    start = time.time()
    while time.time() - start < seconds:
        sock.sendto(payload, (DRONE_IP, DRONE_PORT))
        time.sleep(0.02)

send_for(NEUTRAL, 0.5, "Priming (neutral)")
send_for(CANDIDATE4, 1.0, "Stage 1: candidate 4 (arm)")
send_for(NEUTRAL, 0.025, "Back to neutral")
send_for(CANDIDATE4, 1.0, "Stage 2: candidate 4 (liftoff)")

input("\nairborne now. Press Enter to go to NEUTRAL and watch closely...")
send_for(NEUTRAL, 2.0, "Neutral — does it hold height, sink slowly, or drop immediately?")

print("\nDone — note what you saw.")
sock.close()