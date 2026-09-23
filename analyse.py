from scapy.all import rdpcap, UDP

packets = rdpcap("drone_ch1.pcap")

for pkt in packets:
    if UDP in pkt and pkt[UDP].sport == 7070:
        payload = bytes(pkt[UDP].payload)
        header = payload[:4].hex()
        starts_jpeg = payload[4:6] == b'\xff\xd8'
        print(f"header={header}  starts_new_frame={starts_jpeg}  len={len(payload)}")