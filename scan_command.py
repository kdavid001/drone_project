from scapy.all import rdpcap, IP, UDP

PCAP_PATH = "drone_2.pcap"  # switched to drone_2.pcap — this is the one that had the registration burst originally

pkts = rdpcap(PCAP_PATH)
drone_ip = "192.168.28.1"

has_ip = sum(1 for p in pkts if IP in p)
has_udp = sum(1 for p in pkts if UDP in p)
to_drone = sum(1 for p in pkts if IP in p and p[IP].dst == drone_ip)

print(f"Total packets: {len(pkts)}")
print(f"Packets with an IP layer: {has_ip}")
print(f"Packets with a UDP layer: {has_udp}")
print(f"Packets addressed TO the drone: {to_drone}")
print()

print("Every unique packet sent BY a client TO the drone (any port, any sender):")
seen = set()
for pkt in pkts:
    if IP not in pkt or UDP not in pkt:
        continue
    ip = pkt[IP]
    udp = pkt[UDP]
    if ip.dst != drone_ip:
        continue
    payload = bytes(udp.payload)
    key = (ip.src, udp.sport, udp.dport, payload.hex())
    if key in seen:
        continue
    seen.add(key)
    print(f"  {ip.src}:{udp.sport} -> {drone_ip}:{udp.dport}  len={len(payload)}  payload={payload.hex()}")