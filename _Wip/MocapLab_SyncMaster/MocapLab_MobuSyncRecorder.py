"""
MocapLab_SyncRecorder.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Centralized Synchronization Listener for MotionBuilder.
Receives OSC commands from MocapLab_SyncMaster_GUI and synchronizes
native recording with all SaintMobu Toolkits (LiveLink, VMC, VCam).

OSC Commands Supported:
  /RecordStart   - Create new Take, Start Native Record, Sync Toolkits
  /RecordStop    - Stop Record, Trim Take, Reset Toolkits
  /TakeName [s]  - Set name for the next recording
  /Play, /Stop   - Transport controls
  /GotoStart     - Reset timeline

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import socket
import struct
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# ── Global State ──────────────────────────────────────────────────────────────
if not hasattr(sys, "mobu_master_recording"):
    sys.mobu_master_recording = False

class MasterSyncState:
    def __init__(self):
        self.sock = None
        self.is_listening = False
        self.bind_ip = "0.0.0.0"
        self.port = 9000
        self.last_pkt_time = 0
        self.pkt_count = 0
        self.next_take_name = ""
        self.current_take_name = ""
        self.rec_start_time = 0.0
        self.log_lines = []
        self.last_ui_update = 0.0

# Cleanup old instances
if hasattr(sys, "mobu_master_sync_state") and sys.mobu_master_sync_state:
    if sys.mobu_master_sync_state.sock:
        try: sys.mobu_master_sync_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.mobu_master_sync_idle_func)
    except: pass

sys.mobu_master_sync_state = MasterSyncState()
g_state = sys.mobu_master_sync_state
g_ui = {}

# ── Log Helper ────────────────────────────────────────────────────────────────
def _log(msg):
    ts = time.strftime("%H:%M:%S")
    line = "[{}] {}".format(ts, msg)
    print(line)
    g_state.log_lines.append(line)
    if len(g_state.log_lines) > 200:
        g_state.log_lines = g_state.log_lines[-200:]
    # Immediate UI push if possible
    if "memo_log" in g_ui:
        try:
            g_ui["memo_log"].Text = "\n".join(g_state.log_lines[-30:])
        except: pass

# ── OSC Parser ────────────────────────────────────────────────────────────────
def parse_osc(data):
    try:
        addr_end = data.find(b'\x00')
        if addr_end == -1: return None, []
        address = data[:addr_end].decode('utf-8')

        type_start = (addr_end + 4) & ~0x03
        if type_start >= len(data) or data[type_start] != ord(','): return address, []

        type_end = data.find(b'\x00', type_start)
        if type_end == -1: return address, []
        type_tags = data[type_start+1:type_end].decode('utf-8')

        arg_start = (type_end + 4) & ~0x03
        args = []
        offset = arg_start
        for tag in type_tags:
            if offset >= len(data): break
            if tag == 'f':
                val = struct.unpack('>f', data[offset:offset+4])[0]
                args.append(val); offset += 4
            elif tag == 'i':
                val = struct.unpack('>i', data[offset:offset+4])[0]
                args.append(val); offset += 4
            elif tag == 's':
                s_end = data.find(b'\x00', offset)
                if s_end == -1: break
                val = data[offset:s_end].decode('utf-8')
                args.append(val)
                offset = (s_end + 4) & ~0x03
        return address, args
    except: return None, []

# ── Toolkit Detection ─────────────────────────────────────────────────────────
def _detect_active_toolkits():
    """Check which SaintMobu toolkits are currently active."""
    active = []

    # LiveLink Face
    try:
        states = getattr(sys, "g_livelink_states", None)
        if states and any(s.is_connected for s in states.values()):
            count = sum(1 for s in states.values() if s.is_connected)
            active.append("LiveLinkFace x{}".format(count))
    except: pass

    # VMC2Mobu
    try:
        states = getattr(sys, "vmc_multiactor_states", None)
        if states and any(s.is_connected for s in states.values()):
            count = sum(1 for s in states.values() if s.is_connected)
            active.append("VMC2Mobu x{}".format(count))
    except: pass

    # VCam
    try:
        vcam_state = getattr(sys, "mobu_vcam_toolkit_state", None)
        if vcam_state and vcam_state.get("camera") is not None:
            active.append("VCam")
    except: pass

    return active

# ── UI Update ─────────────────────────────────────────────────────────────────
def _refresh_ui():
    """Update all UI elements to reflect current state."""
    try:
        is_rec = sys.mobu_master_recording

        # Recording state label
        if "lbl_rec_state" in g_ui:
            if is_rec:
                g_ui["lbl_rec_state"].Caption = "🔴 RECORDING"
            else:
                g_ui["lbl_rec_state"].Caption = "⬜ IDLE"

        # Take name
        if "lbl_take" in g_ui:
            take_name = g_state.current_take_name or "(none)"
            g_ui["lbl_take"].Caption = "Take: " + take_name

        # Elapsed time
        if "lbl_elapsed" in g_ui:
            if is_rec and g_state.rec_start_time > 0:
                elapsed = int(time.time() - g_state.rec_start_time)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                g_ui["lbl_elapsed"].Caption = "Elapsed: {:02d}:{:02d}:{:02d}".format(h, m, s)
            else:
                g_ui["lbl_elapsed"].Caption = "Elapsed: 00:00:00"

        # Active toolkits
        if "lbl_toolkits" in g_ui:
            toolkits = _detect_active_toolkits()
            if toolkits:
                g_ui["lbl_toolkits"].Caption = "Active: " + "  |  ".join(toolkits)
            else:
                g_ui["lbl_toolkits"].Caption = "Active: (no toolkits detected)"

        # Connection status
        if "lbl_status" in g_ui:
            if g_state.is_listening:
                g_ui["lbl_status"].Caption = "Port {}  |  Pkts: {}".format(
                    g_state.port, g_state.pkt_count)
            else:
                g_ui["lbl_status"].Caption = "Not listening"

        # Log area
        if "memo_log" in g_ui and g_state.log_lines:
            try:
                g_ui["memo_log"].Text = "\n".join(g_state.log_lines[-30:])
            except: pass

    except: pass

# ── Actions ───────────────────────────────────────────────────────────────────
def StartGlobalRecord():
    ts = time.strftime("%Y%m%d_%H%M%S")
    take_name = g_state.next_take_name if g_state.next_take_name else "Master_Take_" + ts
    g_state.next_take_name = ""  # consume once

    # 1. Create New Take
    new_take = FBTake(take_name)
    FBSystem().Scene.Takes.append(new_take)
    FBSystem().CurrentTake = new_take
    end_t = FBTime(); end_t.SetSecondDouble(600.0)
    new_take.LocalTimeSpan = FBTimeSpan(FBTime(0), end_t)
    FBPlayerControl().LoopStop = end_t

    # 2. Set Global Sync Flag
    sys.mobu_master_recording = True
    g_state.current_take_name = take_name
    g_state.rec_start_time = time.time()

    # 3. Start Native Record & Play
    FBPlayerControl().GotoStart()
    FBPlayerControl().Record = True
    FBPlayerControl().Play()

    # 4. Log
    toolkits = _detect_active_toolkits()
    _log("=== RECORD START ===")
    _log("Take: " + take_name)
    _log("Toolkits: " + (", ".join(toolkits) if toolkits else "none detected"))
    _refresh_ui()

def StopGlobalRecord():
    # Wrap each call independently so one failure won't block the rest
    try: FBPlayerControl().Record = False
    except: pass
    try: FBPlayerControl().Stop()
    except: pass

    # Reset Global Flags
    sys.mobu_master_recording = False

    # Trim Take to actual end
    stop_time = FBSystem().LocalTime
    take = FBSystem().CurrentTake
    elapsed_str = "00:00:00"
    try:
        if take:
            start_time = take.LocalTimeSpan.GetStart()
            take.LocalTimeSpan = FBTimeSpan(start_time, stop_time)
            try: FBPlayerControl().LoopStop = stop_time
            except: pass
            if g_state.rec_start_time > 0:
                elapsed = int(time.time() - g_state.rec_start_time)
                h = elapsed // 3600
                m = (elapsed % 3600) // 60
                s = elapsed % 60
                elapsed_str = "{:02d}:{:02d}:{:02d}".format(h, m, s)
    except: pass

    # Log
    _log("=== RECORD STOP ===")
    _log("Take: {} | Duration: {}".format(g_state.current_take_name, elapsed_str))
    g_state.rec_start_time = 0.0
    _refresh_ui()

# ── UIIdle Loop ───────────────────────────────────────────────────────────────
def OnUIIdle(control, event):
    if not g_state.is_listening or not g_state.sock:
        return

    packets_processed = 0
    while packets_processed < 100:
        try:
            data, addr = g_state.sock.recvfrom(4096)
            address, args = parse_osc(data)

            if address == "/RecordStart":
                try: StartGlobalRecord()
                except Exception as e: _log("RecordStart ERROR: " + str(e))
            elif address == "/RecordStop":
                try: StopGlobalRecord()
                except Exception as e: _log("RecordStop ERROR: " + str(e))
            elif address == "/TakeName" and len(args) > 0:
                g_state.next_take_name = str(args[0])
                _log("TakeName set: " + g_state.next_take_name)
            elif address == "/Play":
                try: FBPlayerControl().Play()
                except: pass
                _log("Play")
            elif address == "/Stop":
                try: FBPlayerControl().Stop()
                except: pass
                _log("Stop")
            elif address == "/GotoStart":
                try: FBPlayerControl().GotoStart()
                except: pass
                _log("GotoStart")

            g_state.pkt_count += 1
            g_state.last_pkt_time = time.time()
            packets_processed += 1

        except BlockingIOError: break
        except socket.error: break
        except: break

    # Periodic UI refresh (0.5s interval)
    now = time.time()
    if now - g_state.last_ui_update > 0.5:
        g_state.last_ui_update = now
        _refresh_ui()

# ── UI Buttons ────────────────────────────────────────────────────────────────
def OnConnectClick(control, event):
    if not g_state.is_listening:
        try:
            g_state.port = int(g_ui["edit_port"].Text.strip())
            g_state.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_state.sock.bind((g_state.bind_ip, g_state.port))
            g_state.sock.setblocking(False)
            g_state.is_listening = True

            g_ui["btn_connect"].Caption = "Stop Listener"
            FBSystem().OnUIIdle.Add(OnUIIdle)
            sys.mobu_master_sync_idle_func = OnUIIdle
            _log("Listener started on port {}".format(g_state.port))
            _refresh_ui()
        except Exception as e:
            FBMessageBox("Error", "Failed to bind port: " + str(e), "OK")
    else:
        if g_state.sock:
            g_state.sock.close()
            g_state.sock = None
        g_state.is_listening = False
        g_ui["btn_connect"].Caption = "Start Master Listener"
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
        _log("Listener stopped.")
        _refresh_ui()

def OnClearLogClick(control, event):
    g_state.log_lines.clear()
    if "memo_log" in g_ui:
        try: g_ui["memo_log"].Text = ""
        except: pass

# ── UI Layout ─────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 300
    tool.StartSizeY = 520

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)

    layout = FBVBoxLayout()
    tool.SetControl("main", layout)

    def hdr(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl

    # Title
    layout.Add(hdr("MOCAP LAB SYNC RECORDER"), 25)

    # Port row
    lyt_port = FBHBoxLayout()
    lbl_p = FBLabel(); lbl_p.Caption = "OSC Port:"
    g_ui["edit_port"] = FBEdit(); g_ui["edit_port"].Text = "9000"
    lyt_port.Add(lbl_p, 70); lyt_port.Add(g_ui["edit_port"], 120)
    layout.Add(lyt_port, 28)

    # Connect button
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Start Master Listener"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    layout.Add(g_ui["btn_connect"], 35)

    # Divider
    layout.Add(hdr("STATUS"), 22)

    # Recording state
    g_ui["lbl_rec_state"] = FBLabel()
    g_ui["lbl_rec_state"].Caption = "⬜ IDLE"
    g_ui["lbl_rec_state"].Justify = FBTextJustify.kFBTextJustifyCenter
    g_ui["lbl_rec_state"].Style = FBTextStyle.kFBTextStyleBold
    layout.Add(g_ui["lbl_rec_state"], 28)

    # Take name
    g_ui["lbl_take"] = FBLabel()
    g_ui["lbl_take"].Caption = "Take: (none)"
    g_ui["lbl_take"].Justify = FBTextJustify.kFBTextJustifyCenter
    layout.Add(g_ui["lbl_take"], 22)

    # Elapsed time
    g_ui["lbl_elapsed"] = FBLabel()
    g_ui["lbl_elapsed"].Caption = "Elapsed: 00:00:00"
    g_ui["lbl_elapsed"].Justify = FBTextJustify.kFBTextJustifyCenter
    layout.Add(g_ui["lbl_elapsed"], 22)

    # Active toolkits
    g_ui["lbl_toolkits"] = FBLabel()
    g_ui["lbl_toolkits"].Caption = "Active: (no toolkits detected)"
    g_ui["lbl_toolkits"].Justify = FBTextJustify.kFBTextJustifyCenter
    layout.Add(g_ui["lbl_toolkits"], 22)

    # Log area
    layout.Add(hdr("LOG"), 22)

    g_ui["memo_log"] = FBMemo()
    g_ui["memo_log"].Text = ""
    layout.Add(g_ui["memo_log"], 200)

    # Clear log button
    g_ui["btn_clear"] = FBButton()
    g_ui["btn_clear"].Caption = "Clear Log"
    g_ui["btn_clear"].OnClick.Add(OnClearLogClick)
    layout.Add(g_ui["btn_clear"], 28)

    # Connection status (bottom)
    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "Not listening"
    g_ui["lbl_status"].Justify = FBTextJustify.kFBTextJustifyCenter
    layout.Add(g_ui["lbl_status"], 22)

def CreateTool():
    tool_name = "MocapLab_SyncRecorder"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
    else:
        print("Error creating sync tool")

CreateTool()
