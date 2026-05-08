import sys
import subprocess
import socket
import struct
import json
import asyncio
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext
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
        sys.exit(1)

# --- OSC Packing Logic ---
def pack_osc_message(address, types, *args):
    msg = address.encode('utf-8') + b'\x00'
    while len(msg) % 4 != 0: msg += b'\x00'
    msg += b',' + types.encode('utf-8') + b'\x00'
    while len(msg) % 4 != 0: msg += b'\x00'
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
    bundle += b'\x00\x00\x00\x00\x00\x00\x00\x01'
    for msg in messages:
        bundle += struct.pack('>i', len(msg))
        bundle += msg
    return bundle

# --- GUI Class ---
class MobuBridgeGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Saint's MobuBridge GUI v1.0")
        self.root.geometry("450x550")
        self.root.configure(bg="#2b2b2b")
        
        self.loop = None
        self.server = None
        self.is_running = False
        self.packet_count = 0
        self.config_file = os.path.join(os.path.dirname(__file__), "bridge_config.json")
        self.local_ip = self.get_local_ip()
        
        self.style = ttk.Style()
        self.style.theme_use('clam')
        self.setup_styles()
        
        self.create_widgets()
        self.load_config()

    def load_config(self):
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    self.mode_var.set(config.get("mode", "VMC"))
                    self.ip_entry.delete(0, tk.END)
                    self.ip_entry.insert(0, config.get("target_ip", "127.0.0.1"))
                    self.port_entry.delete(0, tk.END)
                    self.port_entry.insert(0, config.get("target_port", "39539"))
                    self.ws_port_entry.delete(0, tk.END)
                    self.ws_port_entry.insert(0, config.get("ws_port", "8080"))
        except Exception as e:
            print(f"Error loading config: {e}")

    def save_config(self):
        try:
            config = {
                "mode": self.mode_var.get(),
                "target_ip": self.ip_entry.get(),
                "target_port": self.port_entry.get(),
                "ws_port": self.ws_port_entry.get()
            }
            with open(self.config_file, 'w') as f:
                json.dump(config, f)
        except Exception as e:
            print(f"Error saving config: {e}")

    def setup_styles(self):
        self.style.configure("TFrame", background="#2b2b2b")
        self.style.configure("TLabel", background="#2b2b2b", foreground="#ffffff", font=("Segoe UI", 10))
        self.style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#0078d4")
        self.style.configure("TButton", font=("Segoe UI", 10, "bold"))
        self.style.configure("Start.TButton", background="#1b5e20", foreground="white")
        self.style.map("Start.TButton", background=[('active', '#2e7d32')])
        self.style.configure("Stop.TButton", background="#b71c1c", foreground="white")
        self.style.map("Stop.TButton", background=[('active', '#c62828')])

    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="20")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header = ttk.Label(main_frame, text="MobuBridge - WebSocket to OSC", style="Header.TLabel")
        header.pack(pady=(0, 20))

        # Config Group
        config_frame = ttk.LabelFrame(main_frame, text=" Configuration ", padding="15")
        config_frame.pack(fill=tk.X, pady=5)

        # Mode Selection
        ttk.Label(config_frame, text="Mode:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.mode_var = tk.StringVar(value="VMC")
        mode_cb = ttk.Combobox(config_frame, textvariable=self.mode_var, values=["VMC", "OSC (Generic)"], state="readonly")
        mode_cb.grid(row=0, column=1, sticky=tk.EW, pady=5)
        mode_cb.bind("<<ComboboxSelected>>", self.on_mode_change)

        # Target IP
        ttk.Label(config_frame, text="Target IP:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.ip_entry = ttk.Entry(config_frame)
        self.ip_entry.insert(0, "127.0.0.1")
        self.ip_entry.grid(row=1, column=1, sticky=tk.EW, pady=5)

        # Target Port
        ttk.Label(config_frame, text="Target Port:").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.port_entry = ttk.Entry(config_frame)
        self.port_entry.insert(0, "39539")
        self.port_entry.grid(row=2, column=1, sticky=tk.EW, pady=5)

        # WS Port
        ttk.Label(config_frame, text="WS Listen Port:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.ws_port_entry = ttk.Entry(config_frame)
        self.ws_port_entry.insert(0, "8080")
        self.ws_port_entry.grid(row=3, column=1, sticky=tk.EW, pady=5)

        config_frame.columnconfigure(1, weight=1)

        # Local IP Display
        ip_frame = ttk.Frame(main_frame)
        ip_frame.pack(fill=tk.X, pady=5)
        ttk.Label(ip_frame, text="Your IP:", font=("Segoe UI", 9)).pack(side=tk.LEFT)
        self.ip_display = ttk.Label(ip_frame, text=self.local_ip, font=("Segoe UI", 10, "bold"), foreground="#a5d6a7")
        self.ip_display.pack(side=tk.LEFT, padx=5)
        
        btn_copy = ttk.Button(ip_frame, text="Copy", width=6, command=self.copy_ip)
        btn_copy.pack(side=tk.LEFT, padx=2)
        
        btn_refresh = ttk.Button(ip_frame, text="↻", width=3, command=self.refresh_ip)
        btn_refresh.pack(side=tk.LEFT, padx=2)
        
        ttk.Label(ip_frame, text="(Use on iPad)", font=("Segoe UI", 8, "italic"), foreground="#888").pack(side=tk.LEFT, padx=5)

        # Controls
        self.btn_toggle = ttk.Button(main_frame, text="START BRIDGE", style="TButton", command=self.toggle_bridge)
        self.btn_toggle.pack(fill=tk.X, pady=20)

        # Log
        ttk.Label(main_frame, text="Activity Log:").pack(anchor=tk.W)
        self.log_area = scrolledtext.ScrolledText(main_frame, height=10, bg="#1a1a1a", fg="#a5d6a7", font=("Consolas", 9), borderwidth=0)
        self.log_area.pack(fill=tk.BOTH, expand=True, pady=5)

        # Footer
        footer = ttk.Label(main_frame, text="Collaboration by Saint & Antigravity", font=("Segoe UI", 8), foreground="#666")
        footer.pack(pady=(10, 0))

    def on_mode_change(self, event=None):
        if self.mode_var.get() == "VMC":
            self.port_entry.delete(0, tk.END)
            self.port_entry.insert(0, "39539")
        else:
            self.port_entry.delete(0, tk.END)
            self.port_entry.insert(0, "9000")

    def log(self, message):
        self.log_area.insert(tk.END, f"{message}\n")
        self.log_area.see(tk.END)

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    def refresh_ip(self):
        self.local_ip = self.get_local_ip()
        self.ip_display.configure(text=self.local_ip)
        self.log(f">>> IP Refreshed: {self.local_ip}")

    def copy_ip(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.local_ip)
        self.log(">>> IP copied to clipboard!")

    def toggle_bridge(self):
        if not self.is_running:
            self.start_bridge()
        else:
            self.stop_bridge()

    def start_bridge(self):
        self.target_ip = self.ip_entry.get()
        try:
            self.target_port = int(self.port_entry.get())
            self.ws_port = int(self.ws_port_entry.get())
        except ValueError:
            self.log("!!! Error: Port must be a number.")
            return

        self.save_config()
        self.is_running = True
        self.btn_toggle.configure(text="STOP BRIDGE")
        self.packet_count = 0
        self.log(f">>> Bridge Started (Mode: {self.mode_var.get()})")
        self.log(f">>> Listening on WS:{self.ws_port}")
        self.log(f">>> Forwarding to {self.target_ip}:{self.target_port}")

        self.thread = threading.Thread(target=self.run_async_loop, daemon=True)
        self.thread.start()

    def stop_bridge(self):
        self.is_running = False
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.btn_toggle.configure(text="START BRIDGE")
        self.log(">>> Bridge Stopped.")

    def run_async_loop(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        
        self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        
        async def handler(websocket):
            self.root.after(0, lambda: self.log(">>> Browser Connected!"))
            try:
                async for message in websocket:
                    if not self.is_running: break
                    try:
                        data = json.loads(message)
                        if isinstance(data, list):
                            osc_messages = [pack_osc_message(item['addr'], item['types'], *item['args']) for item in data]
                            if osc_messages:
                                bundle = pack_osc_bundle(osc_messages)
                                self.udp_sock.sendto(bundle, (self.target_ip, self.target_port))
                                self.packet_count += len(osc_messages)
                                if self.packet_count % 100 == 0:
                                    self.root.after(0, lambda c=self.packet_count: self.log(f"Sent {c} packets..."))
                    except: pass
            except Exception as e:
                self.root.after(0, lambda err=e: self.log(f"!!! WS Error: {err}"))

        async def start_ws():
            async with websockets.serve(handler, "0.0.0.0", self.ws_port):
                await asyncio.Future()

        try:
            self.loop.run_until_complete(start_ws())
        except Exception as e:
            if self.is_running:
                self.root.after(0, lambda err=e: self.log(f"!!! Loop Error: {err}"))

if __name__ == "__main__":
    root = tk.Tk()
    app = MobuBridgeGUI(root)
    root.mainloop()
