"""
MobuVCam_NDI_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Virtual Camera + NDI Output Toolkit for MotionBuilder.
Attaches a virtual FBCamera to any 6DOF source and streams the viewport via NDI.

Tabs:
  [VCam]        Rigid body attach, Offset, Zoom/FOV, Record/Snapshot
  [OSC Source]  ARKit phone tracking via OSC UDP
  [OpenVR]      SteamVR controller tracking
  [NDI Out]     Stream selected camera viewport to NDI (OBS, vMix, NDI Monitor)

NDI requires NDI 6 Tools (or Runtime) installed — no pip needed.

Gamepad: Xbox-compatible BT controller via XInput (ctypes, no third-party libs)
  [Camera] LT/RT/LS-Y = Zoom   RS = Pan/Tilt   Start = Reset FOV/Offset
  [Capture] A = Record Toggle   B = Snapshot
  [Takes] X/Y = Prev/Next Take   LB/RB = Goto Start/End
  [Timeline] D-Pad UP/DOWN = Play Fwd/Bwd   D-Pad L/R = Step Bwd/Fwd

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import os
import time
import json
import math
import socket
import ctypes
import struct
import subprocess
from ctypes import wintypes
from pyfbsdk import *
from pyfbsdk_additions import *

# ── NDI 6/5 DLL Structs & Loader ─────────────────────────────────────────────
NDIlib_FourCC_type_BGRX = 0x58524742

class NDIlib_send_create_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name",  ctypes.c_char_p),
        ("p_groups",    ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool),
    ]

class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres",                 ctypes.c_int),
        ("yres",                 ctypes.c_int),
        ("FourCC",               ctypes.c_int),
        ("frame_rate_N",         ctypes.c_int),
        ("frame_rate_D",         ctypes.c_int),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type",    ctypes.c_int),
        ("timecode",             ctypes.c_longlong),
        ("p_data",               ctypes.c_void_p),
        ("line_stride_in_bytes", ctypes.c_int),
        ("p_metadata",           ctypes.c_char_p),
        ("timestamp",            ctypes.c_longlong),
    ]

class _NDILoader:
    _NDI_PATHS = [
        r"C:\Program Files\NDI\NDI 6 Tools\Runtime\Processing.NDI.Lib.x64.dll",
        r"C:\Program Files\NDI\NDI 6 Runtime\v6\Processing.NDI.Lib.x64.dll",
        r"C:\Program Files\NDI\NDI 5 Runtime\v5\Processing.NDI.Lib.x64.dll",
        r"C:\Program Files\NDI\NDI 5 SDK\Bin\x64\Processing.NDI.Lib.x64.dll",
    ]
    def __init__(self):
        self.lib = None; self.dll_path = ""
        for p in self._NDI_PATHS:
            if os.path.exists(p):
                try:
                    self.lib = ctypes.CDLL(p)
                    self.dll_path = p
                    print("[NDI] Loaded:", p)
                    break
                except: pass
    def ok(self): return self.lib is not None

# ── GDI screen capture (no pip) ───────────────────────────────────────────────
_user32 = ctypes.windll.user32
_gdi32  = ctypes.windll.gdi32

class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize",          ctypes.c_uint32),
        ("biWidth",         ctypes.c_int32),
        ("biHeight",        ctypes.c_int32),
        ("biPlanes",        ctypes.c_uint16),
        ("biBitCount",      ctypes.c_uint16),
        ("biCompression",   ctypes.c_uint32),
        ("biSizeImage",     ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed",       ctypes.c_uint32),
        ("biClrImportant",  ctypes.c_uint32),
    ]

def _gdi_capture(x, y, w, h):
    hdc_s = _user32.GetDC(None)
    hdc_m = _gdi32.CreateCompatibleDC(hdc_s)
    hbmp  = _gdi32.CreateCompatibleBitmap(hdc_s, w, h)
    _gdi32.SelectObject(hdc_m, hbmp)
    _gdi32.BitBlt(hdc_m, 0, 0, w, h, hdc_s, x, y, 0x00CC0020)
    bmi = _BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth = w; bmi.biHeight = -h
    bmi.biPlanes = 1; bmi.biBitCount = 32; bmi.biCompression = 0
    buf = (ctypes.c_ubyte * (w * h * 4))()
    _gdi32.GetDIBits(hdc_m, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
    _gdi32.DeleteObject(hbmp); _gdi32.DeleteDC(hdc_m); _user32.ReleaseDC(None, hdc_s)
    return buf

_ndi_loader = _NDILoader()

# ── OpenVR (optional) ─────────────────────────────────────────────────────────
_OVR_OK = False
try:
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    if _script_dir not in sys.path:
        sys.path.insert(0, _script_dir)
    import openvr
    _OVR_OK = True
except Exception:
    pass

# ── State ─────────────────────────────────────────────────────────────────────
if hasattr(sys, 'mobu_vcam_toolkit_state') and sys.mobu_vcam_toolkit_state:
    try: FBSystem().OnUIIdle.Remove(sys.mobu_vcam_toolkit_idle_func)
    except: pass
    _s = sys.mobu_vcam_toolkit_state.get('osc_socket')
    if _s:
        try: _s.close()
        except: pass
    # Force remove ANY potential old idle function
    try: FBSystem().OnUIIdle.Remove(sys.mobu_vcam_toolkit_idle_func)
    except: pass

# ── Versioning to kill old persistent idle loops ──
if not hasattr(sys, 'mobu_vcam_ver'): sys.mobu_vcam_ver = 0
sys.mobu_vcam_ver += 1
g_current_ver = sys.mobu_vcam_ver

sys.mobu_vcam_toolkit_state = {
    'camera':          None,
    'offset_null':     None,
    'target_model':    None,
    'fov':             60.0,
    'is_recording':    False,
    'gamepad_enabled': True,
    'gamepad_index':   0,
    'shot_count':      0,
    'osc_socket':      None,
    'osc_listening':   False,
    'osc_rigid_body':  None,
    'osc_data_null':   None,
    'osc_constraint':  None,
    'osc_con_ok':      False,
    'osc_cache':       {},
    'osc_prop_cache':  {},
    'osc_flip':        [1, 1, -1, 1, -1, -1],
    # OpenVR
    'ovr_system':      None,
    'ovr_listening':   False,
    'ovr_device':      1,
    'ovr_rigid_body':  None,
    'ovr_data_null':   None,
    'ovr_constraint':  None,
    'ovr_con_ok':      False,
    'ovr_ctrl_device': 1,
    'ovr_prev_btns':   0,
    'base_rot':        [0.0, -90.0, 0.0],
    # NDI Out
    'ndi_streaming':   False,
    'ndi_instance':    None,
    'ndi_last_time':   0,
    'ndi_fps':         30,
    'ndi_name':        'MobuVCam',
    'ndi_cap_x':       0,
    'ndi_cap_y':       0,
    'ndi_cap_w':       1280,
    'ndi_cap_h':       720,
}
g_state = sys.mobu_vcam_toolkit_state
g_ui    = {}

# ── XInput (Xbox-compatible gamepad via ctypes) ────────────────────────────────
_XINPUT_OK = False
try:
    class _GAMEPAD(ctypes.Structure):
        _fields_ = [
            ('wButtons',      wintypes.WORD),
            ('bLeftTrigger',  wintypes.BYTE),
            ('bRightTrigger', wintypes.BYTE),
            ('sThumbLX', wintypes.SHORT), ('sThumbLY', wintypes.SHORT),
            ('sThumbRX', wintypes.SHORT), ('sThumbRY', wintypes.SHORT),
        ]
    class _XSTATE(ctypes.Structure):
        _fields_ = [('dwPacketNumber', wintypes.DWORD), ('Gamepad', _GAMEPAD)]
    _xi = ctypes.windll.xinput1_4
    _XINPUT_OK = True
except Exception:
    pass

XBTN_START = 0x0010
XBTN_LB    = 0x0100
XBTN_RB    = 0x0200
XBTN_A     = 0x1000
XBTN_B     = 0x2000
XBTN_X     = 0x4000
XBTN_Y     = 0x8000
XBTN_UP    = 0x0001
XBTN_DOWN  = 0x0002
XBTN_LEFT  = 0x0004
XBTN_RIGHT = 0x0008
GP_DEAD    = 30      # trigger dead-zone (0-255)
GP_STICK_DEAD = 8000 # thumbstick dead-zone
GP_SPEED   = 0.02    # FOV change factor (increased for response)
GP_ROT_MAX = 45.0    # max degrees offset for right stick
_prev_btn  = 0

def _read_gamepad():
    if not _XINPUT_OK: return None
    try:
        s = _XSTATE()
        if _xi.XInputGetState(int(g_state['gamepad_index']), ctypes.byref(s)) == 0:
            return s.Gamepad
    except: pass
    return None

# ── OSC Source (Generic – MobuOSC_Manager style) ──────────────────────────────
def _osc_parse(data):
    """Proven OSC parser (ported from MobuOSC_Manager). Returns (address, args)."""
    try:
        addr_end = data.find(b'\x00')
        if addr_end == -1: return None, []
        address = data[:addr_end].decode('utf-8')

        type_start = (addr_end + 4) & ~0x03
        if type_start >= len(data) or data[type_start] != ord(','):
            return address, []

        type_end = data.find(b'\x00', type_start)
        if type_end == -1: return address, []
        type_tags = data[type_start+1:type_end].decode('utf-8')

        arg_start = (type_end + 4) & ~0x03
        args = []
        offset = arg_start
        for tag in type_tags:
            if offset >= len(data): break
            if tag == 'f':
                args.append(struct.unpack('>f', data[offset:offset+4])[0]); offset += 4
            elif tag == 'i':
                args.append(struct.unpack('>i', data[offset:offset+4])[0]); offset += 4
            elif tag == 's':
                s_end = data.find(b'\x00', offset)
                if s_end == -1: break
                args.append(data[offset:s_end].decode('utf-8'))
                offset = (s_end + 4) & ~0x03
        return address, args
    except:
        return None, []

def _osc_process_message(address, args):
    """Store one OSC message into osc_cache (exact MobuOSC_Manager logic)."""
    if not address: return
    cache = g_state['osc_cache']
    safe  = address.strip('/').replace('/', '_')
    # String-keyed variant (some devices use 'key value' style)
    if len(args) >= 2 and isinstance(args[0], str):
        key = args[0]
        for i in range(1, len(args)):
            if isinstance(args[i], (int, float)):
                cache['{}_{}'.format(key, i)] = float(args[i])
        return
    # Normal float/int args
    if len(args) == 1:
        if isinstance(args[0], (int, float)):
            cache[safe] = float(args[0])
    else:
        for i, val in enumerate(args):
            if isinstance(val, (int, float)):
                cache['{}_{}'.format(safe, i)] = float(val)

def _poll_osc():
    """Non-blocking drain of OSC UDP packets (MobuOSC_Manager approach)."""
    sock = g_state.get('osc_socket')
    if not sock: return
    packets = 0
    last_size = 0
    while packets < 2000:
        try:
            data, _ = sock.recvfrom(65536)
            last_size = len(data)
            if data.startswith(b'#bundle'):
                offset = 16
                while offset < len(data):
                    try:
                        size = struct.unpack('>i', data[offset:offset+4])[0]
                        offset += 4
                        _osc_process_message(*_osc_parse(data[offset:offset+size]))
                        offset += size
                    except: break
            else:
                _osc_process_message(*_osc_parse(data))
            packets += 1
        except BlockingIOError: break
        except socket.error as e:
            if e.errno == 10035: break  # Windows WSAEWOULDBLOCK
            break
        except: break
    if last_size > 0:
        now = time.time()
        if now - g_state.get('osc_last_ui', 0) > 0.2:
            g_state['osc_last_ui'] = now
            g_state['osc_pkt_count'] = g_state.get('osc_pkt_count', 0) + packets
            _update_status('OSC ✔ Receiving  (pkts: {})'.format(g_state['osc_pkt_count']))

def _quat_to_euler(qx, qy, qz, qw):
    """Quaternion → XYZ Euler degrees."""
    rx = math.degrees(math.atan2(2*(qw*qx+qy*qz), 1-2*(qx*qx+qy*qy)))
    sinp = max(-1.0, min(1.0, 2*(qw*qy-qz*qx)))
    ry = math.degrees(math.asin(sinp))
    rz = math.degrees(math.atan2(2*(qw*qz+qx*qy), 1-2*(qy*qy+qz*qz)))
    return rx, ry, rz

def _set_osc_prop(null, name, val):
    """Write to a named property on the OSC data null."""
    p = null.PropertyList.Find(name)
    if p:
        try: p.Data = float(val)
        except: pass

def _interpret_osc_cache():
    """Read osc_cache → find ARKit channels → write to VCam_OSC_Data Tx/Ty/Tz/Rx/Ry/Rz."""
    null = g_state.get('osc_data_null')
    if not null: return
    cache = g_state.get('osc_cache', {})
    if not cache: return

    for key in list(cache.keys()):
        if 'arkitposition_0' in key.lower():
            base = key[:-1]  # strip trailing '0'
            _set_osc_prop(null, 'Tx',  cache.get(base + '0', 0) * 100)
            _set_osc_prop(null, 'Ty',  cache.get(base + '1', 0) * 100)
            _set_osc_prop(null, 'Tz', -cache.get(base + '2', 0) * 100)
            break

    for key in list(cache.keys()):
        if 'arkitrotation_0' in key.lower():
            base = key[:-1]
            qx = cache.get(base + '0', 0)
            qy = cache.get(base + '1', 0)
            qz = cache.get(base + '2', 0)
            qw = cache.get(base + '3', 1)
            rx, ry, rz = _quat_to_euler(qx, -qy, -qz, qw)
            _set_osc_prop(null, 'Rx', rx)
            _set_osc_prop(null, 'Ry', ry)
            _set_osc_prop(null, 'Rz', rz)
            break






def _connect_osc():
    """Connect OSC receiver (mirrors MobuOSC_Manager OnConnectClick)."""
    ip   = g_ui['edit_osc_ip'].Text.strip()  if 'edit_osc_ip'   in g_ui else '0.0.0.0'
    port = int(g_ui['edit_osc_port'].Value)  if 'edit_osc_port' in g_ui else 9007
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((ip, port))
        s.setblocking(False)
        g_state['osc_socket']    = s
        g_state['osc_listening'] = True
        g_state['osc_cache']     = {}   # flush stale data
        g_state['osc_prop_cache']= {}   # flush property cache
        # Register idle loop (same as MobuOSC_Manager – must be done on connect)
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
        FBSystem().OnUIIdle.Add(OnUIIdle)
        sys.mobu_vcam_toolkit_idle_func = OnUIIdle
        if 'btn_osc_toggle' in g_ui:
            g_ui['btn_osc_toggle'].Caption = 'Disconnect'
        _update_status('Connected: listening on {}:{}'.format(ip, port))
    except Exception as ex:
        FBMessageBox('Error', 'Cannot bind {}:{}\n{}'.format(ip, port, str(ex)), 'OK')

def _disconnect_osc():
    """Disconnect OSC receiver."""
    s = g_state.get('osc_socket')
    if s:
        try: s.close()
        except: pass
    g_state['osc_socket']    = None
    g_state['osc_listening'] = False
    if 'btn_osc_toggle' in g_ui:
        g_ui['btn_osc_toggle'].Caption = 'Connect'
    _update_status('OSC Disconnected.')

def OnOSCToggleClick(c, e):
    if g_state['osc_listening']: _disconnect_osc()
    else: _connect_osc()

# ── OpenVR Source ──────────────────────────────────────────────────────────────
def _mat34_to_pose(mat):
    """Extract (tx, ty, tz, rx, ry, rz) in cm/deg from OpenVR HmdMatrix34_t."""
    m = mat.m
    tx = m[0][3] * 100.0
    ty = m[1][3] * 100.0
    tz = m[2][3] * 100.0  # sign handled by osc_flip[2] default=-1
    # Euler from rotation matrix (ZXY decomposition)
    sy = math.sqrt(m[0][0]**2 + m[1][0]**2)
    if sy > 1e-6:
        rx = math.degrees(math.atan2( m[2][1], m[2][2]))
        ry = math.degrees(math.atan2(-m[2][0], sy))
        rz = math.degrees(math.atan2( m[1][0], m[0][0]))
    else:
        rx = math.degrees(math.atan2(-m[1][2], m[1][1]))
        ry = math.degrees(math.atan2(-m[2][0], sy))
        rz = 0.0
    return tx, ty, tz, rx, ry, rz

def _connect_openvr():
    if not _OVR_OK:
        FBMessageBox('Error',
            'openvr module not found.\nMake sure the openvr folder is in SaintMobu_Tools.', 'OK')
        return
    try:
        vr = openvr.init(openvr.VRApplication_Background)
        g_state['ovr_system']    = vr
        g_state['ovr_listening'] = True
        # Register idle if not already
        FBSystem().OnUIIdle.Remove(OnUIIdle)
        FBSystem().OnUIIdle.Add(OnUIIdle)
        sys.mobu_vcam_toolkit_idle_func = OnUIIdle
        if 'btn_ovr_toggle' in g_ui:
            g_ui['btn_ovr_toggle'].Caption = 'Disconnect OpenVR'
        _update_status('OpenVR connected. SteamVR must be running.')
    except Exception as ex:
        FBMessageBox('Error', 'OpenVR init failed:\n' + str(ex), 'OK')

def _disconnect_openvr():
    try: openvr.shutdown()
    except: pass
    g_state['ovr_system']    = None
    g_state['ovr_listening'] = False
    if 'btn_ovr_toggle' in g_ui:
        g_ui['btn_ovr_toggle'].Caption = 'Connect OpenVR'
    _update_status('OpenVR disconnected.')

def OnOVRToggleClick(c, e):
    if g_state['ovr_listening']: _disconnect_openvr()
    else: _connect_openvr()

def OnOVRDeviceChange(c, e):
    g_state['ovr_device'] = int(c.Value)

def _clean_user_props(model):
    """Remove all user-created custom properties from a model (cleans up old OSC residue)."""
    if not model: return 0
    removed = 0
    try:
        to_remove = []
        for i in range(len(model.PropertyList)):
            try:
                p = model.PropertyList[i]
                if p.IsUserProperty():
                    to_remove.append(p)
            except: pass
        for p in to_remove:
            try:
                model.PropertyList.Remove(p)
                removed += 1
            except: pass
    except: pass
    return removed

def _ensure_osc_data_null():
    """Get or create VCam_OSC_Data null (plain, no pre-created custom props)."""
    existing = g_state.get('osc_data_null')
    if existing:
        try:
            _ = existing.Name   # raises if deleted from scene
            return existing
        except:
            g_state['osc_data_null']  = None
            g_state['osc_prop_cache'] = {}
    # Search scene for existing null
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBModel) and comp.Name == 'VCam_OSC_Data':
            _clean_user_props(comp)   # purge any stale OSC properties
            g_state['osc_data_null']  = comp
            g_state['osc_prop_cache'] = {}
            return comp
    # Create fresh null
    null = FBModelNull('VCam_OSC_Data')
    null.Show = True
    null.Size = 5.0
    g_state['osc_data_null'] = null
    return null

def OnResetOSCDataClick(c, e):
    """Delete existing VCam_OSC_Data and force recreate on next poll."""
    old = g_state.get('osc_data_null')
    if old:
        try: old.FBDelete()
        except: pass
    g_state['osc_data_null']  = None
    g_state['osc_prop_cache'] = {}
    # Recreate immediately so constraint can be rebuilt
    null = _ensure_osc_data_null()
    # Rebuild constraint if rigid body exists
    rb = g_state.get('osc_rigid_body')
    old_con = g_state.get('osc_constraint')
    if old_con:
        try: old_con.FBDelete()
        except: pass
    g_state['osc_constraint'] = None
    g_state['osc_con_ok']     = False
    if rb and null:
        _create_osc_constraint(null, rb)
    _update_status('VCam_OSC_Data reset. Old properties cleared.')


def _create_osc_constraint(osc_data, rb):
    """Relation Constraint: VCam_OSC_Data.Translation/Rotation → VCam_RigidBody T/R.
    Vector-to-vector connection (cleaner, no Tx/Ty/Tz intermediaries).
    """
    g_state['osc_con_ok'] = False
    try:
        con = FBConstraintRelation('VCam_OSC_Link')
        src_box  = con.SetAsSource(osc_data)
        trgt_box = con.ConstrainObject(rb)
        con.SetBoxPosition(src_box,  50, 80)
        con.SetBoxPosition(trgt_box, 400, 80)

        src_out = src_box.AnimationNodeOutGet()
        trgt_in = trgt_box.AnimationNodeInGet()

        def find_node(parent, name):
            if not parent: return None
            for n in parent.Nodes:
                if n.Name.lower() == name.lower(): return n
            return None

        connected = 0
        for node_name in ('Translation', 'Rotation'):
            src_n  = find_node(src_out, node_name)
            trgt_n = find_node(trgt_in,  node_name)
            if src_n and trgt_n:
                try:
                    FBConnect(src_n, trgt_n)
                    connected += 1
                    print('VCam: Constraint {} → {} OK'.format(node_name, node_name))
                except Exception as ex:
                    print('VCam: Constraint {} FAILED: {}'.format(node_name, ex))

        con.Active = True
        g_state['osc_constraint'] = con
        g_state['osc_con_ok']     = (connected >= 2)
        print('VCam: Constraint result: {}/2 connections. Bridge fallback: {}'.format(
              connected, 'OFF' if connected >= 2 else 'ON'))
        return con
    except Exception as ex:
        print('VCam: Constraint creation failed:', ex)
        g_state['osc_constraint'] = None
        g_state['osc_con_ok']     = False
        return None

def OnCreateOSCRigidBodyClick(c, e):
    """Create VCam_OSC_Data + VCam_RigidBody + Relation Constraint."""
    # Cleanup old rigid body
    existing = g_state.get('osc_rigid_body')
    if existing:
        try: existing.FBDelete()
        except: pass
    old_con = g_state.get('osc_constraint')
    if old_con:
        try: old_con.FBDelete()
        except: pass
    g_state['osc_constraint'] = None

    osc_data = _ensure_osc_data_null()

    rb = FBModelNull('VCam_RigidBody')
    rb.Show   = True
    rb.Size   = 30.0
    rb.Selected = True
    g_state['osc_rigid_body'] = rb
    FBSystem().Scene.Evaluate()

    _create_osc_constraint(osc_data, rb)

    # Refresh dropdown and auto-select VCam_RigidBody
    OnRefreshClick(None, None)
    lst = g_ui.get('list_models')
    if lst:
        for i in range(len(lst.Items)):
            if 'VCam_RigidBody' in lst.Items[i]:
                lst.ItemIndex = i; break
    _update_status('VCam_OSC_Data + VCam_RigidBody created. Start OSC, then Create & Attach Camera.')


def _ensure_ovr_data_null():
    """Get or create VCam_OVR_Data null."""
    existing = g_state.get('ovr_data_null')
    if existing:
        try:
            _ = existing.Name
            return existing
        except:
            g_state['ovr_data_null'] = None
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBModel) and comp.Name == 'VCam_OVR_Data':
            g_state['ovr_data_null'] = comp
            return comp
    null = FBModelNull('VCam_OVR_Data')
    null.Show = True
    null.Size = 5.0
    g_state['ovr_data_null'] = null
    return null

def OnResetOVRDataClick(c, e):
    old = g_state.get('ovr_data_null')
    if old:
        try: old.FBDelete()
        except: pass
    g_state['ovr_data_null'] = None
    null = _ensure_ovr_data_null()
    rb = g_state.get('ovr_rigid_body')
    old_con = g_state.get('ovr_constraint')
    if old_con:
        try: old_con.FBDelete()
        except: pass
    g_state['ovr_constraint'] = None
    g_state['ovr_con_ok']     = False
    if rb and null:
        _create_ovr_constraint(null, rb)
    _update_status('VCam_OVR_Data reset.')

def _create_ovr_constraint(ovr_data, rb):
    g_state['ovr_con_ok'] = False
    try:
        con = FBConstraintRelation('VCam_OVR_Link')
        src_box  = con.SetAsSource(ovr_data)
        trgt_box = con.ConstrainObject(rb)
        con.SetBoxPosition(src_box,  50, 200)
        con.SetBoxPosition(trgt_box, 400, 200)

        src_out = src_box.AnimationNodeOutGet()
        trgt_in = trgt_box.AnimationNodeInGet()

        def find_node(parent, name):
            if not parent: return None
            for n in parent.Nodes:
                 if n.Name.lower() == name.lower(): return n
            return None

        connected = 0
        for node_name in ('Translation', 'Rotation'):
            src_n  = find_node(src_out, node_name)
            trgt_n = find_node(trgt_in,  node_name)
            if src_n and trgt_n:
                try:
                    FBConnect(src_n, trgt_n)
                    connected += 1
                except: pass

        con.Active = True
        g_state['ovr_constraint'] = con
        g_state['ovr_con_ok']     = (connected >= 2)
        return con
    except:
        g_state['ovr_constraint'] = None
        g_state['ovr_con_ok']     = False
        return None

def OnCreateOVRRigidBodyClick(c, e):
    existing = g_state.get('ovr_rigid_body')
    if existing:
        try: existing.FBDelete()
        except: pass
    old_con = g_state.get('ovr_constraint')
    if old_con:
        try: old_con.FBDelete()
        except: pass
    g_state['ovr_constraint'] = None

    ovr_data = _ensure_ovr_data_null()

    rb = FBModelNull('VCam_OVR_RigidBody')
    rb.Show   = True
    rb.Size   = 30.0
    rb.Selected = True
    g_state['ovr_rigid_body'] = rb
    FBSystem().Scene.Evaluate()

    _create_ovr_constraint(ovr_data, rb)

    OnRefreshClick(None, None)
    lst = g_ui.get('list_models')
    if lst:
        for i in range(len(lst.Items)):
            if 'VCam_OVR_RigidBody' in lst.Items[i]:
                lst.ItemIndex = i; break
    _update_status('VCam_OVR_Data + VCam_OVR_RigidBody created.')

# ── FOV constants ──────────────────────────────────────────────────────────────
FOV_MIN   = 20.0
FOV_MAX   = 150.0
FOV_STEP  = 5.0

# ── Helpers ────────────────────────────────────────────────────────────────────
def _key_vector(prop, vec):
    prop.SetAnimated(True)
    node = prop.GetAnimationNode()
    if node and len(node.Nodes) >= 3:
        t = FBSystem().LocalTime
        node.Nodes[0].KeyAdd(t, float(vec[0]))
        node.Nodes[1].KeyAdd(t, float(vec[1]))
        node.Nodes[2].KeyAdd(t, float(vec[2]))

def _key_float(prop, val):
    prop.SetAnimated(True)
    node = prop.GetAnimationNode()
    if node:
        t = FBSystem().LocalTime
        if len(node.Nodes) > 0:
            node.Nodes[0].KeyAdd(t, float(val))
        else:
            node.KeyAdd(t, float(val))
def _update_status(msg):
    if 'lbl_status' in g_ui:
        g_ui['lbl_status'].Caption = str(msg)

def _find_model(long_name):
    for comp in FBSystem().Scene.Components:
        if not isinstance(comp, FBModel): continue
        n = comp.LongName if hasattr(comp, 'LongName') and comp.LongName else comp.Name
        if n == long_name:
            return comp
    return None

def _scan_models():
    skip = {'SaintVCam', 'SaintVCam_Offset', 'VCam_OSC_Data',
            'Camera Switcher', 'CameraSwitcher', 'CameraInterest'}
    seen, out = set(), []
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, (FBCamera, FBLight)): continue
        if not isinstance(comp, FBModel): continue
        if comp.Name in skip: continue
        if comp.Name.startswith('VCam_Shot_'): continue
        n = comp.LongName if hasattr(comp, 'LongName') and comp.LongName else comp.Name
        if n and n not in seen:
            seen.add(n); out.append(n)
    return sorted(out)

def _cleanup_vcam():
    for comp in list(FBSystem().Scene.Components):
        if isinstance(comp, FBModel) and comp.Name in ('SaintVCam', 'SaintVCam_Offset'):
            try: comp.FBDelete()
            except: pass
    g_state['camera'] = g_state['offset_null'] = g_state['target_model'] = None
    g_state['is_recording'] = False

# ── Camera display helpers ─────────────────────────────────────────────────────
def _set_hd_resolution(cam):
    """Set camera picture format to 1920x1080 HD via best available API."""
    # Method 1: ResolutionMode enum (MB 2013+)
    try:
        for attr in ('kFBResolution1920x1080', 'kFBResolutionFullHD',
                     'kFBResolutionHD', 'kFBResolution1080p'):
            if hasattr(FBCameraResolutionMode, attr):
                cam.ResolutionMode = getattr(FBCameraResolutionMode, attr)
                return
    except: pass
    # Method 2: Direct width/height properties
    for wn, hn in (('ResolutionWidth','ResolutionHeight'),
                   ('PictureWidth','PictureHeight'),
                   ('AspectW','AspectH')):
        try:
            setattr(cam, wn, 1920); setattr(cam, hn, 1080); return
        except: pass
    # Method 3: PropertyList fallback
    for wn, hn in (('ResolutionWidth','ResolutionHeight'),
                   ('Width','Height')):
        pw = cam.PropertyList.Find(wn)
        ph = cam.PropertyList.Find(hn)
        if pw and ph:
            try: pw.Data = 1920; ph.Data = 1080; return
            except: pass

# ── Set FOV ────────────────────────────────────────────────────────────────────
def _set_fov(val):
    val = max(FOV_MIN, min(FOV_MAX, float(val)))
    g_state['fov'] = val
    cam = g_state['camera']
    if cam:
        try:
            if g_state.get('is_recording'):
                _key_float(cam.FieldOfView, val)
            else:
                cam.FieldOfView = val
        except: pass
    if 'edit_fov' in g_ui:
        try: g_ui['edit_fov'].Value = val
        except: pass

# ── Rotation offset helper ─────────────────────────────────────────────────────
def _apply_rot(axis, deg):
    null = g_state['offset_null']
    if not null:
        FBMessageBox('Error', 'No VCam attached. Create one first.', 'OK'); return
    rot = FBVector3d()
    null.GetVector(rot, FBModelTransformationType.kModelRotation, False)
    if axis == 'X': rot[0] += deg
    elif axis == 'Y': rot[1] += deg
    else: rot[2] += deg
    null.SetVector(rot, FBModelTransformationType.kModelRotation, False)
    g_state['base_rot'] = [rot[0], rot[1], rot[2]]
    FBSystem().Scene.Evaluate()

def _make_rot_cb(axis, deg):
    def cb(c, e): _apply_rot(axis, deg)
    return cb

# ── Callbacks ──────────────────────────────────────────────────────────────────
def OnRefreshClick(c, e):
    if 'list_models' not in g_ui: return
    g_ui['list_models'].Items.removeAll()
    for n in _scan_models():
        g_ui['list_models'].Items.append(n)
    if len(g_ui['list_models'].Items) > 0:
        g_ui['list_models'].ItemIndex = 0

def OnCreateCameraClick(c, e):
    if 'list_models' not in g_ui: return
    idx = g_ui['list_models'].ItemIndex
    if idx < 0 or idx >= len(g_ui['list_models'].Items):
        FBMessageBox('Error', 'Please select a rigid body model first.', 'OK'); return

    model_name = g_ui['list_models'].Items[idx]
    target = _find_model(model_name)
    if not target:
        FBMessageBox('Error', 'Model not found:\n' + model_name, 'OK'); return

    _cleanup_vcam()

    # CamOffset Null → parented to rigid body
    offset = FBModelNull('SaintVCam_Offset')
    offset.Show = True; offset.Size = 15.0
    offset.Parent = target
    # Y=-90 rotates the camera's natural +X facing to world +Z
    offset.SetVector(FBVector3d(0, 0, 0),  FBModelTransformationType.kModelTranslation, False)
    offset.SetVector(FBVector3d(0, -90, 0), FBModelTransformationType.kModelRotation,    False)
    g_state['base_rot'] = [0.0, -90.0, 0.0]
    FBSystem().Scene.Evaluate()

    # FBCamera → child of offset, also zeroed locally
    cam = FBCamera('SaintVCam')
    cam.Parent = offset
    cam.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelTranslation, False)
    cam.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
    try: cam.FieldOfView.SetAnimated(True)
    except: pass
    cam.FieldOfView = g_state['fov']
    cam.NearPlane   = 1.0
    cam.FarPlane    = 50000.0
    cam.Show        = True

    # ── Camera display settings ─────────────────────────────────────────────
    # Safe Frame (action/title safe area overlay)
    for pn in ('ShowSafeArea', 'SafeAreaDisplay', 'DisplaySafeArea', 'ShowGate'):
        p = cam.PropertyList.Find(pn)
        if p:
            try: p.Data = True; break
            except: pass

    # Timecode overlay
    for pn in ('ShowTimeCode', 'TimecodeDisplay', 'DisplayTimecode', 'ShowTimecode'):
        p = cam.PropertyList.Find(pn)
        if p:
            try: p.Data = True; break
            except: pass

    # Picture format → HD (1920×1080)
    _set_hd_resolution(cam)

    FBSystem().Scene.Evaluate()

    g_state['camera']       = cam
    g_state['offset_null']  = offset
    g_state['target_model'] = target

    FBSystem().OnUIIdle.Remove(OnUIIdle)
    FBSystem().OnUIIdle.Add(OnUIIdle)
    sys.mobu_vcam_toolkit_idle_func = OnUIIdle

    _update_status('VCam attached to: ' + model_name)
    FBMessageBox('Success', 'VCam created!\nAttached to: {}\n\nClick "Set Active Camera" to view through it.'.format(model_name), 'OK')

def OnSetActiveClick(c, e):
    cam = g_state['camera']
    if not cam:
        FBMessageBox('Error', 'No VCam created yet.', 'OK'); return
    try:
        renderer = FBSystem().Scene.Renderer
        # Set VCam only in the first viewport pane (pane 0)
        try: renderer.SetCameraInPane(cam, 0)
        except: pass
        _update_status('SaintVCam is active in pane 0.')
    except Exception as ex:
        _update_status('Error: ' + str(ex))


def OnDetachClick(c, e):
    null = g_state['offset_null']
    if not null:
        FBMessageBox('Error', 'No VCam attached.', 'OK'); return
    try:
        null.Parent = None
        g_state['target_model'] = None
        _update_status('VCam detached from rigid body.')
    except Exception as ex:
        _update_status('Error: ' + str(ex))

def OnDeleteVCamClick(c, e):
    _cleanup_vcam()
    try: FBSystem().OnUIIdle.Remove(OnUIIdle)
    except: pass
    _update_status('VCam deleted.')

def OnResetOffsetClick(c, e):
    null = g_state['offset_null']
    if null:
        # Y=-90 → camera faces world +Z (FBCamera natural look = +X, so -90° → +Z)
        null.SetVector(FBVector3d(0, -90, 0), FBModelTransformationType.kModelRotation, False)
        g_state['base_rot'] = [0.0, -90.0, 0.0]
        FBSystem().Scene.Evaluate()
        _update_status('Mounting offset reset to face +Z.')
    else:
        FBMessageBox('Error', 'No VCam created yet.', 'OK')

def OnFOVChange(control, e):
    _set_fov(control.Value)

def OnZoomInClick(c, e):  _set_fov(g_state['fov'] - FOV_STEP)
def OnZoomOutClick(c, e): _set_fov(g_state['fov'] + FOV_STEP)

def OnGamepadToggle(c, e):
    g_state['gamepad_enabled'] = bool(c.State)

def OnGPIndexChange(c, e):
    g_state['gamepad_index'] = int(c.Value)

# ── Record ─────────────────────────────────────────────────────────────────────
def OnRecordClick(c, e):
    cam = g_state['camera']
    if not cam:
        FBMessageBox('Error', 'No VCam created.', 'OK'); return

    if not g_state['is_recording']:
        g_state['is_recording'] = True
        if 'btn_record' in g_ui:
            g_ui['btn_record'].Caption = '⏹ Stop Recording'
        try: cam.FieldOfView.SetAnimated(True)
        except: pass
        # Enable animation on rigid body T/R so keyframes can be stored
        model = g_state['target_model']
        if model:
            try:
                model.Translation.SetAnimated(True)
                model.Rotation.SetAnimated(True)
            except: pass
        try:
            take_name = 'VCam_Take_' + time.strftime('%Y%m%d_%H%M%S')
            new_take  = FBTake(take_name)
            FBSystem().Scene.Takes.append(new_take)
            FBSystem().CurrentTake = new_take
            end_t = FBTime(); end_t.SetSecondDouble(600.0)
            new_take.LocalTimeSpan = FBTimeSpan(FBTime(0), end_t)
            FBPlayerControl().LoopStop = end_t
        except Exception as ex: print('VCam Record:', ex)
        try:
            FBPlayerControl().GotoStart()
            FBPlayerControl().Play()
        except: pass
        _update_status('Recording...')
    else:
        g_state['is_recording'] = False
        if 'btn_record' in g_ui:
            g_ui['btn_record'].Caption = '🔴 Record'
        try:
            FBPlayerControl().Stop()
            FBSystem().Scene.Evaluate() # Force refresh
            stop_t = FBSystem().LocalTime
            take   = FBSystem().CurrentTake
            if take:
                take.LocalTimeSpan = FBTimeSpan(take.LocalTimeSpan.GetStart(), stop_t)
                FBPlayerControl().LoopStop = stop_t
        except Exception as ex: print('VCam StopRecord:', ex)
        _update_status('Recording stopped.')

# ── Snapshot ─────────────────────────────────────────────────────────────────
def OnBrowseSnapClick(c, e):
    popup = FBFolderPopup()
    popup.Caption = 'Select Snapshot Folder'
    if popup.Execute() and 'edit_snap_path' in g_ui:
        g_ui['edit_snap_path'].Text = popup.Path

def _create_shot_marker(shot_num, ts, filename, pos, rot, fov):
    """Create a Hard Cross scene marker at camera world position with shot metadata."""
    name = 'VCam_Shot_{:03d}'.format(shot_num)
    marker = FBModelMarker(name)
    marker.Show = True
    marker.Size = 20.0
    try: marker.Look = FBMarkerLook.kFBMarkerLookHardCross
    except: pass
    # Place at camera world position/rotation
    marker.SetVector(FBVector3d(pos[0], pos[1], pos[2]),
                     FBModelTransformationType.kModelTranslation, True)
    marker.SetVector(FBVector3d(rot[0], rot[1], rot[2]),
                     FBModelTransformationType.kModelRotation, True)
    FBSystem().Scene.Evaluate()
    # Add user properties for shot metadata
    try:
        p_fov = marker.PropertyCreate('FOV_deg',   FBPropertyType.kFBPT_double, 'Number', False, True, None)
        p_ts  = marker.PropertyCreate('Timestamp', FBPropertyType.kFBPT_charptr,'String', False, True, None)
        p_fn  = marker.PropertyCreate('Filename',  FBPropertyType.kFBPT_charptr,'String', False, True, None)
        if p_fov: p_fov.Data = float(fov)
        if p_ts:  p_ts.Data  = str(ts)
        if p_fn:  p_fn.Data  = str(filename)
    except Exception as ex:
        print('VCam Marker property error:', ex)
    return marker

def OnSnapshotClick(c, e):
    save_dir = g_ui['edit_snap_path'].Text.strip() if 'edit_snap_path' in g_ui else os.path.expanduser('~')
    if not os.path.isdir(save_dir):
        try: os.makedirs(save_dir)
        except:
            FBMessageBox('Error', 'Invalid path:\n' + save_dir, 'OK'); return

    cam = g_state['camera']
    ts  = time.strftime('%Y%m%d_%H%M%S')
    fn  = 'VCam_{}.png'.format(ts)
    fp  = os.path.join(save_dir, fn).replace('\\', '/')

    # ① Screenshot via PowerShell
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms;"
        "Add-Type -AssemblyName System.Drawing;"
        "$b=[System.Windows.Forms.Screen]::PrimaryScreen.Bounds;"
        "$bmp=New-Object System.Drawing.Bitmap($b.Width,$b.Height);"
        "$g=[System.Drawing.Graphics]::FromImage($bmp);"
        "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
        "$bmp.Save('{}');"
        "$g.Dispose();$bmp.Dispose();"
    ).format(fp)
    subprocess.Popen(['powershell', '-WindowStyle', 'Hidden', '-Command', ps])

    # ② Read camera world T / R / FOV
    pos = [0.0, 0.0, 0.0]
    rot = [0.0, 0.0, 0.0]
    fov = g_state['fov']
    if cam:
        try:
            v = FBVector3d()
            cam.GetVector(v, FBModelTransformationType.kModelTranslation, True)
            pos = [round(v[0], 3), round(v[1], 3), round(v[2], 3)]
            cam.GetVector(v, FBModelTransformationType.kModelRotation, True)
            rot = [round(v[0], 3), round(v[1], 3), round(v[2], 3)]
            fov = round(float(cam.FieldOfView), 2)
        except: pass

    # ③ Scene marker at camera position
    g_state['shot_count'] += 1
    shot_num = g_state['shot_count']
    _create_shot_marker(shot_num, ts, fn, pos, rot, fov)

    # ④ Append to JSONL log file
    log_path = os.path.join(save_dir, 'VCam_Shots.jsonl')
    record = {
        'shot':      shot_num,
        'timestamp': ts,
        'file':      fn,
        'pos_cm':    pos,
        'rot_deg':   rot,
        'fov_deg':   fov,
    }
    try:
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    except Exception as ex:
        print('VCam Log write error:', ex)

    _update_status('Shot {:03d} saved → {} | FOV {:.1f}°'.format(shot_num, fn, fov))

# ── UIIdle (OSC polling + Gamepad + Keyframing during Record) ──────────────────
def OnUIIdle(c, e):
    # Kill self if a newer version of the script is running
    if sys.mobu_vcam_ver != g_current_ver:
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
        return

    global _prev_btn

    # ── NDI frame send (merged into VCam idle) ──────────────────────────────────
    if g_state.get('ndi_streaming') and g_state.get('ndi_instance'):
        _now = time.time()
        if _now - g_state['ndi_last_time'] >= 1.0 / max(1, g_state['ndi_fps']):
            g_state['ndi_last_time'] = _now
            try:
                _x = g_state['ndi_cap_x']; _y = g_state['ndi_cap_y']
                _w = g_state['ndi_cap_w']; _h = g_state['ndi_cap_h']
                _buf = _gdi_capture(_x, _y, _w, _h)
                _vf = NDIlib_video_frame_v2_t()
                _vf.xres = _w; _vf.yres = _h
                _vf.FourCC = NDIlib_FourCC_type_BGRX
                _vf.frame_rate_N = g_state['ndi_fps']; _vf.frame_rate_D = 1
                _vf.picture_aspect_ratio = float(_w) / float(_h)
                _vf.frame_format_type = 0; _vf.timecode = 0
                _vf.p_data = ctypes.cast(_buf, ctypes.c_void_p).value
                _vf.line_stride_in_bytes = _w * 4
                _vf.p_metadata = None; _vf.timestamp = 0
                _ndi_loader.lib.NDIlib_send_send_video_v2(
                    ctypes.c_void_p(g_state['ndi_instance']), ctypes.byref(_vf))
            except Exception as _e:
                pass

    # ── OSC polling ────────────────────────────────────────────────────────────
    if g_state['osc_listening'] and g_state.get('osc_socket'):
        _poll_osc()
        # Validate null (clear stale ref if user deleted it from scene)
        osc_null = g_state.get('osc_data_null')
        if osc_null:
            try: _ = osc_null.Name
            except:
                g_state['osc_data_null'] = None
                osc_null = None
        # NOTE: no auto-create – only "Create OSC Source & RigidBody" button creates it

        # Write all received OSC channels as animatable properties on VCam_OSC_Data
        # (so users can monitor incoming data; Reset button cleans stale props via FBDelete)
        if osc_null:
            prop_cache = g_state.setdefault('osc_prop_cache', {})
            for key, val in list(g_state['osc_cache'].items()):
                prop = prop_cache.get(key)
                if not prop:
                    prop = osc_null.PropertyList.Find(key)
                    if not prop:
                        prop = osc_null.PropertyCreate(key, FBPropertyType.kFBPT_double, 'Number', True, True, None)
                        if prop: prop.SetAnimated(True)
                    if prop: prop_cache[key] = prop
                if prop:
                    try: prop.Data = float(val)
                    except: pass

        # ARKit interpretation → drive VCam_OSC_Data.Translation/Rotation
        # raw OSC channels stay in osc_cache (memory only, not written as scene props)
        cache = g_state.get('osc_cache', {})
        tx = ty = tz = rx = ry = rz = None
        for k in cache:
            kl = k.lower()
            if tx is None and 'arkitposition_0' in kl:
                base = k[:-1]
                flip = g_state.get('osc_flip', [1,1,-1,1,-1,-1])
                tx = cache.get(base+'0', 0) * 100 * flip[0]
                ty = cache.get(base+'1', 0) * 100 * flip[1]
                tz = cache.get(base+'2', 0) * 100 * flip[2]
            if rx is None and 'arkitrotation_0' in kl:
                base = k[:-1]
                flip = g_state.get('osc_flip', [1,1,-1,1,-1,-1])
                qx = cache.get(base+'0', 0)
                qy = cache.get(base+'1', 0)
                qz = cache.get(base+'2', 0)
                qw = cache.get(base+'3', 1)
                rx, ry, rz = _quat_to_euler(qx, qy, qz, qw)   # pure conversion
                rx *= flip[3]; ry *= flip[4]; rz *= flip[5]     # apply flip state
        if osc_null:
            if tx is not None:
                if g_state.get('is_recording') or getattr(sys, 'mobu_master_recording', False):
                    _key_vector(osc_null.Translation, [tx, ty, tz])
                else:
                    try: osc_null.SetVector(FBVector3d(tx, ty, tz), FBModelTransformationType.kModelTranslation, True)
                    except: pass
            if rx is not None:
                if g_state.get('is_recording') or getattr(sys, 'mobu_master_recording', False):
                    _key_vector(osc_null.Rotation, [rx, ry, rz])
                else:
                    try: osc_null.SetVector(FBVector3d(rx, ry, rz), FBModelTransformationType.kModelRotation, True)
                    except: pass

    # ── Fallback bridge: direct RigidBody drive when Constraint not connected ──
    if g_state['osc_listening'] and not g_state.get('osc_con_ok'):
        rb = g_state.get('osc_rigid_body')
        if rb:
            try:
                if tx is not None:
                    rb.SetVector(FBVector3d(tx, ty, tz), FBModelTransformationType.kModelTranslation, True)
                if rx is not None:
                    rb.SetVector(FBVector3d(rx, ry, rz), FBModelTransformationType.kModelRotation, True)
                if tx is not None or rx is not None:
                    FBSystem().Scene.Evaluate()
            except: pass

    # ── OpenVR polling (drives OVR T/R pipeline) ──────────────────
    if g_state['ovr_listening'] and g_state.get('ovr_system'):
        try:
            vr     = g_state['ovr_system']
            dev    = int(g_state.get('ovr_device', 0))
            flip   = g_state.get('osc_flip', [1,1,-1,1,-1,-1])
            poses  = (openvr.TrackedDevicePose_t * openvr.k_unMaxTrackedDeviceCount)()
            vr.getDeviceToAbsoluteTrackingPose(
                openvr.TrackingUniverseStanding, 0, poses)
            pose = poses[dev]
            if pose.bPoseIsValid:
                rtx, rty, rtz, rrx, rry, rrz = _mat34_to_pose(
                    pose.mDeviceToAbsoluteTracking)
                tx = rtx * flip[0]
                ty = rty * flip[1]
                tz = rtz * flip[2]
                rx = rrx * flip[3]
                ry = rry * flip[4]
                rz = rrz * flip[5]
                ovr_null = g_state.get('ovr_data_null')
                if ovr_null:
                    if g_state.get('is_recording') or getattr(sys, 'mobu_master_recording', False):
                        _key_vector(ovr_null.Translation, [tx, ty, tz])
                        _key_vector(ovr_null.Rotation, [rx, ry, rz])
                    else:
                        try: ovr_null.SetVector(FBVector3d(tx,ty,tz), FBModelTransformationType.kModelTranslation, True)
                        except: pass
                        try: ovr_null.SetVector(FBVector3d(rx,ry,rz), FBModelTransformationType.kModelRotation, True)
                        except: pass
                if not g_state.get('ovr_con_ok'):
                    rb2 = g_state.get('ovr_rigid_body')
                    if rb2:
                        try:
                            rb2.SetVector(FBVector3d(tx,ty,tz), FBModelTransformationType.kModelTranslation, True)
                            rb2.SetVector(FBVector3d(rx,ry,rz), FBModelTransformationType.kModelRotation, True)
                            FBSystem().Scene.Evaluate()
                        except: pass
        except: pass

    # ── OpenVR Controller polling (Zoom/Snapshot/Record) ──────────────
    if g_state.get('ovr_listening') and g_state.get('ovr_system'):
        try:
            vr = g_state['ovr_system']
            ctrl_dev = int(g_state.get('ovr_ctrl_device', 1))
            res, cstate = vr.getControllerState(ctrl_dev)
            if res:
                # 1. Zoom via Trackpad/Joystick Y-axis (Axis 0)
                ty = cstate.rAxis[0].y
                if abs(ty) > 0.1:  # 10% deadzone
                    _set_fov(g_state['fov'] - ty * 1.5)

                # 2. Buttons (Snapshot / Record)
                btns = cstate.ulButtonPressed
                prev_btns = g_state.get('ovr_prev_btns', 0)
                pressed = btns & ~prev_btns
                g_state['ovr_prev_btns'] = btns
                
                now = time.time()
                # Trigger (33) -> Snapshot
                if pressed & (1 << 33):
                    if now - g_state.get('last_snap_time', 0) > 0.5:
                        g_state['last_snap_time'] = now
                        OnSnapshotClick(None, None)
                
                # Application Menu (1) -> Record
                if pressed & (1 << 1):
                    if now - g_state.get('last_record_time', 0) > 1.0:
                        g_state['last_record_time'] = now
                        if 'btn_record' in g_ui: OnRecordClick(g_ui['btn_record'], None)
        except: pass

    # ── Ensure FOV is continuously keyed during recording ──────────────────
    if g_state.get('is_recording') or getattr(sys, 'mobu_master_recording', False):
        cam = g_state.get('camera')
        if cam:
            try:
                _ = cam.Name # Check if destroyed
                _key_float(cam.FieldOfView, g_state['fov'])
            except:
                g_state['camera'] = None

    # ── Gamepad polling ────────────────────────────────────────────────────
    if not g_state['gamepad_enabled']: return
    gp = _read_gamepad()
    if not gp: return

    # 1. Smooth Zoom (LT/RT + Left Stick Y)
    lt = max(0, gp.bLeftTrigger  - GP_DEAD)
    rt = max(0, gp.bRightTrigger - GP_DEAD)
    lsy = 0
    if abs(gp.sThumbLY) > GP_STICK_DEAD:
        lsy = gp.sThumbLY / 128.0  # Scale stick to match trigger range roughly
    
    zoom_delta = (lt - rt) + lsy
    if zoom_delta != 0:
        _set_fov(g_state['fov'] + zoom_delta * GP_SPEED)

    # 2. Right Stick Rotation (Auto-centering ON CAMERA BODY)
    cam = g_state.get('camera')
    if cam:
        try:
            _ = cam.Name # Check if destroyed
            rsx = gp.sThumbRX if abs(gp.sThumbRX) > GP_STICK_DEAD else 0
            rsy = gp.sThumbRY if abs(gp.sThumbRY) > GP_STICK_DEAD else 0
            
            if rsx != 0 or rsy != 0:
                pan  = -(rsx / 32767.0) * GP_ROT_MAX
                tilt = (rsy / 32767.0) * GP_ROT_MAX
                # For Camera body: Y is Yaw, X is Roll, so Z must be Tilt
                cam.SetVector(FBVector3d(0, pan, tilt), 
                              FBModelTransformationType.kModelRotation, False)
            else:
                # Snap back to zero rotation locally
                cam.SetVector(FBVector3d(0, 0, 0), 
                              FBModelTransformationType.kModelRotation, False)
        except:
            g_state['camera'] = None

    # 3. Buttons
    btn     = gp.wButtons
    pressed = btn & ~_prev_btn
    _prev_btn = btn
    now = time.time()
    
    # A Button -> Record Toggle
    if pressed & XBTN_A:
        if now - g_state.get('last_record_time', 0) > 1.0:
            g_state['last_record_time'] = now
            if 'btn_record' in g_ui: OnRecordClick(g_ui['btn_record'], None)
            
    # B Button -> Snapshot
    if pressed & XBTN_B:
        if now - g_state.get('last_snap_time', 0) > 0.5:
            g_state['last_snap_time'] = now
            OnSnapshotClick(None, None)
            
    # Start Button -> Reset FOV & Offset
    if pressed & XBTN_START:
        _set_fov(60.0)
        OnResetOffsetClick(None, None)
        _update_status('Reset FOV & Camera Offset.')

    # 4. D-Pad -> Timeline control
    player = FBPlayerControl()
    p_rev  = player.PropertyList.Find('Reverse')
    if not p_rev: p_rev = player.PropertyList.Find('PlaybackReverse')
    
    p_spd  = player.PropertyList.Find('PlaybackSpeed')
    if not p_spd: p_spd = player.PropertyList.Find('TransportSpeed')
    
    if pressed & XBTN_UP:
        # Toggle Forward
        if player.IsPlaying and g_state.get('playback_dir') == 'fwd':
            player.Stop()
            g_state['playback_dir'] = None
        else:
            player.Stop()
            if p_spd: p_spd.Data = 1.0
            if p_rev: p_rev.Data = False
            g_state['playback_dir'] = 'fwd'
            player.Play(False)
            
    if pressed & XBTN_DOWN:
        # Toggle Backward
        if player.IsPlaying and g_state.get('playback_dir') == 'bwd':
            player.Stop()
            g_state['playback_dir'] = None
        else:
            player.Stop()
            if p_spd: p_spd.Data = 1.0
            if p_rev: p_rev.Data = True
            g_state['playback_dir'] = 'bwd'
            try:
                player.PlayReverse()
            except AttributeError:
                try:
                    player.Play(True)
                except:
                    pass
            except:
                pass
            
    if pressed & XBTN_LEFT:
        player.StepBackward()
    if pressed & XBTN_RIGHT:
        player.StepForward()

    # LB/RB -> Jump to Start/End
    if pressed & XBTN_LB:
        player.GotoStart()
        _update_status('Jumped to Start.')
    if pressed & XBTN_RB:
        try:
            player.GotoEnd()
            _update_status('Jumped to End.')
        except:
            pass

    # X/Y -> Previous/Next Take
    if pressed & (XBTN_X | XBTN_Y):
        takes = FBSystem().Scene.Takes
        cur_take = FBSystem().CurrentTake
        idx = -1
        for i, t in enumerate(takes):
            if t == cur_take:
                idx = i
                break
        if pressed & XBTN_X and idx > 0:
            FBSystem().CurrentTake = takes[idx - 1]
            _update_status('Take: ' + takes[idx - 1].Name)
        elif pressed & XBTN_Y and idx >= 0 and idx < len(takes) - 1:
            FBSystem().CurrentTake = takes[idx + 1]
            _update_status('Take: ' + takes[idx + 1].Name)


# ── NDI Controls ──────────────────────────────────────────────────────────────
def _ndi_start():
    if not _ndi_loader.ok():
        FBMessageBox('NDI Error', 'NDI Runtime DLL not found.\nInstall NDI 6 Tools first.', 'OK')
        return False
    if g_state.get('ndi_streaming'): return True
    try:
        _ndi_loader.lib.NDIlib_initialize.restype = ctypes.c_bool
        if not _ndi_loader.lib.NDIlib_initialize():
            print('[NDI] Initialize failed'); return False

        _ndi_loader.lib.NDIlib_send_create.restype  = ctypes.c_void_p
        _cs = NDIlib_send_create_t()
        _cs.p_ndi_name  = g_state['ndi_name'].encode('utf-8')
        _cs.p_groups    = None
        _cs.clock_video = False
        _cs.clock_audio = False
        _ndi_loader.lib.NDIlib_send_create.argtypes = [ctypes.POINTER(NDIlib_send_create_t)]
        inst = _ndi_loader.lib.NDIlib_send_create(ctypes.pointer(_cs))
        if not inst:  # fallback: NULL struct
            _ndi_loader.lib.NDIlib_send_create.argtypes = [ctypes.c_void_p]
            inst = _ndi_loader.lib.NDIlib_send_create(None)
        if not inst:
            print('[NDI] Failed to create sender'); return False

        g_state['ndi_instance'] = inst
        g_state['ndi_streaming'] = True
        # Ensure idle is running
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
        FBSystem().OnUIIdle.Add(OnUIIdle)
        sys.mobu_vcam_toolkit_idle_func = OnUIIdle
        print('[NDI] Streaming: {} @ {}x{} {}fps'.format(
            g_state['ndi_name'], g_state['ndi_cap_w'], g_state['ndi_cap_h'], g_state['ndi_fps']))
        return True
    except Exception as ex:
        print('[NDI] Start error:', ex); return False

def _ndi_stop():
    if not g_state.get('ndi_streaming'): return
    g_state['ndi_streaming'] = False
    inst = g_state.get('ndi_instance')
    if inst and _ndi_loader.ok():
        try:
            _ndi_loader.lib.NDIlib_initialize()
            _ndi_loader.lib.NDIlib_send_destroy(ctypes.c_void_p(inst))
        except: pass
    g_state['ndi_instance'] = None
    print('[NDI] Stopped.')

def OnNDIToggleClick(c, e):
    if not g_state.get('ndi_streaming'):
        # Read UI values before start
        try: g_state['ndi_name'] = g_ui['ndi_name'].Text.strip() or 'MobuVCam'
        except: pass
        try: g_state['ndi_fps']   = max(1, int(g_ui['ndi_fps'].Text))
        except: pass
        try: g_state['ndi_cap_x'] = int(g_ui['ndi_x'].Text)
        except: pass
        try: g_state['ndi_cap_y'] = int(g_ui['ndi_y'].Text)
        except: pass
        try: g_state['ndi_cap_w'] = max(64, int(g_ui['ndi_w'].Text))
        except: pass
        try: g_state['ndi_cap_h'] = max(64, int(g_ui['ndi_h'].Text))
        except: pass
        if _ndi_start():
            c.Caption = 'Stop NDI'
            if 'ndi_status' in g_ui: g_ui['ndi_status'].Caption = 'STREAMING'
    else:
        _ndi_stop()
        c.Caption = 'Start NDI'
        if 'ndi_status' in g_ui: g_ui['ndi_status'].Caption = 'OFFLINE'

def OnNDIRefreshCamsClick(c, e):
    lst = g_ui.get('ndi_cam_list')
    if not lst: return
    lst.Items.removeAll()
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBCamera):
            lst.Items.append(comp.Name)
    print('[NDI] Camera list refreshed.')

def OnNDISetCamClick(c, e):
    lst = g_ui.get('ndi_cam_list')
    if not lst or lst.ItemIndex < 0: return
    name = lst.Items[lst.ItemIndex]
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBCamera) and comp.Name == name:
            try:
                FBSystem().Renderer.SetCameraInPane(comp, 0)
                print('[NDI] Viewport camera: {}'.format(name))
                _update_status('NDI Camera: {}'.format(name))
            except Exception as ex:
                print('[NDI] SetCamera error:', ex)
            break

# ── UI ─────────────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 250
    tool.StartSizeY = 750

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   '')
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    '')
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  '')
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, '')
    
    y_tab = FBAddRegionParam(25, FBAttachType.kFBAttachNone, '')
    tool.AddRegion('tab', 'tab', x, y, w, y_tab)
    
    tab_panel = FBTabPanel()
    tab_panel.Items.append('VCam')
    tab_panel.Items.append('OSC Source')
    tab_panel.Items.append('OpenVR Source')
    tab_panel.Items.append('NDI Out')
    tool.SetControl('tab', tab_panel)
    
    y_status_h = FBAddRegionParam(30, FBAttachType.kFBAttachNone, '')
    y_status_y = FBAddRegionParam(-30, FBAttachType.kFBAttachBottom, '')
    tool.AddRegion('status', 'status', x, y_status_y, w, y_status_h)

    y_content_start = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, 'tab')
    h_content_end = FBAddRegionParam(0, FBAttachType.kFBAttachTop, 'status')
    tool.AddRegion('content', 'content', x, y_content_start, w, h_content_end)

    view_osc  = FBVBoxLayout()
    view_ovr  = FBVBoxLayout()
    view_vcam = FBVBoxLayout()
    view_ndi  = FBVBoxLayout()
    tool.SetControl('content', view_vcam)

    def OnTabChange(c, e):
        if   c.ItemIndex == 0: tool.SetControl('content', view_vcam)
        elif c.ItemIndex == 1: tool.SetControl('content', view_osc)
        elif c.ItemIndex == 2: tool.SetControl('content', view_ovr)
        else:                  tool.SetControl('content', view_ndi)

    tab_panel.OnChange.Add(OnTabChange)

    def hdr(text):
        lbl = FBLabel()
        lbl.Caption = '--- ' + text + ' ---'
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl

    def btn(caption, cb):
        b = FBButton(); b.Caption = caption; b.OnClick.Add(cb); return b

    def rot_row(axis):
        row = FBHBoxLayout()
        lbl = FBLabel(); lbl.Caption = axis + ':'
        row.Add(lbl, 22)
        for deg in (-90, -10, 10, 90):
            row.Add(btn('{:+d}'.format(deg), _make_rot_cb(axis, deg)), 50)
        return row

    # ── RIGID BODY SOURCE
    lyt_src = FBHBoxLayout()
    g_ui['list_models']  = FBList()
    g_ui['btn_refresh']  = btn('Refresh', OnRefreshClick)
    lyt_src.Add(g_ui['list_models'],  160)
    lyt_src.Add(g_ui['btn_refresh'],   70)

    g_ui['btn_create'] = btn('Create & Attach Camera', OnCreateCameraClick)

    lyt_cam_ctrl = FBHBoxLayout()
    g_ui['btn_set_active'] = btn('Set Active', OnSetActiveClick)
    g_ui['btn_detach']     = btn('Detach', OnDetachClick)
    g_ui['btn_del_vcam']   = btn('Del VCam', OnDeleteVCamClick)
    lyt_cam_ctrl.Add(g_ui['btn_set_active'], 100)
    lyt_cam_ctrl.Add(g_ui['btn_detach'],      65)
    lyt_cam_ctrl.Add(g_ui['btn_del_vcam'],    65)

    # ── OSC SOURCE
    lyt_osc_ip = FBHBoxLayout()
    lbl_osc_ip = FBLabel(); lbl_osc_ip.Caption = 'Bind IP:'
    g_ui['edit_osc_ip'] = FBEdit()
    g_ui['edit_osc_ip'].Text = '0.0.0.0'
    lyt_osc_ip.Add(lbl_osc_ip, 60); lyt_osc_ip.Add(g_ui['edit_osc_ip'], 170)

    lyt_osc_top = FBHBoxLayout()
    lbl_osc_p = FBLabel(); lbl_osc_p.Caption = 'Port:'
    g_ui['edit_osc_port'] = FBEditNumber()
    g_ui['edit_osc_port'].Min = 1024; g_ui['edit_osc_port'].Max = 65535
    g_ui['edit_osc_port'].Value = 9007; g_ui['edit_osc_port'].Precision = 0
    g_ui['btn_osc_toggle'] = btn('Connect', OnOSCToggleClick)
    lyt_osc_top.Add(lbl_osc_p, 38)
    lyt_osc_top.Add(g_ui['edit_osc_port'], 60)
    lyt_osc_top.Add(g_ui['btn_osc_toggle'], 130)
    g_ui['btn_create_osc_rb'] = btn('Create OSC Source & RigidBody', OnCreateOSCRigidBodyClick)
    g_ui['btn_reset_osc_data'] = btn('🔄 Reset OSC Data Null', OnResetOSCDataClick)

    # ── OPENVR SOURCE
    _ovr_label = 'Connect OpenVR' if _OVR_OK else 'OpenVR: module not found'
    lyt_ovr = FBHBoxLayout()
    lbl_ovr_dev = FBLabel(); lbl_ovr_dev.Caption = 'Device:'
    g_ui['edit_ovr_dev'] = FBEditNumber()
    g_ui['edit_ovr_dev'].Min = 0; g_ui['edit_ovr_dev'].Max = 15
    g_ui['edit_ovr_dev'].Value = 1; g_ui['edit_ovr_dev'].Precision = 0
    g_ui['edit_ovr_dev'].OnChange.Add(OnOVRDeviceChange)
    g_ui['btn_ovr_toggle'] = btn(_ovr_label, OnOVRToggleClick)
    lyt_ovr.Add(lbl_ovr_dev, 50)
    lyt_ovr.Add(g_ui['edit_ovr_dev'], 45)
    lyt_ovr.Add(g_ui['btn_ovr_toggle'], 135)

    lyt_ovr_ctrl = FBHBoxLayout()
    lbl_ovr_ctrl = FBLabel(); lbl_ovr_ctrl.Caption = 'Controller Dev:'
    g_ui['edit_ovr_ctrl'] = FBEditNumber()
    g_ui['edit_ovr_ctrl'].Min = 0; g_ui['edit_ovr_ctrl'].Max = 15
    g_ui['edit_ovr_ctrl'].Value = g_state.get('ovr_ctrl_device', 1)
    g_ui['edit_ovr_ctrl'].Precision = 0
    def OnOVRCtrlChange(c, e): g_state['ovr_ctrl_device'] = int(c.Value)
    g_ui['edit_ovr_ctrl'].OnChange.Add(OnOVRCtrlChange)
    lyt_ovr_ctrl.Add(lbl_ovr_ctrl, 85)
    lyt_ovr_ctrl.Add(g_ui['edit_ovr_ctrl'], 45)

    g_ui['btn_create_ovr_rb'] = btn('Create OpenVR Source & RigidBody', OnCreateOVRRigidBodyClick)
    g_ui['btn_reset_ovr_data'] = btn('🔄 Reset OpenVR Data Null', OnResetOVRDataClick)

    # ── MOUNTING OFFSET
    g_ui['btn_reset'] = btn('Reset to +Z (default)', OnResetOffsetClick)
    g_ui['rot_x'] = rot_row('X')
    g_ui['rot_y'] = rot_row('Y')
    g_ui['rot_z'] = rot_row('Z')

    # ── OSC AXIS DIRECTION (Flip toggles)
    _FLIP_NAMES    = ['Tx', 'Ty', 'Tz', 'Rx', 'Ry', 'Rz']
    _FLIP_DEFAULTS = g_state.get('osc_flip', [1,1,-1,1,-1,-1])

    def _make_flip_cb(axis_idx):
        name = _FLIP_NAMES[axis_idx]
        key  = 'flip_' + name.lower()
        def cb(c, e):
            flip = g_state.get('osc_flip', [1,1,-1,1,-1,-1])
            flip[axis_idx] = -flip[axis_idx]
            g_state['osc_flip'] = flip
            c.Caption = name + (' (+)' if flip[axis_idx] > 0 else ' (-)')
        return cb

    lyt_flip_pos = FBHBoxLayout()
    lbl_fp = FBLabel(); lbl_fp.Caption = 'POS:'
    lyt_flip_pos.Add(lbl_fp, 35)
    for i in range(3):
        sign = '(+)' if _FLIP_DEFAULTS[i] > 0 else '(-)'
        b = btn(_FLIP_NAMES[i] + ' ' + sign, _make_flip_cb(i))
        g_ui['flip_' + _FLIP_NAMES[i].lower()] = b
        lyt_flip_pos.Add(b, 65)

    lyt_flip_rot = FBHBoxLayout()
    lbl_fr = FBLabel(); lbl_fr.Caption = 'ROT:'
    lyt_flip_rot.Add(lbl_fr, 35)
    for i in range(3, 6):
        sign = '(+)' if _FLIP_DEFAULTS[i] > 0 else '(-)'
        b = btn(_FLIP_NAMES[i] + ' ' + sign, _make_flip_cb(i))
        g_ui['flip_' + _FLIP_NAMES[i].lower()] = b
        lyt_flip_rot.Add(b, 65)

    # ── FOV / ZOOM
    lyt_fov = FBHBoxLayout()
    lbl_fov = FBLabel(); lbl_fov.Caption = 'FOV:'
    g_ui['edit_fov'] = FBEditNumber()
    g_ui['edit_fov'].Min = FOV_MIN; g_ui['edit_fov'].Max = FOV_MAX
    g_ui['edit_fov'].Value = g_state['fov']; g_ui['edit_fov'].Precision = 1
    g_ui['edit_fov'].OnChange.Add(OnFOVChange)
    lyt_fov.Add(lbl_fov, 35); lyt_fov.Add(g_ui['edit_fov'], 195)

    lyt_zoom = FBHBoxLayout()
    g_ui['btn_wider']   = btn('Zoom Out', OnZoomOutClick)
    g_ui['btn_tighter'] = btn('Zoom In',  OnZoomInClick)
    lyt_zoom.Add(g_ui['btn_wider'],  115); lyt_zoom.Add(g_ui['btn_tighter'], 115)

    lyt_gp = FBHBoxLayout()
    g_ui['chk_gamepad'] = FBButton()
    g_ui['chk_gamepad'].Style   = FBButtonStyle.kFBCheckbox
    g_ui['chk_gamepad'].Caption = 'Gamepad (Xbox)'
    g_ui['chk_gamepad'].State   = 1 if g_state['gamepad_enabled'] else 0
    g_ui['chk_gamepad'].OnClick.Add(OnGamepadToggle)
    lyt_gp.Add(g_ui['chk_gamepad'], 230)

    # ── CAPTURE
    lyt_snap_path = FBHBoxLayout()
    lbl_sp = FBLabel(); lbl_sp.Caption = 'Save:'
    g_ui['edit_snap_path'] = FBEdit()
    g_ui['edit_snap_path'].Text = os.path.join(os.path.expanduser('~'), 'Desktop')
    g_ui['btn_browse_snap'] = btn('Browse', OnBrowseSnapClick)
    lyt_snap_path.Add(lbl_sp, 35)
    lyt_snap_path.Add(g_ui['edit_snap_path'], 130)
    lyt_snap_path.Add(g_ui['btn_browse_snap'], 65)

    lyt_capture = FBHBoxLayout()
    g_ui['btn_record']   = btn('🔴 Record',    OnRecordClick)
    g_ui['btn_snapshot'] = btn('📷 Snapshot',  OnSnapshotClick)
    lyt_capture.Add(g_ui['btn_record'],   115)
    lyt_capture.Add(g_ui['btn_snapshot'], 115)

    # ── Status
    g_ui['lbl_status'] = FBLabel()
    g_ui['lbl_status'].Caption = 'Status: Ready'
    tool.SetControl('status', g_ui['lbl_status'])

    # ── Assemble layouts
    view_osc.Add(lyt_osc_ip,                         30)
    view_osc.Add(lyt_osc_top,                        35)
    view_osc.Add(g_ui['btn_create_osc_rb'],          35)
    view_osc.Add(g_ui['btn_reset_osc_data'],         35)

    view_ovr.Add(lyt_ovr,                            35)
    view_ovr.Add(lyt_ovr_ctrl,                       35)
    view_ovr.Add(g_ui['btn_create_ovr_rb'],          35)
    view_ovr.Add(g_ui['btn_reset_ovr_data'],         35)

    view_vcam.Add(hdr('RIGID BODY SOURCE'),           25)
    view_vcam.Add(lyt_src,                            32)
    view_vcam.Add(g_ui['btn_create'],                 35)
    view_vcam.Add(lyt_cam_ctrl,                       35)

    view_vcam.Add(hdr('MOUNTING OFFSET'),             25)
    view_vcam.Add(g_ui['btn_reset'],            30)
    view_vcam.Add(g_ui['rot_x'],                30)
    view_vcam.Add(g_ui['rot_y'],                30)
    view_vcam.Add(g_ui['rot_z'],                30)

    view_vcam.Add(hdr('OSC AXIS DIRECTION'),    25)
    view_vcam.Add(lyt_flip_pos,                 35)
    view_vcam.Add(lyt_flip_rot,                 35)

    view_vcam.Add(hdr('ZOOM / FOV'),            25)
    view_vcam.Add(lyt_fov,                      30)
    view_vcam.Add(lyt_zoom,                     35)
    view_vcam.Add(lyt_gp,                       30)

    view_vcam.Add(hdr('CAPTURE'),               25)
    view_vcam.Add(lyt_snap_path,                30)
    view_vcam.Add(lyt_capture,                  35)

    # Auto-populate model list
    OnRefreshClick(None, None)

    # ── NDI Out Tab ──────────────────────────────────────────────────────────────
    def _ndi_row(label, key, default, w1=80, w2=140):
        r = FBHBoxLayout()
        l = FBLabel(); l.Caption = label; r.Add(l, w1)
        e = FBEdit(); e.Text = str(default); g_ui[key] = e; r.Add(e, w2)
        return r

    view_ndi.Add(hdr('NDI SOURCE NAME'), 25)
    view_ndi.Add(_ndi_row('NDI Name:',  'ndi_name', g_state['ndi_name']), 30)
    view_ndi.Add(_ndi_row('FPS Limit:', 'ndi_fps',  g_state['ndi_fps']),  30)

    view_ndi.Add(hdr('CAMERA'), 25)
    g_ui['ndi_cam_list'] = FBList()
    g_ui['ndi_cam_list'].Style = FBListStyle.kFBVerticalList
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBCamera):
            g_ui['ndi_cam_list'].Items.append(comp.Name)
    view_ndi.Add(g_ui['ndi_cam_list'], 90)

    lyt_ndi_cams = FBHBoxLayout()
    _btn_ndi_ref = FBButton(); _btn_ndi_ref.Caption = 'Refresh Cams'
    _btn_ndi_ref.OnClick.Add(OnNDIRefreshCamsClick)
    lyt_ndi_cams.Add(_btn_ndi_ref, 120)
    _btn_ndi_set = FBButton(); _btn_ndi_set.Caption = 'Set to Viewport'
    _btn_ndi_set.OnClick.Add(OnNDISetCamClick)
    lyt_ndi_cams.Add(_btn_ndi_set, 120)
    view_ndi.Add(lyt_ndi_cams, 32)

    view_ndi.Add(hdr('CAPTURE REGION (screen px)'), 22)
    lyt_ndi_xy = FBHBoxLayout()
    _lx = FBLabel(); _lx.Caption = 'X:'; lyt_ndi_xy.Add(_lx, 20)
    g_ui['ndi_x'] = FBEdit(); g_ui['ndi_x'].Text = str(g_state['ndi_cap_x'])
    lyt_ndi_xy.Add(g_ui['ndi_x'], 55)
    _ly = FBLabel(); _ly.Caption = '  Y:'; lyt_ndi_xy.Add(_ly, 28)
    g_ui['ndi_y'] = FBEdit(); g_ui['ndi_y'].Text = str(g_state['ndi_cap_y'])
    lyt_ndi_xy.Add(g_ui['ndi_y'], 55)
    view_ndi.Add(lyt_ndi_xy, 30)

    lyt_ndi_wh = FBHBoxLayout()
    _lw = FBLabel(); _lw.Caption = 'W:'; lyt_ndi_wh.Add(_lw, 20)
    g_ui['ndi_w'] = FBEdit(); g_ui['ndi_w'].Text = str(g_state['ndi_cap_w'])
    lyt_ndi_wh.Add(g_ui['ndi_w'], 55)
    _lh = FBLabel(); _lh.Caption = '  H:'; lyt_ndi_wh.Add(_lh, 28)
    g_ui['ndi_h'] = FBEdit(); g_ui['ndi_h'].Text = str(g_state['ndi_cap_h'])
    lyt_ndi_wh.Add(g_ui['ndi_h'], 55)
    view_ndi.Add(lyt_ndi_wh, 30)

    view_ndi.Add(hdr('STREAM'), 22)
    g_ui['ndi_toggle'] = FBButton()
    g_ui['ndi_toggle'].Caption = 'Stop NDI' if g_state.get('ndi_streaming') else 'Start NDI'
    g_ui['ndi_toggle'].OnClick.Add(OnNDIToggleClick)
    view_ndi.Add(g_ui['ndi_toggle'], 40)

    g_ui['ndi_status'] = FBLabel()
    g_ui['ndi_status'].Caption = 'STREAMING' if g_state.get('ndi_streaming') else 'OFFLINE'
    g_ui['ndi_status'].Justify = FBTextJustify.kFBTextJustifyCenter
    g_ui['ndi_status'].Style   = FBTextStyle.kFBTextStyleBold
    view_ndi.Add(g_ui['ndi_status'], 25)

    _ndi_dll_lbl = FBLabel()
    _ndi_dll_lbl.Caption = 'DLL: ' + (os.path.basename(_ndi_loader.dll_path) if _ndi_loader.dll_path else 'NOT FOUND')
    _ndi_dll_lbl.Justify = FBTextJustify.kFBTextJustifyCenter
    view_ndi.Add(_ndi_dll_lbl, 20)

def CreateTool():
    tool_name = "MobuVCam_NDI_Toolkit"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
    else:
        print('Error creating MobuVCam_NDI_Toolkit')

CreateTool()
