"""
VCam_Generator.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Virtual Camera Generator for MotionBuilder.
Attaches a virtual FBCamera to any 6DOF rigid body / Null / Skeleton in scene.

Workflow:
  1. Select a scene model → Create & Attach Camera
  2. Set as Active Camera to view through it
  3. Adjust Mounting Offset if lens direction needs correction
  4. Use FOV slider or Xbox gamepad (LT/RT) to zoom
  5. Record (FOV keyframed) / Snapshot (viewport → PNG)

Gamepad: Xbox-compatible BT controller via XInput (ctypes, no third-party libs)
  LT = Zoom Out (FOV↑)   RT = Zoom In (FOV↓)
  RB = Snapshot           Start = Record toggle

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
if hasattr(sys, 'vcam_gen_state') and sys.vcam_gen_state:
    try: FBSystem().OnUIIdle.Remove(sys.vcam_gen_idle_func)
    except: pass
    _s = sys.vcam_gen_state.get('osc_socket')
    if _s:
        try: _s.close()
        except: pass

sys.vcam_gen_state = {
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
    # ARKit axis direction: [Tx, Ty, Tz, Rx, Ry, Rz] each +1 or -1
    'osc_flip':        [1, 1, -1, 1, -1, -1],
    # OpenVR
    'ovr_system':      None,
    'ovr_listening':   False,
    'ovr_device':      0,       # tracked device index (0 = HMD)
}
g_state = sys.vcam_gen_state
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
XBTN_RB    = 0x0200
GP_DEAD    = 30      # trigger dead-zone (0-255)
GP_SPEED   = 0.06   # FOV degrees per trigger unit per idle frame
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
        sys.vcam_gen_idle_func = OnUIIdle
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
        sys.vcam_gen_idle_func = OnUIIdle
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

# ── FOV constants ──────────────────────────────────────────────────────────────
FOV_MIN   = 5.0
FOV_MAX   = 170.0
FOV_STEP  = 5.0

# ── Helpers ────────────────────────────────────────────────────────────────────
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
            cam.FieldOfView = val
            if g_state['is_recording']:
                cam.FieldOfView.Key()
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
    sys.vcam_gen_idle_func = OnUIIdle

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
    global _prev_btn

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
                try: osc_null.SetVector(FBVector3d(tx, ty, tz), FBModelTransformationType.kModelTranslation, True)
                except: pass
            if rx is not None:
                try: osc_null.SetVector(FBVector3d(rx, ry, rz), FBModelTransformationType.kModelRotation, True)
                except: pass

    # ── Fallback bridge: direct RigidBody drive when Constraint not connected ──
    any_source = g_state['osc_listening'] or g_state['ovr_listening']
    if any_source and not g_state.get('osc_con_ok'):
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

    # ── OpenVR polling (drives same T/R pipeline as OSC) ──────────────────
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
                osc_null = g_state.get('osc_data_null')
                if osc_null:
                    try: osc_null.SetVector(FBVector3d(tx,ty,tz), FBModelTransformationType.kModelTranslation, True)
                    except: pass
                    try: osc_null.SetVector(FBVector3d(rx,ry,rz), FBModelTransformationType.kModelRotation, True)
                    except: pass
                if not g_state.get('osc_con_ok'):
                    rb2 = g_state.get('osc_rigid_body')
                    if rb2:
                        try:
                            rb2.SetVector(FBVector3d(tx,ty,tz), FBModelTransformationType.kModelTranslation, True)
                            rb2.SetVector(FBVector3d(rx,ry,rz), FBModelTransformationType.kModelRotation, True)
                            FBSystem().Scene.Evaluate()
                        except: pass
        except: pass

    # ── Per-frame keyframing during recording ──────────────────────────────
    if g_state['is_recording']:
        model = g_state['target_model']
        if model:
            try:
                model.Translation.Key()   # Rigid body world position
                model.Rotation.Key()      # Rigid body world rotation
            except: pass
        cam = g_state['camera']
        if cam:
            try: cam.FieldOfView.Key()    # VCam FOV
            except: pass

    # ── Gamepad polling ────────────────────────────────────────────────────
    if not g_state['gamepad_enabled']: return
    gp = _read_gamepad()
    if not gp: return

    lt = max(0, gp.bLeftTrigger  - GP_DEAD)
    rt = max(0, gp.bRightTrigger - GP_DEAD)
    if lt > 0 or rt > 0:
        _set_fov(g_state['fov'] + (lt - rt) * GP_SPEED)

    btn     = gp.wButtons
    pressed = btn & ~_prev_btn
    if pressed & XBTN_RB:    OnSnapshotClick(None, None)
    if pressed & XBTN_START:
        if 'btn_record' in g_ui: OnRecordClick(g_ui['btn_record'], None)
    _prev_btn = btn

# ── UI ─────────────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 400
    tool.StartSizeY = 1000

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   '')
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    '')
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  '')
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, '')
    tool.AddRegion('main', 'main', x, y, w, h)

    g_ui['main_layout'] = FBVBoxLayout()
    tool.SetControl('main', g_ui['main_layout'])

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
            row.Add(btn('{:+d}'.format(deg), _make_rot_cb(axis, deg)), 88)
        return row

    # ── RIGID BODY SOURCE
    lyt_src = FBHBoxLayout()
    g_ui['list_models']  = FBList()
    g_ui['btn_refresh']  = btn('Refresh', OnRefreshClick)
    lyt_src.Add(g_ui['list_models'],  268)
    lyt_src.Add(g_ui['btn_refresh'],   90)

    g_ui['btn_create'] = btn('Create & Attach Camera', OnCreateCameraClick)

    lyt_cam_ctrl = FBHBoxLayout()
    g_ui['btn_set_active'] = btn('Set Active Camera', OnSetActiveClick)
    g_ui['btn_detach']     = btn('Detach', OnDetachClick)
    g_ui['btn_del_vcam']   = btn('Delete VCam', OnDeleteVCamClick)
    lyt_cam_ctrl.Add(g_ui['btn_set_active'], 170)
    lyt_cam_ctrl.Add(g_ui['btn_detach'],      96)
    lyt_cam_ctrl.Add(g_ui['btn_del_vcam'],    96)

    # ── OSC SOURCE
    lyt_osc_ip = FBHBoxLayout()
    lbl_osc_ip = FBLabel(); lbl_osc_ip.Caption = 'Bind IP:'
    g_ui['edit_osc_ip'] = FBEdit()
    g_ui['edit_osc_ip'].Text = '0.0.0.0'
    lyt_osc_ip.Add(lbl_osc_ip, 60); lyt_osc_ip.Add(g_ui['edit_osc_ip'], 300)

    lyt_osc_top = FBHBoxLayout()
    lbl_osc_p = FBLabel(); lbl_osc_p.Caption = 'Port:'
    g_ui['edit_osc_port'] = FBEditNumber()
    g_ui['edit_osc_port'].Min = 1024; g_ui['edit_osc_port'].Max = 65535
    g_ui['edit_osc_port'].Value = 9007; g_ui['edit_osc_port'].Precision = 0
    g_ui['btn_osc_toggle'] = btn('Connect', OnOSCToggleClick)
    lyt_osc_top.Add(lbl_osc_p, 38)
    lyt_osc_top.Add(g_ui['edit_osc_port'], 90)
    lyt_osc_top.Add(g_ui['btn_osc_toggle'], 240)
    g_ui['btn_create_osc_rb'] = btn('Create OSC Source & RigidBody', OnCreateOSCRigidBodyClick)
    g_ui['btn_reset_osc_data'] = btn('🔄 Reset OSC Data Null', OnResetOSCDataClick)

    # ── OPENVR SOURCE
    _ovr_label = 'Connect OpenVR' if _OVR_OK else 'OpenVR: module not found'
    lyt_ovr = FBHBoxLayout()
    lbl_ovr_dev = FBLabel(); lbl_ovr_dev.Caption = 'Device:'
    g_ui['edit_ovr_dev'] = FBEditNumber()
    g_ui['edit_ovr_dev'].Min = 0; g_ui['edit_ovr_dev'].Max = 15
    g_ui['edit_ovr_dev'].Value = 0; g_ui['edit_ovr_dev'].Precision = 0
    g_ui['edit_ovr_dev'].OnChange.Add(OnOVRDeviceChange)
    g_ui['btn_ovr_toggle'] = btn(_ovr_label, OnOVRToggleClick)
    lyt_ovr.Add(lbl_ovr_dev, 52)
    lyt_ovr.Add(g_ui['edit_ovr_dev'], 55)
    lyt_ovr.Add(g_ui['btn_ovr_toggle'], 263)

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
    lyt_flip_pos.Add(lbl_fp, 38)
    for i in range(3):
        sign = '(+)' if _FLIP_DEFAULTS[i] > 0 else '(-)'
        b = btn(_FLIP_NAMES[i] + ' ' + sign, _make_flip_cb(i))
        g_ui['flip_' + _FLIP_NAMES[i].lower()] = b
        lyt_flip_pos.Add(b, 117)

    lyt_flip_rot = FBHBoxLayout()
    lbl_fr = FBLabel(); lbl_fr.Caption = 'ROT:'
    lyt_flip_rot.Add(lbl_fr, 38)
    for i in range(3, 6):
        sign = '(+)' if _FLIP_DEFAULTS[i] > 0 else '(-)'
        b = btn(_FLIP_NAMES[i] + ' ' + sign, _make_flip_cb(i))
        g_ui['flip_' + _FLIP_NAMES[i].lower()] = b
        lyt_flip_rot.Add(b, 117)

    # ── FOV / ZOOM
    lyt_fov = FBHBoxLayout()
    lbl_fov = FBLabel(); lbl_fov.Caption = 'FOV:'
    g_ui['edit_fov'] = FBEditNumber()
    g_ui['edit_fov'].Min = FOV_MIN; g_ui['edit_fov'].Max = FOV_MAX
    g_ui['edit_fov'].Value = g_state['fov']; g_ui['edit_fov'].Precision = 1
    g_ui['edit_fov'].OnChange.Add(OnFOVChange)
    lyt_fov.Add(lbl_fov, 35); lyt_fov.Add(g_ui['edit_fov'], 320)

    lyt_zoom = FBHBoxLayout()
    g_ui['btn_wider']   = btn('Zoom Out', OnZoomOutClick)
    g_ui['btn_tighter'] = btn('Zoom In',  OnZoomInClick)
    lyt_zoom.Add(g_ui['btn_wider'],  188); lyt_zoom.Add(g_ui['btn_tighter'], 188)

    lyt_gp = FBHBoxLayout()
    g_ui['chk_gamepad'] = FBButton()
    g_ui['chk_gamepad'].Style   = FBButtonStyle.kFBCheckbox
    g_ui['chk_gamepad'].Caption = 'Gamepad Zoom (LT/RT)'
    g_ui['chk_gamepad'].State   = 1
    g_ui['chk_gamepad'].OnClick.Add(OnGamepadToggle)
    lbl_gi = FBLabel(); lbl_gi.Caption = ' Ctrl:'
    g_ui['edit_gp_idx'] = FBEditNumber()
    g_ui['edit_gp_idx'].Min = 0; g_ui['edit_gp_idx'].Max = 3
    g_ui['edit_gp_idx'].Value = 0; g_ui['edit_gp_idx'].Precision = 0
    g_ui['edit_gp_idx'].OnChange.Add(OnGPIndexChange)
    lyt_gp.Add(g_ui['chk_gamepad'], 200)
    lyt_gp.Add(lbl_gi, 45); lyt_gp.Add(g_ui['edit_gp_idx'], 65)

    # ── CAPTURE
    lyt_snap_path = FBHBoxLayout()
    lbl_sp = FBLabel(); lbl_sp.Caption = 'Save:'
    g_ui['edit_snap_path'] = FBEdit()
    g_ui['edit_snap_path'].Text = os.path.join(os.path.expanduser('~'), 'Desktop')
    g_ui['btn_browse_snap'] = btn('Browse', OnBrowseSnapClick)
    lyt_snap_path.Add(lbl_sp, 38)
    lyt_snap_path.Add(g_ui['edit_snap_path'], 230)
    lyt_snap_path.Add(g_ui['btn_browse_snap'], 90)

    lyt_capture = FBHBoxLayout()
    g_ui['btn_record']   = btn('🔴 Record',    OnRecordClick)
    g_ui['btn_snapshot'] = btn('📷 Snapshot',  OnSnapshotClick)
    lyt_capture.Add(g_ui['btn_record'],   188)
    lyt_capture.Add(g_ui['btn_snapshot'], 188)

    # ── Status
    g_ui['lbl_status'] = FBLabel()
    g_ui['lbl_status'].Caption = 'Status: Ready'

    # ── Assemble layout
    lay = g_ui['main_layout']
    lay.Add(hdr('OSC SOURCE'),              25)
    lay.Add(lyt_osc_ip,                         30)
    lay.Add(lyt_osc_top,                        35)
    lay.Add(g_ui['btn_create_osc_rb'],          35)
    lay.Add(g_ui['btn_reset_osc_data'],         35)
    lay.Add(hdr('OPENVR SOURCE'),               25)
    lay.Add(lyt_ovr,                            35)
    lay.Add(hdr('RIGID BODY SOURCE'),           25)
    lay.Add(lyt_src,                            32)
    lay.Add(g_ui['btn_create'],                 35)
    lay.Add(lyt_cam_ctrl,                       35)
    lay.Add(hdr('MOUNTING OFFSET'),             25)
    lay.Add(g_ui['btn_reset'],            30)
    lay.Add(g_ui['rot_x'],                30)
    lay.Add(g_ui['rot_y'],                30)
    lay.Add(g_ui['rot_z'],                30)
    lay.Add(hdr('OSC AXIS DIRECTION'),    25)
    lay.Add(lyt_flip_pos,                 35)
    lay.Add(lyt_flip_rot,                 35)
    lay.Add(hdr('ZOOM / FOV'),            25)
    lay.Add(lyt_fov,                      30)
    lay.Add(lyt_zoom,                     35)
    lay.Add(lyt_gp,                       30)
    lay.Add(hdr('CAPTURE'),               25)
    lay.Add(lyt_snap_path,                30)
    lay.Add(lyt_capture,                  35)
    lay.Add(g_ui['lbl_status'],           30)

    # Auto-populate model list
    OnRefreshClick(None, None)


def CreateTool():
    tool_name = "Saint's Virtual Camera System"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox('Welcome',
            "Saint's VCS — Virtual Camera System\n"
            "由小聖腦絲與Antigravity協作完成\n"
            "https://www.facebook.com/hysaint3d.mocap", 'OK')
    else:
        print('Error creating VCam tool')

CreateTool()
