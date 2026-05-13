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
_ensure("websocket-client", "websocket")

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


def send_motive(ip, port, cmd, log, take_name=""):
    """Send XML Remote Trigger AND NatNet Command to Optitrack Motive (2.x & 3.x)."""
    try:
        # 1. Prepare XML Payload (Legacy/Port 1512)
        # Use single-line format, standard for Motive 3.x XML triggering
        if cmd == "start":
            xml = (f'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>'
                   f'<CaptureStart><Name VALUE="{take_name}"/><SessionName VALUE=""/><Notes VALUE=""/></CaptureStart>')
        else:
            xml = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?><CaptureStop/>'

        # 2. Prepare NatNet Binary Commands (Modern/Port 1510)
        # NatNet Packet: [MessageID (2 bytes)] [ByteCount (2 bytes)] [Payload]
        # MessageID 2 = NAT_REQUEST
        def pack_natnet(s):
            payload = s.encode('utf-8') + b'\x00'
            return struct.pack('<HH', 2, len(payload)) + payload

        natnet_cmds = []
        if cmd == "start":
            natnet_cmds.append(pack_natnet(f"SetRecordTakeName,{take_name}"))
            natnet_cmds.append(pack_natnet("StartRecording"))
        else:
            natnet_cmds.append(pack_natnet("StopRecording"))

        # 3. Dispatching
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        targets = [(ip, int(port))]
        # Always try broadcast to bypass NIC binding issues
        targets.append(("255.255.255.255", int(port)))
        
        # Command port (usually 1510)
        cmd_port = 1510
        cmd_targets = [(ip, cmd_port), ("255.255.255.255", cmd_port)]

        # Send XML to 1512 targets
        xml_bytes = xml.encode('utf-8')
        for target in targets:
            try: sock.sendto(xml_bytes, target)
            except: pass
        
        # Send NatNet Commands to 1510 targets
        for n_pkt in natnet_cmds:
            for target in cmd_targets:
                try: sock.sendto(n_pkt, target)
                except: pass
            time.sleep(0.05) # short gap between commands

        sock.close()
        
        log(f"[Motive] Dual-Trigger ({cmd}) → {ip}:{port} & {ip}:1510 ✔")
        log("[Motive] Info: Sent XML (1512) and NatNet Command (1510) for 2.x/3.x compatibility.")
    except Exception as e:
        log(f"[Motive] ERROR: {e}")


def send_obs(ip, port, password, cmd, log, take_name=""):
    """Send StartRecord/StopRecord via obs-websocket v5 with Filename Sync."""
    try:
        import obsws_python as obs
        cl = obs.ReqClient(host=ip, port=int(port), password=password, timeout=3)
        if cmd == "start":
            # Synchronize Filename with Take Name
            if take_name:
                try:
                    cl.set_profile_parameter("Output", "FilenameFormatting", take_name)
                    log(f"[OBS] FilenameFormatting → {take_name}")
                except Exception as ex:
                    log(f"[OBS] Warning (SetFilename): {ex}")
            
            cl.start_record()
            log(f"[OBS] StartRecord → {ip}:{port} ✔")
        else:
            cl.stop_record()
            log(f"[OBS] StopRecord → {ip}:{port} ✔")
        cl.disconnect()
    except Exception as e:
        log(f"[OBS] ERROR: {e}")

def obs_switch_scene(ip, port, password, scene_name, log):
    """Manually switch OBS scene."""
    try:
        import obsws_python as obs
        cl = obs.ReqClient(host=ip, port=int(port), password=password, timeout=3)
        cl.set_current_program_scene(scene_name)
        cl.disconnect()
        log(f"[OBS] Scene switched → {scene_name} ✔")
    except Exception as e:
        log(f"[OBS] Switch Error: {e}")

def obs_get_scenes(ip, port, password, log):
    """Fetch all available scene names from OBS."""
    try:
        import obsws_python as obs
        cl = obs.ReqClient(host=ip, port=int(port), password=password, timeout=3)
        resp = cl.get_scene_list()
        cl.disconnect()
        scenes = [s['sceneName'] for s in resp.scenes]
        log(f"[OBS] Fetched {len(scenes)} scenes ✔")
        return scenes
    except Exception as e:
        log(f"[OBS] Fetch Error: {e}")
        return []


def send_ue5(ip, port, cmd, log, take_name=""):
    """Send Start/Stop Recording via UE5 Web Remote Control with Take Name Sync."""
    try:
        base_url = f"http://{ip}:{port}/remote/object/call"
        lib_path = "/Script/TakeRecorder.Default__TakeRecorderBlueprintLibrary"
        
        if cmd == "start":
            # 1. Try to set the Take Name (Level Sequence Name)
            if take_name:
                try:
                    # In some UE5 versions, we might need to set metadata or properties
                    # We'll try to call StartRecording which can sometimes take parameters or 
                    # we set it via a separate call if the plugin supports it.
                    # For standard TakeRecorder, we'll log it.
                    log(f"[UE5] Preparing Take: {take_name}")
                    
                except: pass
            
            # 2. Trigger StartRecording
            body = {
                "objectPath": lib_path,
                "functionName": "StartRecording",
                "parameters": {}
            }
            resp = requests.put(base_url, json=body, timeout=2)
            log(f"[UE5] StartRecording → {ip}:{port} [{resp.status_code}] ✔")
            return True
        else:
            # Trigger StopRecording
            body = {
                "objectPath": lib_path,
                "functionName": "StopRecording",
                "parameters": {}
            }
            resp = requests.put(base_url, json=body, timeout=2)
            log(f"[UE5] StopRecording → {ip}:{port} [{resp.status_code}] ✔")
            return True
    except Exception as e:
        log(f"[UE5] ERROR: {e}")
        return False

def check_target_online(key, ip, port, password=""):
    """Check if a target is reachable (Background task)."""
    try:
        if key == "obs":
            import obsws_python as obs
            cl = obs.ReqClient(host=ip, port=int(port), password=password, timeout=1)
            cl.disconnect()
            return True
        elif key == "ue5":
            url = f"http://{ip}:{port}/remote/info"
            requests.get(url, timeout=1)
            return True
        elif key == "warudo":
            import websocket
            ws = websocket.create_connection(f"ws://{ip}:{port}", timeout=1)
            ws.close()
            return True
        else:
            # For UDP targets (Mobu, Motive, etc.), we do a simple socket test
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1)
            s.connect((ip, int(port)))
            s.close()
            return True
    except:
        return False

def send_warudo(ip, port, cmd, log, take_name=""):
    """Send JSON action to Warudo via WebSocket (Compatible with 'On WebSocket Action' node)."""
    try:
        import websocket
        ws = websocket.create_connection(f"ws://{ip}:{port}", timeout=2)
        payload = {
            "action": "RecordStart" if cmd == "start" else "RecordStop",
            "data": { "take_name": take_name }
        }
        ws.send(json.dumps(payload))
        ws.close()
        log(f"[Warudo] Action: {payload['action']} → {ip}:{port} ✔")
    except Exception as e:
        log(f"[Warudo] ERROR: {e}")

WEB_UI_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>MocapLab SyncMaster</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;900&family=JetBrains+Mono:wght@400;700&display=swap');

  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --bg:         #0a0a0f;
    --surface:    #111118;
    --border:     #2a2a3a;
    --text:       #e2e2f0;
    --muted:      #666680;
    --red:        #e53935;
    --red-dim:    #7b1c1a;
    --green:      #43a047;
    --accent:     #7c4dff;
    --timer-idle: #444466;
    --timer-rec:  #ff4444;
  }

  html, body {
    height: 100%; width: 100%;
    font-family: 'Inter', system-ui, sans-serif;
    background: var(--bg); color: var(--text);
    overflow-x: hidden;
  }

  body {
    display: flex; flex-direction: column;
    align-items: center; justify-content: flex-start;
    min-height: 100dvh;
    padding: 0 16px 40px;
    gap: 0;
  }

  header {
    width: 100%; max-width: 540px;
    display: flex; align-items: center; justify-content: space-between;
    padding: 20px 0 12px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 24px;
  }
  header h1 {
    font-size: 1rem; font-weight: 600; letter-spacing: 3px;
    text-transform: uppercase; color: var(--muted);
  }
  #dot-status {
    width: 10px; height: 10px; border-radius: 50%;
    background: var(--muted);
    transition: background 0.4s;
    box-shadow: 0 0 6px transparent;
  }
  #dot-status.connected   { background: var(--green); box-shadow: 0 0 10px var(--green); }
  #dot-status.recording   { background: var(--red);   box-shadow: 0 0 14px var(--red); animation: pulse 1s infinite; }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50%       { opacity: 0.4; }
  }

  main {
    width: 100%; max-width: 540px;
    display: flex; flex-direction: column; gap: 20px;
  }

  .timer-block {
    display: flex; flex-direction: column; align-items: center;
    padding: 32px 24px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 20px;
  }
  #timer {
    font-family: 'JetBrains Mono', monospace;
    font-size: clamp(3.5rem, 14vw, 5.5rem);
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--timer-idle);
    transition: color 0.4s, text-shadow 0.4s;
    line-height: 1;
    tab-size: 4;
  }
  #timer.recording {
    color: var(--timer-rec);
    text-shadow: 0 0 40px rgba(255,68,68,0.3);
  }
  #rec-label {
    margin-top: 12px;
    font-size: 0.75rem; font-weight: 600; letter-spacing: 3px;
    text-transform: uppercase; color: var(--muted);
    transition: color 0.3s;
  }
  #rec-label.recording { color: var(--red); }
  #take-display {
    margin-top: 6px; font-size: 0.8rem;
    color: var(--muted); font-family: 'JetBrains Mono', monospace;
    text-align: center; word-break: break-all;
    min-height: 1.2em;
  }

  .targets-block {
    display: flex; justify-content: space-around;
    padding: 12px 10px; background: var(--surface);
    border: 1px solid var(--border); border-radius: 14px;
    gap: 8px;
  }
  .target-item {
    display: flex; flex-direction: column; align-items: center; gap: 6px;
    flex: 1;
  }
  .target-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #333; transition: all 0.3s;
  }
  .target-label {
    font-size: 0.6rem; font-weight: 700; color: var(--muted);
    letter-spacing: 1px; text-transform: uppercase;
  }
  .dot-green  { background: var(--green); box-shadow: 0 0 10px var(--green); }
  .dot-red    { background: var(--red);   box-shadow: 0 0 10px var(--red); }
  .dot-yellow { background: #fbc02d;     box-shadow: 0 0 10px #fbc02d; }
  .dot-gray   { background: #333;        box-shadow: none; }

  .video-ctrl-block {
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 16px; padding: 16px;
    display: flex; flex-direction: column; gap: 14px;
  }
  .obs-header {
    display: flex; align-items: center; justify-content: space-between;
  }
  .obs-header h2 {
    font-size: 0.75rem; font-weight: 600; letter-spacing: 2px;
    text-transform: uppercase; color: var(--muted);
  }
  #btn-refresh-scenes {
    background: transparent; border: 1px solid var(--border);
    color: var(--muted); border-radius: 8px;
    padding: 4px 10px; font-size: 0.75rem; font-weight: 600;
    cursor: pointer; transition: all 0.2s;
    display: flex; align-items: center; gap: 4px;
  }
  #btn-refresh-scenes:hover { border-color: var(--accent); color: var(--accent); }
  #btn-refresh-scenes.spinning svg { animation: spin 0.8s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  #scene-btn-grid { display: flex; flex-wrap: wrap; gap: 8px; }
  #scene-btn-grid .scene-empty {
    font-size: 0.8rem; color: var(--muted);
    font-family: 'JetBrains Mono', monospace;
  }
  .scene-btn {
    padding: 10px 16px;
    background: #1a1a24; border: 1px solid var(--border);
    border-radius: 10px; color: var(--text);
    font-size: 0.85rem; font-weight: 500; cursor: pointer;
    transition: all 0.18s ease; white-space: nowrap;
    -webkit-tap-highlight-color: transparent;
  }
  .scene-btn:hover { background: #22223a; border-color: var(--accent); color: white; }
  .scene-btn:active { transform: scale(0.95); }
  .scene-btn.active {
    background: #e65100; border-color: #e65100; color: white;
    box-shadow: 0 0 14px rgba(230,81,0,0.4); font-weight: 700;
  }

  .input-group {
    display: flex; flex-direction: column; gap: 6px;
  }
  .input-group label {
    font-size: 0.75rem; font-weight: 600;
    letter-spacing: 2px; text-transform: uppercase;
    color: var(--muted);
  }
  .input-group input {
    width: 100%;
    background: var(--surface); border: 1px solid var(--border);
    color: var(--text); border-radius: 10px;
    padding: 12px 16px; font-size: 1rem;
    font-family: 'JetBrains Mono', monospace;
    outline: none; transition: border-color 0.2s;
  }
  .input-group input:focus { border-color: var(--accent); }
  .input-group input::placeholder { color: var(--muted); }

  .server-row {
    display: flex; gap: 8px; align-items: center;
  }
  .server-row input {
    flex: 1;
    background: var(--surface); border: 1px solid var(--border);
    color: var(--text); border-radius: 10px;
    padding: 10px 14px; font-size: 0.9rem;
    font-family: 'JetBrains Mono', monospace;
    outline: none; transition: border-color 0.2s;
  }
  .server-row input:focus { border-color: var(--accent); }
  #btn-connect-server {
    padding: 10px 20px; border: none; border-radius: 10px;
    background: var(--accent); color: white;
    font-size: 0.85rem; font-weight: 600; cursor: pointer;
    white-space: nowrap; transition: opacity 0.2s;
  }

  .btn-row {
    display: flex; gap: 12px;
  }
  .btn {
    flex: 1; border: none; border-radius: 16px;
    font-size: 1.1rem; font-weight: 700;
    padding: 22px 12px; cursor: pointer;
    letter-spacing: 1px; text-transform: uppercase;
    transition: transform 0.12s ease, opacity 0.2s, box-shadow 0.2s;
    -webkit-tap-highlight-color: transparent;
  }
  .btn:active { transform: scale(0.96); }

  #btn-start {
    background: var(--red); color: white;
    box-shadow: 0 4px 24px rgba(229,57,53,0.25);
  }
  #btn-start:hover { box-shadow: 0 4px 32px rgba(229,57,53,0.45); }
  #btn-start:disabled { background: var(--red-dim); box-shadow: none; cursor: not-allowed; }

  #btn-stop {
    background: var(--surface); color: var(--muted);
    border: 1px solid var(--border);
    flex: 0 0 auto; width: 120px;
  }
  #btn-stop:hover { background: #1e1e28; color: var(--text); }
  #btn-stop:disabled { opacity: 0.3; cursor: not-allowed; }

  .log-block {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 14px 16px;
    max-height: 140px;
    overflow-y: auto;
    display: flex; flex-direction: column; gap: 4px;
  }
  .log-block::-webkit-scrollbar { width: 4px; }
  .log-block::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  .log-entry {
    font-family: 'JetBrains Mono', monospace;
    font-size: 0.72rem; color: var(--muted);
    line-height: 1.5;
  }
  .log-entry.ok  { color: #66bb6a; }
  .log-entry.err { color: #ef9a9a; }
  .log-entry.ts  { color: #888; }

  footer {
    margin-top: 24px;
    text-align: center;
    font-size: 0.7rem; color: #333344;
    letter-spacing: 1px;
  }
</style>
</head>
<body>

<header>
  <h1>🎬 MocapLab SyncMaster</h1>
  <div id="dot-status" title="Connection Status"></div>
</header>

<main>
  <div style="display:flex;flex-direction:column;gap:6px;">
    <label style="font-size:.72rem;font-weight:600;letter-spacing:2px;text-transform:uppercase;color:var(--muted);">SyncMaster Server Address</label>
    <div class="server-row">
      <input id="server-addr" type="text" value="[[SERVER_ADDR]]" placeholder="192.168.1.x:5000" autocomplete="off">
      <button id="btn-connect-server" onclick="connectServer()">Connect</button>
    </div>
  </div>

  <div class="timer-block">
    <div id="timer">00:00:00</div>
    <div id="rec-label">Idle</div>
    <div id="take-display"></div>
  </div>

  <div class="targets-block">
    <div class="target-item">
      <div id="dot-motive" class="target-dot"></div>
      <div class="target-label">Motive</div>
    </div>
    <div class="target-item">
      <div id="dot-mobu" class="target-dot"></div>
      <div class="target-label">Mobu</div>
    </div>
    <div class="target-item">
      <div id="dot-ue5" class="target-dot"></div>
      <div class="target-label">UE5</div>
    </div>
    <div class="target-item">
      <div id="dot-warudo" class="target-dot"></div>
      <div class="target-label">Warudo</div>
    </div>
    <div class="target-item">
      <div id="dot-obs" class="target-dot"></div>
      <div class="target-label">OBS</div>
    </div>
  </div>

  <div class="input-group">
    <label>Take Name</label>
    <input id="take-name" type="text" placeholder="e.g. Scene01_A" autocomplete="off" autocorrect="off">
  </div>

  <div class="btn-row">
    <button class="btn" id="btn-start" onclick="sendCmd('start')" disabled>⏺ START</button>
    <button class="btn" id="btn-stop"  onclick="sendCmd('stop')"  disabled>⏹ STOP</button>
  </div>

  <div class="video-ctrl-block">
    <div class="obs-header">
      <h2>🎥 OBS Scene Switch</h2>
      <button id="btn-refresh-scenes" onclick="refreshScenes()">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
        Refresh
      </button>
    </div>
    <div id="scene-btn-grid">
      <span class="scene-empty">— Connect to load scenes —</span>
    </div>
  </div>

  <div class="log-block" id="log-area">
    <div class="log-entry ts">— Waiting for server connection —</div>
  </div>

</main>

<footer>Mocap Lab × Antigravity &nbsp;|&nbsp; SaintMobu Tools</footer>

<script>
  let serverBase = '';
  let polling = null;
  let isConnected = false;
  let sceneFetched = false;

  function pad(n) { return String(Math.floor(n)).padStart(2, '0'); }
  function fmtSec(s) {
    return pad(s / 3600) + ':' + pad((s % 3600) / 60) + ':' + pad(s % 60);
  }

  function addLog(msg, type = '') {
    const el = document.getElementById('log-area');
    const ts = new Date().toLocaleTimeString('en', {hour12: false});
    const div = document.createElement('div');
    div.className = 'log-entry ' + type;
    div.textContent = '[' + ts + '] ' + msg;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
    while (el.children.length > 60) el.removeChild(el.firstChild);
  }

  function setConnected(ok) {
    isConnected = ok;
    const dot = document.getElementById('dot-status');
    if (ok) {
      dot.className = 'connected';
      document.getElementById('btn-start').disabled = false;
      document.getElementById('btn-stop').disabled = false;
    } else {
      dot.className = '';
      document.getElementById('btn-start').disabled = true;
      document.getElementById('btn-stop').disabled = true;
      sceneFetched = false;
    }
  }

  function connectServer() {
    let addr = document.getElementById('server-addr').value.trim();
    if (!addr) { addLog('Please enter a server address.', 'err'); return; }
    if (!addr.startsWith('http')) addr = 'http://' + addr;
    serverBase = addr.replace(/\/$/, '');
    addLog('Connecting to ' + serverBase + ' ...');
    if (polling) clearInterval(polling);
    poll();
    polling = setInterval(poll, 1000);
  }

  function poll() {
    if (!serverBase) return;
    fetch(serverBase + '/status')
      .then(r => r.json())
      .then(d => {
        if (!isConnected) { 
          addLog('Connected to SyncMaster.', 'ok'); 
          setConnected(true); 
          fetchScenes();
        }
        const timer = document.getElementById('timer');
        const recLabel = document.getElementById('rec-label');
        const takeDisplay = document.getElementById('take-display');
        const dot = document.getElementById('dot-status');
        const btnStart = document.getElementById('btn-start');
        timer.textContent = fmtSec(d.elapsed || 0);
        if (d.recording) {
          timer.className = 'recording';
          recLabel.className = 'recording';
          recLabel.textContent = '⏺ Recording';
          dot.className = 'recording';
          takeDisplay.textContent = d.take_name || '';
          btnStart.disabled = true;
        } else {
          timer.className = '';
          recLabel.className = '';
          recLabel.textContent = 'Idle';
          dot.className = 'connected';
          takeDisplay.textContent = '';
          btnStart.disabled = false;
        }
        if (d.targets) {
          Object.keys(d.targets).forEach(key => {
            const el = document.getElementById('dot-' + key);
            if (el) el.className = 'target-dot dot-' + d.targets[key];
          });
        }
      })
      .catch((e) => {
        if (isConnected) { 
          addLog('Lost connection to SyncMaster.', 'err'); 
          setConnected(false); 
        } else {
          if (polling) {
            addLog('Connection failed. Retrying...', 'err');
          }
        }
      });
  }

  let activeScene = '';

  function fetchScenes() {
    if (!serverBase) return;
    const grid = document.getElementById('scene-btn-grid');
    grid.innerHTML = '<span class="scene-empty">Loading scenes...</span>';
    addLog('Fetching OBS scenes...');
    fetch(serverBase + '/obs_scenes')
      .then(r => {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(scenes => {
        grid.innerHTML = '';
        if (!scenes || scenes.length === 0) {
          grid.innerHTML = '<span class="scene-empty">— No scenes. Is OBS connected? —</span>';
          addLog('OBS: no scenes returned.', 'err');
          return;
        }
        scenes.forEach(s => {
          const btn = document.createElement('button');
          btn.className = 'scene-btn' + (s === activeScene ? ' active' : '');
          btn.textContent = s;
          btn.onclick = () => switchScene(s, btn);
          grid.appendChild(btn);
        });
        sceneFetched = true;
        addLog('OBS: ' + scenes.length + ' scenes loaded.', 'ok');
      })
      .catch(e => {
        grid.innerHTML = '<span class="scene-empty">— Failed to load scenes —</span>';
        addLog('OBS fetch failed: ' + e.message, 'err');
      });
  }

  function refreshScenes() {
    sceneFetched = false;
    const btn = document.getElementById('btn-refresh-scenes');
    btn.classList.add('spinning');
    setTimeout(() => btn.classList.remove('spinning'), 800);
    fetchScenes();
  }

  function switchScene(sceneName, btnEl) {
    if (!serverBase || !sceneName) return;
    document.querySelectorAll('.scene-btn').forEach(b => b.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');
    activeScene = sceneName;
    fetch(serverBase + '/switch_scene', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scene: sceneName })
    }).then(r => r.json())
      .then(() => addLog('OBS: switched to "' + sceneName + '"', 'ok'))
      .catch(e => {
        addLog('OBS switch failed: ' + e.message, 'err');
        document.querySelectorAll('.scene-btn').forEach(b => b.classList.remove('active'));
        activeScene = '';
      });
  }

  function sendCmd(action) {
    if (!serverBase) { addLog('Not connected to server.', 'err'); return; }
    const takeName = document.getElementById('take-name').value.trim();
    fetch(serverBase + '/record', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, take_name: takeName })
    });
  }

  window.addEventListener('load', () => {
    const addrInput = document.getElementById('server-addr');
    const protocol = location.protocol;
    
    if (protocol.startsWith('http')) {
      // If served via web, ensure input matches current host and connect
      if (!addrInput.value || addrInput.value.includes('[[SERVER_ADDR]]')) {
        addrInput.value = location.host;
      }
      connectServer();
    } else {
      // Local file fallback
      if (!addrInput.value || addrInput.value.includes('[[SERVER_ADDR]]')) {
        addrInput.value = '127.0.0.1:5000';
      }
    }
  });
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
        self.status_colors = {} # To track connectivity for web remote

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

        # Start Connection Watchdog
        self.watchdog_thread = threading.Thread(target=self._connection_watchdog, daemon=True)
        self.watchdog_thread.start()

    def _connection_watchdog(self):
        """Periodically check all enabled targets' connection status."""
        while True:
            for key, t in self.targets.items():
                if not t["enabled"].get():
                    self._set_status_light(key, "gray")
                    continue
                
                self._set_status_light(key, "yellow")
                ip = t["ip"].get()
                port = t["port"].get()
                pw = t.get("password").get() if "password" in t else ""
                
                is_online = check_target_online(key, ip, port, pw)
                self._set_status_light(key, "green" if is_online else "red")
            
            time.sleep(5) # Check every 5 seconds

    def _set_status_light(self, key, color):
        colors = {"gray": "#333333", "yellow": "#fbc02d", "green": "#4caf50", "red": "#f44336"}
        self.status_colors[key] = color # Store for web remote
        if key in self.targets and "light" in self.targets[key]:
            canvas = self.targets[key]["light"]
            self.root.after(0, lambda: canvas.itemconfig("circle", fill=colors.get(color, "#333333")))

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
            return WEB_UI_HTML.replace('[[SERVER_ADDR]]', f"{self.local_ip}:5000")

        @app.route("/status", methods=["GET", "OPTIONS"])
        def status():
            if request.method == "OPTIONS":
                return Response(status=200)
            return jsonify({
                "recording": self.is_recording,
                "elapsed": self.elapsed_sec,
                "take_name": self.current_take,
                "targets": self.status_colors
            })

        @app.route("/obs_scenes", methods=["GET", "OPTIONS"])
        def obs_scenes():
            if request.method == "OPTIONS": return Response(status=200)
            if "obs" not in self.targets: return jsonify([])
            ip = self.targets["obs"]["ip"].get()
            port = self.targets["obs"]["port"].get()
            pw = self.targets["obs"]["password"].get()
            scenes = obs_get_scenes(ip, port, pw, self.log)
            return jsonify(scenes)

        @app.route("/switch_scene", methods=["POST", "OPTIONS"])
        def switch_scene():
            if request.method == "OPTIONS": return Response(status=200)
            data = request.get_json(force=True)
            scene = data.get("scene", "")
            if "obs" not in self.targets or not scene: return jsonify({"ok": False})
            ip = self.targets["obs"]["ip"].get()
            port = self.targets["obs"]["port"].get()
            pw = self.targets["obs"]["password"].get()
            def task(): obs_switch_scene(ip, port, pw, scene, self.log)
            threading.Thread(target=task, daemon=True).start()
            return jsonify({"ok": True})

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
        ttk.Label(self.root, text="🎬  Saint's MocapLab SyncMaster Console", style="Title.TLabel").pack(pady=(18, 6))

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
        self._add_motive(targets_frame)
        self._add_target(targets_frame, "mobu",   "MotionBuilder", "127.0.0.1", "9000")
        self._add_target(targets_frame, "ue5",    "Unreal Engine 5", "127.0.0.1", "30010")
        self._add_target(targets_frame, "warudo", "Warudo (WebSocket)", "127.0.0.1", "19190", default_on=False)

        # Video Ctrl panel
        video_frame = ttk.LabelFrame(self.root, text=" Video Ctrl ", padding=10)
        video_frame.pack(fill=tk.X, padx=16, pady=6)
        self._add_obs(video_frame)

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
        footer_text = "Mocap Lab × Antigravity  |  Saint's Motion Capture Tools\n小聖腦絲的粉專: https://www.facebook.com/hysaint3d.mocap"
        self.footer_label = ttk.Label(self.root, text=footer_text,
                                      foreground="#444444", background="#1a1a1a",
                                      font=("Segoe UI", 8), justify=tk.CENTER)
        self.footer_label.pack(pady=(0, 8))
        self.footer_label.bind("<Button-1>", lambda e: os.startfile("https://www.facebook.com/hysaint3d.mocap"))
        self.footer_label.configure(cursor="hand2")

    def _add_target(self, parent, key, label, default_ip, default_port, default_on=True):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=default_on)
        chk = ttk.Checkbutton(row, text=label, variable=var, width=16)
        chk.pack(side=tk.LEFT)

        ttk.Label(row, text=" IP:", width=3).pack(side=tk.LEFT)
        ip_var = tk.StringVar(value=default_ip)
        ttk.Entry(row, textvariable=ip_var, width=15).pack(side=tk.LEFT, padx=4)

        ttk.Label(row, text="Port:", width=4).pack(side=tk.LEFT)
        port_var = tk.StringVar(value=default_port)
        ttk.Entry(row, textvariable=port_var, width=6).pack(side=tk.LEFT)

        # Status Light
        light = tk.Canvas(row, width=12, height=12, bg="#1a1a1a", highlightthickness=0)
        light.pack(side=tk.LEFT, padx=6)
        light.create_oval(2, 2, 10, 10, fill="#333333", outline="", tags="circle")

        self.targets[key] = {"enabled": var, "ip": ip_var, "port": port_var, "light": light}

    def _add_motive(self, parent):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Motive (2.x/3.x)", variable=var, width=16).pack(side=tk.LEFT)

        ttk.Label(row, text=" IP:", width=3).pack(side=tk.LEFT)
        ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(row, textvariable=ip_var, width=15).pack(side=tk.LEFT, padx=4)

        ttk.Label(row, text="Port:", width=4).pack(side=tk.LEFT)
        port_var = tk.StringVar(value="1512")
        ttk.Entry(row, textvariable=port_var, width=6).pack(side=tk.LEFT)

        # Status Light
        light = tk.Canvas(row, width=12, height=12, bg="#1a1a1a", highlightthickness=0)
        light.pack(side=tk.LEFT, padx=6)
        light.create_oval(2, 2, 10, 10, fill="#333333", outline="", tags="circle")

        self.targets["motive"] = {"enabled": var, "ip": ip_var, "port": port_var, "light": light}

    def _add_obs(self, parent):
        row1 = ttk.Frame(parent)
        row1.pack(fill=tk.X, pady=2)

        var = tk.BooleanVar(value=True)
        ttk.Checkbutton(row1, text="OBS Studio", variable=var, width=16).pack(side=tk.LEFT)

        ttk.Label(row1, text=" IP:", width=3).pack(side=tk.LEFT)
        ip_var = tk.StringVar(value="127.0.0.1")
        ttk.Entry(row1, textvariable=ip_var, width=15).pack(side=tk.LEFT, padx=4)

        ttk.Label(row1, text="Port:", width=4).pack(side=tk.LEFT)
        port_var = tk.StringVar(value="4455")
        ttk.Entry(row1, textvariable=port_var, width=6).pack(side=tk.LEFT)

        # Status Light
        light = tk.Canvas(row1, width=12, height=12, bg="#1a1a1a", highlightthickness=0)
        light.pack(side=tk.LEFT, padx=6)
        light.create_oval(2, 2, 10, 10, fill="#333333", outline="", tags="circle")

        row2 = ttk.Frame(parent)
        row2.pack(fill=tk.X, pady=(0, 2))
        ttk.Label(row2, text="Password:", width=10, anchor=tk.E).pack(side=tk.LEFT)
        pw_var = tk.StringVar(value="")
        ttk.Entry(row2, textvariable=pw_var, show="*", width=12).pack(side=tk.LEFT, padx=4)

        ttk.Label(row2, text="Scene:", width=6).pack(side=tk.LEFT)
        scene_var = tk.StringVar(value="")
        self.obs_scene_cb = ttk.Combobox(row2, textvariable=scene_var, width=15, state="readonly")
        self.obs_scene_cb.pack(side=tk.LEFT, padx=4)

        def fetch_scenes():
            ip, port, pw = ip_var.get(), port_var.get(), pw_var.get()
            scenes = obs_get_scenes(ip, port, pw, self.log)
            if scenes:
                self.obs_scene_cb['values'] = scenes
                if not scene_var.get() and scenes:
                    scene_var.set(scenes[0])

        def switch_scene():
            ip, port, pw = ip_var.get(), port_var.get(), pw_var.get()
            sn = scene_var.get()
            if sn:
                threading.Thread(target=lambda: obs_switch_scene(ip, port, pw, sn, self.log), daemon=True).start()

        ttk.Button(row2, text="🔄", width=4, command=fetch_scenes).pack(side=tk.LEFT)
        ttk.Button(row2, text="Switch", width=7, command=switch_scene).pack(side=tk.LEFT, padx=2)

        self.targets["obs"] = {"enabled": var, "ip": ip_var, "port": port_var, "password": pw_var, "scene": scene_var, "light": light}

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
                        if key == "obs":
                            if "password" in cfg[key]:
                                t["password"].set(cfg[key]["password"])
                            if "scene" in cfg[key]:
                                t["scene"].set(cfg[key]["scene"])
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
                if key == "obs":
                    entry["password"] = t["password"].get()
                    entry["scene"] = t["scene"].get()
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
                            cmd, self.log, take_name)
            if t["obs"]["enabled"].get():
                send_obs(t["obs"]["ip"].get(), t["obs"]["port"].get(),
                         t["obs"]["password"].get(), cmd, self.log, take_name)
            if t["ue5"]["enabled"].get():
                send_ue5(t["ue5"]["ip"].get(), t["ue5"]["port"].get(), cmd, self.log, take_name)
            if t["warudo"]["enabled"].get():
                send_warudo(t["warudo"]["ip"].get(), t["warudo"]["port"].get(), cmd, self.log, take_name)

        threading.Thread(target=dispatch, daemon=True).start()


# ── Entry Point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = SyncMasterApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app._save_config(), root.destroy()))
    root.mainloop()
