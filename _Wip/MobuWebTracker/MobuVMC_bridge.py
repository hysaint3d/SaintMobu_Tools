import sys
import subprocess
import socket
import struct
import json
import asyncio
import os

# --- Dependency Check ---
try:
    import websockets
except ImportError:
    print(">>> Installing missing dependency: websockets...")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets"])
        import websockets
    except Exception as e:
        print(f"!!! Failed to install 'websockets': {e}")
        print("Please run: pip install websockets")
        sys.exit(1)

# --- Minimal OSC Packing (No python-osc needed) ---
def pack_osc_message(address, types, *args):
    # Address
    msg = address.encode('utf-8') + b'\x00'
    while len(msg) % 4 != 0: msg += b'\x00'
    # Types
    msg += b',' + types.encode('utf-8') + b'\x00'
    while len(msg) % 4 != 0: msg += b'\x00'
    # Args
    for i, t in enumerate(types):
        if t == 'f':
            val = args[i] if args[i] is not None else 0.0
            msg += struct.pack('>f', float(val))
        elif t == 'i':
            val = args[i] if args[i] is not None else 0
            msg += struct.pack('>i', int(val))
        elif t == 's':
            val = args[i] if args[i] is not None else ""
            s_bytes = val.encode('utf-8') + b'\x00'
            while len(s_bytes) % 4 != 0: s_bytes += b'\x00'
            msg += s_bytes
    return msg

def pack_osc_bundle(messages):
    bundle = b'#bundle\x00'
    bundle += b'\x00\x00\x00\x00\x00\x00\x00\x01' # Timetag: immediately
    for msg in messages:
        bundle += struct.pack('>i', len(msg))
        bundle += msg
    return bundle

# --- Configuration ---
LISTEN_IP = "localhost"
LISTEN_PORT = 8080
TARGET_IP = "127.0.0.1"
TARGET_PORT = 39539  # Default VMC Port (matches VMC2Mobu)

udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

async def handler(websocket):
    print(f">>> Browser connected!")
    packet_count = 0
    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                if isinstance(data, list):
                    osc_messages = []
                    for item in data:
                        osc_msg = pack_osc_message(item['addr'], item['types'], *item['args'])
                        osc_messages.append(osc_msg)
                    
                    if osc_messages:
                        bundle = pack_osc_bundle(osc_messages)
                        udp_sock.sendto(bundle, (TARGET_IP, TARGET_PORT))
                        packet_count += len(osc_messages)
                
                if packet_count % 100 == 0:
                    print(f"\r>>> Relaying data... Total frames sent.", end="")
            except Exception:
                pass
    except Exception as e:
        print(f"\n>>> Connection error: {e}")

async def main():
    os.system('cls' if os.name == 'nt' else 'clear')
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" MobuVMC-Bridge v1.0")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f" [1] Listening for Browser: ws://{LISTEN_IP}:{LISTEN_PORT}")
    print(f" [2] Forwarding to MoBu:    UDP {TARGET_IP}:{TARGET_PORT}")
    print(" [3] Status:                READY. Please open MobuWebFace.html in Chrome.")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(" Press Ctrl+C to stop.\n")
    
    async with websockets.serve(handler, LISTEN_IP, LISTEN_PORT):
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n>>> Bridge stopped by user.")
    except Exception as e:
        print(f"\n!!! Critical error: {e}")
