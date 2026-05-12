"""
MocapLab_SyncMaster_GUI.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mocap Lab — Master Sync Recording Console
Simultaneously triggers recording across multiple software targets.

Supported Targets:
  - MotionBuilder  (OSC/UDP → MocapLab_SyncRecorder.py)
  - Optitrack Motive 2.x / 3.x  (UDP Remote Trigger)
  - OBS Studio  (obs-websocket v5)
  - Unreal Engine 5  (Web Remote Control API)
  - Warudo  (Placeholder - not yet implemented)

Features:
  - Tkinter GUI with per-target IP/Port configuration
  - Recording timer display
  - Built-in Flask web server for LAN remote control (http://<IP>:5000)
  - JSON config auto-save/load

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import sys
import os
import subprocess
import socket
import struct
import json
import time
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext

# ── Dependency Auto-Install ───────────────────────────────────────────────────
def _ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
    except ImportError:
        print(f">>> Installing {pkg}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])

_ensure("flask")
_ensure("obsws-python", "obsws_python")
_ensure("requests")

import requests
from flask import Flask, request, jsonify, render_template_string, Response

# ── OSC Helper ────────────────────────────────────────────────────────────────
def pack_osc(address: str, args=None) -> bytes:
    """Pack an OSC message. Supports string args."""
    msg = address.encode('utf-8') + b'\x00'
    while len(msg) % 4 != 0:
        msg += b'\x00'

    if args and len(args) > 0:
        # Build type tag string
        type_tag = b','
        encoded_args = b''
        for a in args:
            if isinstance(a, str):
                type_tag += b's'
                s_bytes = a.encode('utf-8') + b'\x00'
                while len(s_bytes) % 4 != 0:
                    s_bytes += b'\x00'
                encoded_args += s_bytes
            elif isinstance(a, int):
                type_tag += b'i'
                encoded_args += struct.pack('>i', a)
            elif isinstance(a, float):
                type_tag += b'f'
                encoded_args += struct.pack('>f', a)
        type_tag += b'\x00'
        while len(type_tag) % 4 != 0:
            type_tag += b'\x00'
        msg += type_tag + encoded_args
    else:
        msg += b',\x00\x00\x00'  # empty type tag
    return msg

# ── Target Senders ────────────────────────────────────────────────────────────
def send_mobu(ip, port, cmd, log, take_name=""):
    """Send OSC RecordStart/Stop to MocapLab_SyncRecorder."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target = (ip, int(port))

        if cmd == "start":
            # Send TakeName first if provided
            if take_name:
                name_pkt = pack_osc("/TakeName", [take_name])
                sock.sendto(name_pkt, target)
                log(f"[Mobu] /TakeName → {take_name}")
            sock.sendto(pack_osc("/RecordStart"), target)
            log(f"[Mobu] /RecordStart → {ip}:{port} ✔")
        else:
            # Send RecordStop to stop recording, then Stop to stop transport
            sock.sendto(pack_osc("/RecordStop"), target)
            log(f"[Mobu] /RecordStop → {ip}:{port} ✔")
            time.sleep(0.1)  # small delay to let RecordStop process first
            sock.sendto(pack_osc("/Stop"), target)
            log(f"[Mobu] /Stop → {ip}:{port} ✔")

        sock.close()
    except Exception as e:
        log(f"[Mobu] ERROR: {e}")


def send_motive(ip, port, cmd, version, log, take_name=""):
    """Send XML Remote Trigger to Optitrack Motive (standard for 2.x/3.x)."""
    try:
        # Motive XML Remote Trigger format
        if cmd == "start":
            xml = f'<?xml version="1.0" encoding="UTF-8" standalone="no" ?><CaptureStart><Name VALUE="{take_name}"/></CaptureStart>'
            action_log = "CaptureStart"
        else:
            xml = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?><CaptureStop/>'
            action_log = "CaptureStop"

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.sendto(xml.encode('utf-8'), (ip, int(port)))
        sock.close()
        log(f"[Motive] {action_log} → {ip}:{port} (Take: {take_name if cmd=='start' else 'N/A'}) ✔")
    except Exception as e:
        log(f"[Motive] ERROR: {e}")


def send_obs(ip, port, password, cmd, log):
    """Send StartRecord/StopRecord via obs-websocket v5."""
    try:
        import obsws_python as obs
        cl = obs.ReqClient(host=ip, port=int(port), password=password, timeout=3)
        if cmd == "start":
            cl.start_record()
            log(f"[OBS] StartRecord → {ip}:{port} ✔")
        else:
            cl.stop_record()
            log(f"[OBS] StopRecord → {ip}:{port} ✔")
        cl.disconnect()
    except Exception as e:
        log(f"[OBS] ERROR: {e}")


def send_ue5(ip, port, cmd, log):
    """Send StartRecording/StopRecording via UE5 Web Remote Control."""
    try:
        url = f"http://{ip}:{port}/remote/object/call"
        func = "StartRecording" if cmd == "start" else "StopRecording"
        body = {
            "objectPath": "/Script/TakeRecorder.Default__TakeRecorderBlueprintLibrary",
            "functionName": func,
            "parameters": {}
        }
        resp = requests.put(url, json=body, timeout=3)
        log(f"[UE5] {func} → {ip}:{port} [{resp.status_code}] ✔")
    except Exception as e:
        log(f"[UE5] ERROR: {e}")


def send_warudo(ip, port, cmd, log):
    """Warudo recording trigger — placeholder, not yet implemented."""
    log(f"[Warudo] Not yet implemented (reserved: {ip}:{port})")

# ── Web UI HTML ───────────────────────────────────────────────────────────────
WEB_UI_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MocapLab SyncMaster</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: 'Segoe UI', system-ui, sans-serif;
    background: #111; color: #eee;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    min-height: 100vh; gap: 24px; padding: 20px;
  }
  h1 { font-size: 1.4rem; color: #aaa; letter-spacing: 2px; text-transform: uppercase; }
  #timer {
    font-size: 4rem; font-weight: bold; letter-spacing: 4px;
    color: #fff; font-variant-numeric: tabular-nums;
  }
  #timer.recording { color: #f44336; }
  #status { font-size: 0.9rem; color: #888; }
  .btn {
    width: 220px; height: 70px; border: none; border-radius: 16px;
    font-size: 1.3rem; font-weight: bold; cursor: pointer;
    transition: transform 0.1s, opacity 0.2s;
    letter-spacing: 1px;
  }
  .btn:active { transform: scale(0.96); }
  #btn-start { background: #c62828; color: white; }
  #btn-start:hover { background: #e53935; }
  #btn-stop  { background: #333; color: #aaa; border: 2px solid #555; }
  #btn-stop:hover  { background: #444; }
  .take-row { display: flex; gap: 8px; align-items: center; }
  .take-row input {
    background: #222; border: 1px solid #444; color: #eee;
    padding: 8px 12px; border-radius: 8px; font-size: 0.9rem;
    width: 220px;
  }
  footer { color: #444; font-size: 0.75rem; }
</style>
</head>
<body>
  <h1>🎬 MocapLab SyncMaster</h1>
  <div id="timer">00:00:00</div>
  <div id="status">Idle</div>

  <div class="take-row">
    <input id="take-name" type="text" placeholder="Take Name (optional)">
  </div>

  <button class="btn" id="btn-start" onclick="sendCmd('start')">⏺ START</button>
  <button class="btn" id="btn-stop"  onclick="sendCmd('stop')">⏹ STOP</button>

  <footer>Mocap Lab × Antigravity — LAN Remote</footer>

<script>
  function sendCmd(action) {
    const takeName = document.getElementById('take-name').value.trim();
    fetch('/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, take_name: takeName })
    });
  }

  function pad(n) { return String(n).padStart(2, '0'); }

  function poll() {
    fetch('/status').then(r => r.json()).then(d => {
      const el = document.getElementById('timer');
      const s = d.elapsed;
      el.textContent = pad(Math.floor(s/3600)) + ':' + pad(Math.floor(s/60)%60) + ':' + pad(s%60);
      el.className = d.recording ? 'recording' : '';
      document.getElementById('status').textContent = d.recording ? '⏺ Recording: ' + d.take_name : 'Idle';
    }).catch(() => {});
  }
  setInterval(poll, 1000);
  poll();
</script>
</body>
</html>
"""

# ── Main GUI Application ──────────────────────────────────────────────────────
class SyncMasterApp:
    def __init__(self, root):
        self.root = root
        self.root.title("MocapLab SyncMaster Console")
        self.root.geometry("520x720")
        self.root.configure(bg="#1a1a1a")
        self.root.resizable(False, False)

        self.is_recording = False
        self.rec_start_time = 0
        self.elapsed_sec = 0
        self.current_take = ""
        self.timer_thread = None

        self.config_path = os.path.join(os.path.dirname(__file__), "sync_config.json")
        self.local_ip = self._get_local_ip()

        # Flask app in background thread
        self.flask_app = self._build_flask()
        self.flask_thread = threading.Thread(
            target=lambda: self.flask_app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False),
            daemon=True
        )
        self.flask_thread.start()

        self._setup_styles()
        self._build_ui()
        self._load_config()

    # ── Flask Web Server ──────────────────────────────────────────────────────
    def _build_flask(self):
        app = Flask(__name__)

        # ── CORS: allow requests from any origin (file:// or LAN devices)
        @app.after_request
        def add_cors(response):
            response.headers['Access-Control-Allow-Origin']  = '*'
            response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            return response

        @app.route("/", methods=["GET", "OPTIONS"])
        def index():
            if request.method == "OPTIONS":
                return Response(status=200)
            return render_template_string(WEB_UI_HTML)

        @app.route("/status", methods=["GET", "OPTIONS"])
        def status():
            if request.method == "OPTIONS":
                return Response(status=200)
            return jsonify({
                "recording": self.is_recording,
                "elapsed": self.elapsed_sec,
                "take_name": self.current_take
            })

        @app.route("/record", methods=["POST", "OPTIONS"])
        def record():
            if request.method == "OPTIONS":
                return Response(status=200)
            data = request.get_json(force=True)
            action = data.get("action", "")
            take_name = data.get("take_name", "")
            self.root.after(0, lambda: self._trigger(action, take_name))
            return jsonify({"ok": True})

        return app

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"

    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.root.after(0, lambda: (
            self.log_area.insert(tk.END, f"[{ts}] {msg}\n"),
            self.log_area.see(tk.END)
        ))

    def _setup_styles(self):
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self.style.configure("TFrame", background="#1a1a1a")
        self.style.configure("TLabel", background="#1a1a1a", foreground="#cccccc", font=("Segoe UI", 10))
        self.style.configure("Title.TLabel", background="#1a1a1a", foreground="#e0e0e0",
                             font=("Segoe UI", 13, "bold"))
        self.style.configure("Timer.TLabel", background="#1a1a1a", foreground="#ffffff",
                             font=("Consolas", 36, "bold"))
        self.style.configure("RecTimer.TLabel", background="#1a1a1a", foreground="#f44336",
                             font=("Consolas", 36, "bold"))
        self.style.configure("TLabelframe", background="#1a1a1a", foreground="#888888")
        self.style.configure("TLabelframe.Label", background="#1a1a1a", foreground="#888888",
                             font=("Segoe UI", 9))
        self.style.configure("TCheckbutton", background="#1a1a1a", foreground="#cccccc")
        self.style.configure("TEntry", fieldbackground="#2a2a2a", foreground="#eeeeee",
                             insertcolor="#eeeeee")
        self.style.configure("TCombobox", fieldbackground="#2a2a2a", foreground="#eeeeee")

    def _build_ui(self):
        pad = {"padx": 16, "pady": 4}

        # Title
        ttk.Label(self.root, text="🎬  MocapLab SyncMaster Console", style="Title.TLabel").pack(pady=(18, 6))

        # Take Name
        take_frame = ttk.Frame(self.root)
        take_frame.pack(fill=tk.X, padx=16, pady=4)
        ttk.Label(take_frame, text="Take Name:").pack(side=tk.LEFT)
        self.take_entry = ttk.Entry(take_frame, width=28)
        self.take_entry.insert(0, "Master_Take")
        self.take_entry.pack(side=tk.LEFT, padx=8)

        # Targets panel
        targets_frame = ttk.LabelFrame(self.root, text=" Targets ", padding=10)
        targets_frame.pack(fill=tk.X, padx=16, pady=6)

        self.targets = {}
        self._add_target(targets_frame, "mobu",   "MotionBuilder", "127.0.0.1", "9000")
        self._add_motive(targets_frame)
        self._add_obs(targets_frame)
        self._add_target(targets_frame, "ue5",    "Unreal Engine 5", "127.0.0.1", "30010")
        self._add_target(targets_frame, "warudo", "Warudo (reserved)", "127.0.0.1", "39539", default_on=False)

        # Timer
        self.timer_label = ttk.Label(self.root, text="00:00:00", style="Timer.TLabel")
        self.timer_label.pack(pady=(10, 2))
        self.status_label = ttk.Label(self.root, text="Idle", foreground="#666666",
                                      background="#1a1a1a", font=("Segoe UI", 9))
        self.status_label.pack()

        # Buttons
        btn_frame = tk.Frame(self.root, bg="#1a1a1a")
        btn_frame.pack(pady=10)
        self.btn_start = tk.Button(btn_frame, text="⏺  START RECORD",
                                   bg="#c62828", fg="white", activebackground="#e53935",
                                   font=("Segoe UI", 12, "bold"), width=20, height=2,
                                   relief="flat", cursor="hand2",
                                   command=lambda: self._trigger("start"))
        self.btn_start.pack(side=tk.LEFT, padx=6)
        self.btn_stop = tk.Button(btn_frame, text="⏹  STOP",
                                  bg="#333333", fg="#aaaaaa", activebackground="#444444",
                                  font=("Segoe UI", 12, "bold"), width=10, height=2,
                                  relief="flat", cursor="hand2",
                                  command=lambda: self._trigger("stop"))
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        # Web URL — with copy button
        url_frame = ttk.Frame(self.root)
        url_frame.pack(fill=tk.X, padx=16, pady=(2, 4))

        web_url = f"http://{self.local_ip}:5000"

        ttk.Label(url_frame, text="🌐", foreground="#64b5f6",
                  background="#1a1a1a", font=("Segoe UI", 10)).pack(side=tk.LEFT)
        self.url_label = ttk.Label(url_frame,
                                   text=web_url,
                                   foreground="#64b5f6", background="#1a1a1a",
                                   font=("Segoe UI", 9, "underline"), cursor="hand2")
        self.url_label.pack(side=tk.LEFT, padx=4)
        self.url_label.bind("<Button-1>",
                            lambda e: os.startfile(web_url))

        def copy_url():
            self.root.clipboard_clear()
            self.root.clipboard_append(web_url)
            btn_copy_url.configure(text="Copied!")
            self.root.after(1500, lambda: btn_copy_url.configure(text="Copy"))

        btn_copy_url = ttk.Button(url_frame, text="Copy", width=6, command=copy_url)
        btn_copy_url.pack(side=tk.LEFT, padx=4)

        # Web Remote HTML hint
        hint_frame = ttk.Frame(self.root)
        hint_frame.pack(fill=tk.X, padx=16, pady=(0, 4))
        ttk.Label(hint_frame,
                  text=f"Web Remote: open SyncMaster_WebRemote.html → enter {self.local_ip}:5000",
                  foreground="#444455", background="#1a1a1a",
                  font=("Segoe UI", 7), wraplength=480).pack(anchor=tk.W)

        # Log
        ttk.Label(self.root, text="Log:", foreground="#555555",
                  background="#1a1a1a", font=("Segoe UI", 8)).pack(anchor=tk.W, padx=16)
        self.log_area = scrolledtext.ScrolledText(
            self.root, height=7, bg="#0d0d0d", fg="#66bb6a",
            font=("Consolas", 8), borderwidth=0, insertbackground="#66bb6a")
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 8))

        # Footer
        ttk.Label(self.root, text="Mocap Lab × Antigravity  |  Saint's Motion Capture Tools",
                  foreground="#333333", background="#1a1a1a",
                  font=("Segoe UI", 8)).pack(pady=(0, 8))

    def _add_target(self, parent, key, label, default_ip, default_port, default_on=True):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=default_on)
        chk = ttk.Checkbutton(row, text=label, variable=var, width=18)
        chk.pack(side=tk.LEFT)

        ip_var = tk.StringVar(value=default_ip)
        ttk.Entry(row, textvariable=ip_var, width=14).pack(side=tk.LEFT, padx=4)

        port_var = tk.StringVar(value=default_port)
        ttk.Entry(row, textvariable=port_var, width=7).pack(side=tk.LEFT)

        self.targets[key] = {"enabled": var, "ip": ip_var, "port": port_var}

    def _add_motive(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Motive", variable=var, width=9).pack(side=tk.LEFT)

        ver_var = tk.StringVar(value="2.x")
        ttk.Combobox(row, textvariable=ver_var, values=["2.x", "3.x"],
                     state="readonly", width=4).pack(side=tk.LEFT, padx=(0, 4))

        ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(row, textvariable=ip_var, width=14).pack(side=tk.LEFT, padx=4)

        port_var = tk.StringVar(value="1512")
        ttk.Entry(row, textvariable=port_var, width=7).pack(side=tk.LEFT)

        self.targets["motive"] = {"enabled": var, "ip": ip_var, "port": port_var, "version": ver_var}

    def _add_obs(self, parent):
        row1 = ttk.Frame(parent)
        row1.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="OBS Studio", variable=var, width=18).pack(side=tk.LEFT)

        ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(row1, textvariable=ip_var, width=14).pack(side=tk.LEFT, padx=4)

        port_var = tk.StringVar(value="4455")
        ttk.Entry(row1, textvariable=port_var, width=7).pack(side=tk.LEFT)

        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(row2, text="Password:", width=18, anchor=tk.E).pack(side=tk.LEFT)
        pw_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=pw_var, show="*", width=22).pack(side=tk.LEFT, padx=4)

        self.targets["obs"] = {"enabled": var, "ip": ip_var, "port": port_var, "password": pw_var}

    # ── Config ────────────────────────────────────────────────────────────────
    def _load_config(self):
        try:
            if os.path.exists(self.config_path):
                with open(self.config_path, "r") as f:
                    cfg = json.load(f)
                for key, t in self.targets.items():
                    if key in cfg:
                        t["ip"].set(cfg[key].get("ip", t["ip"].get()))
                        t["port"].set(cfg[key].get("port", t["port"].get()))
                        t["enabled"].set(cfg[key].get("enabled", True))
                        if key == "motive" and "version" in cfg[key]:
                            t["version"].set(cfg[key]["version"])
                        if key == "obs" and "password" in cfg[key]:
                            t["password"].set(cfg[key]["password"])
                if "take_name" in cfg:
                    self.take_entry.delete(0, tk.END)
                    self.take_entry.insert(0, cfg["take_name"])
        except Exception as e:
            self.log(f"Config load error: {e}")

    def _save_config(self):
        try:
            cfg = {"take_name": self.take_entry.get()}
            for key, t in self.targets.items():
                entry = {"ip": t["ip"].get(), "port": t["port"].get(),
                         "enabled": t["enabled"].get()}
                if key == "motive":
                    entry["version"] = t["version"].get()
                if key == "obs":
                    entry["password"] = t["password"].get()
                cfg[key] = entry
            with open(self.config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            self.log(f"Config save error: {e}")

    # ── Timer ─────────────────────────────────────────────────────────────────
    def _start_timer(self):
        self.rec_start_time = time.time()
        self.elapsed_sec = 0

        def tick():
            while self.is_recording:
                self.elapsed_sec = int(time.time() - self.rec_start_time)
                h = self.elapsed_sec // 3600
                m = (self.elapsed_sec % 3600) // 60
                s = self.elapsed_sec % 60
                txt = f"{h:02d}:{m:02d}:{s:02d}"
                self.root.after(0, lambda t=txt: (
                    self.timer_label.configure(text=t, style="RecTimer.TLabel")
                ))
                time.sleep(1)

        self.timer_thread = threading.Thread(target=tick, daemon=True)
        self.timer_thread.start()

    def _stop_timer(self):
        self.timer_label.configure(style="Timer.TLabel")

    # ── Trigger ───────────────────────────────────────────────────────────────
    def _trigger(self, action, take_name_override=""):
        if action == "start" and self.is_recording:
            return
        if action == "stop" and not self.is_recording:
            return

        take = take_name_override or self.take_entry.get().strip()
        if action == "start":
            ts = time.strftime("%Y%m%d_%H%M%S")
            self.current_take = "{}_{}" .format(take, ts) if take else "Master_Take_{}".format(ts)
            self.is_recording = True
            self.status_label.configure(text="⏺ REC: {}".format(self.current_take))
            self._start_timer()
            self.log("=== START RECORD: {} ===".format(self.current_take))
            self._send_all("start", self.current_take)  # pass take name
            self._save_config()
        else:
            self.is_recording = False
            self._stop_timer()
            self.status_label.configure(text="Idle")
            self.log("=== STOP RECORD ===")
            self._send_all("stop")  # no take name needed for stop
            self.timer_label.configure(text="00:00:00")
            self.elapsed_sec = 0

    def _send_all(self, cmd, take_name=""):
        """Dispatch to all enabled targets in separate threads (non-blocking)."""
        t = self.targets

        def dispatch():
            if t["mobu"]["enabled"].get():
                send_mobu(t["mobu"]["ip"].get(), t["mobu"]["port"].get(),
                          cmd, self.log, take_name)  # pass take name
            if t["motive"]["enabled"].get():
                send_motive(t["motive"]["ip"].get(), t["motive"]["port"].get(),
                            cmd, t["motive"]["version"].get(), self.log, take_name)
            if t["obs"]["enabled"].get():
                send_obs(t["obs"]["ip"].get(), t["obs"]["port"].get(),
                         t["obs"]["password"].get(), cmd, self.log)
            if t["ue5"]["enabled"].get():
                send_ue5(t["ue5"]["ip"].get(), t["ue5"]["port"].get(), cmd, self.log)
            if t["warudo"]["enabled"].get():
                send_warudo(t["warudo"]["ip"].get(), t["warudo"]["port"].get(), cmd, self.log)

        threading.Thread(target=dispatch, daemon=True).start()


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = SyncMasterApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._save_config(), root.destroy()))
    root.mainloop()
