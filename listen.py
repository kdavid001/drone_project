import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", 7070))

magic = bytes.fromhex("cc5a018202 36b7".replace(" ", ""))
sock.sendto(magic, ("192.168.28.1", 7080))

print("Sent start command, listening...")
while True:
    data, addr = sock.recvfrom(65536)
    print(f"Got {len(data)} bytes from {addr}")