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

# ── ZIG SIM Pro OSC Source ─────────────────────────────────────────────────────
def _osc_str(data, off):
    end = data.index(b'\x00', off)
    s = data[off:end].decode('utf-8', errors='ignore')
    return s, (end + 4) & ~3

def _osc_parse(data):
    try:
        addr, off = _osc_str(data, 0)
        tag,  off = _osc_str(data, off)
        if not tag.startswith(','): return None
        args = []
        for c in tag[1:]:
            if   c == 'f': args.append(struct.unpack('>f', data[off:off+4])[0]); off += 4
            elif c == 'i': args.append(struct.unpack('>i', data[off:off+4])[0]); off += 4
            elif c == 's': s, off = _osc_str(data, off); args.append(s)
        return addr, args
    except: return None

def _quat_to_euler(qx, qy, qz, qw):
    """Quaternion → XYZ Euler degrees."""
    rx = math.degrees(math.atan2(2*(qw*qx+qy*qz), 1-2*(qx*qx+qy*qy)))
    sinp = max(-1.0, min(1.0, 2*(qw*qy-qz*qx)))
    ry = math.degrees(math.asin(sinp))
    rz = math.degrees(math.atan2(2*(qw*qz+qx*qy), 1-2*(qy*qy+qz*qz)))
    return rx, ry, rz

def _apply_zigsim(addr, args):
    """Apply ZIG SIM ARKit OSC data to the OSC Rigid Body (ZigSim_RigidBody)."""
    rb = g_state.get('osc_rigid_body')
    if not rb: return
    # Position: /zigsim/<uuid>/arposition  (x, y, z) metres → MB centimetres
    if addr.endswith('/arposition') and len(args) >= 3:
        rb.SetVector(FBVector3d(args[0]*100, args[1]*100, args[2]*100),
                     FBModelTransformationType.kModelTranslation, True)
        FBSystem().Scene.Evaluate()
    # Rotation: /zigsim/<uuid>/arqt or /attitude  (x, y, z, w) quaternion
    elif addr.endswith(('/arqt', '/attitude')) and len(args) >= 4:
        rx, ry, rz = _quat_to_euler(args[0], args[1], args[2], args[3])
        rb.SetVector(FBVector3d(rx, ry, rz),
                     FBModelTransformationType.kModelRotation, True)
        FBSystem().Scene.Evaluate()

def _poll_osc():
    """Non-blocking drain of incoming OSC UDP packets."""
    sock = g_state.get('osc_socket')
    if not sock: return
    try:
        while True:
            data, _ = sock.recvfrom(4096)
            pkt = _osc_parse(data)
            if pkt: _apply_zigsim(*pkt)
    except: pass

def _start_osc():
    port = int(g_ui['edit_osc_port'].Value) if 'edit_osc_port' in g_ui else 9007
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind(('0.0.0.0', port))
        s.setblocking(False)
        g_state['osc_socket']    = s
        g_state['osc_listening'] = True
        if 'btn_osc_toggle' in g_ui:
            g_ui['btn_osc_toggle'].Caption = '⏹ Stop OSC'
        _update_status('OSC listening on :{}'.format(port))
    except Exception as ex:
        FBMessageBox('Error', 'Cannot bind port {}:\n{}'.format(port, str(ex)), 'OK')

def _stop_osc():
    s = g_state.get('osc_socket')
    if s:
        try: s.close()
        except: pass
    g_state['osc_socket']    = None
    g_state['osc_listening'] = False
    if 'btn_osc_toggle' in g_ui:
        g_ui['btn_osc_toggle'].Caption = '▶ Start OSC'
    _update_status('OSC stopped.')

def OnOSCToggleClick(c, e):
    if g_state['osc_listening']: _stop_osc()
    else: _start_osc()

def OnCreateOSCRigidBodyClick(c, e):
    """Create a scene Null driven by ZIG SIM OSC data."""
    # Delete existing one if present
    existing = g_state.get('osc_rigid_body')
    if existing:
        try: existing.FBDelete()
        except: pass
    rb = FBModelNull('ZigSim_RigidBody')
    rb.Show   = True
    rb.Size   = 30.0
    rb.Selected = True
    g_state['osc_rigid_body'] = rb
    FBSystem().Scene.Evaluate()
    # Auto-refresh model dropdown and select new rigid body
    OnRefreshClick(None, None)
    lst = g_ui.get('list_models')
    if lst:
        for i in range(len(lst.Items)):
            if lst.Items[i] == 'ZigSim_RigidBody':
                lst.ItemIndex = i
                break
    _update_status('ZigSim_RigidBody created. Start OSC then Create & Attach Camera.')

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
    skip = {'SaintVCam', 'SaintVCam_Offset', 'Camera Switcher', 'CameraSwitcher', 'CameraInterest'}
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
    # Reset local T/R to zero → places offset AT the rigid body's world position
    offset.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelTranslation, False)
    offset.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
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
        FBSystem().Scene.Renderer.CurrentCamera = cam
        _update_status('SaintVCam is now the active camera.')
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
        null.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
        FBSystem().Scene.Evaluate()
        _update_status('Mounting offset reset to +Z.')
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

    # ── OSC polling (always, regardless of gamepad state) ────────────────────
    _poll_osc()

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
    tool.StartSizeY = 750

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
        for deg in (-90, -10, -1, 1, 10, 90):
            row.Add(btn('{:+d}'.format(deg), _make_rot_cb(axis, deg)), 52)
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

    # ── OSC SOURCE (ZIG SIM Pro)
    lyt_osc_top = FBHBoxLayout()
    lbl_osc_p = FBLabel(); lbl_osc_p.Caption = 'Port:'
    g_ui['edit_osc_port'] = FBEditNumber()
    g_ui['edit_osc_port'].Min = 1024; g_ui['edit_osc_port'].Max = 65535
    g_ui['edit_osc_port'].Value = 9007; g_ui['edit_osc_port'].Precision = 0
    g_ui['btn_osc_toggle'] = btn('▶ Start OSC', OnOSCToggleClick)
    lyt_osc_top.Add(lbl_osc_p, 38)
    lyt_osc_top.Add(g_ui['edit_osc_port'], 90)
    lyt_osc_top.Add(g_ui['btn_osc_toggle'], 240)
    g_ui['btn_create_osc_rb'] = btn('Create OSC Rigid Body (ZigSim)', OnCreateOSCRigidBodyClick)

    # ── MOUNTING OFFSET
    g_ui['btn_reset'] = btn('Reset to +Z (default)', OnResetOffsetClick)
    g_ui['rot_x'] = rot_row('X')
    g_ui['rot_y'] = rot_row('Y')
    g_ui['rot_z'] = rot_row('Z')

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
    lay.Add(hdr('RIGID BODY SOURCE'),          25)
    lay.Add(lyt_src,                           32)
    lay.Add(g_ui['btn_create'],                35)
    lay.Add(lyt_cam_ctrl,                      35)
    lay.Add(hdr('OSC SOURCE (ZIG SIM Pro)'),   25)
    lay.Add(lyt_osc_top,                        35)
    lay.Add(g_ui['btn_create_osc_rb'],          35)
    lay.Add(hdr('MOUNTING OFFSET'),             25)
    lay.Add(g_ui['btn_reset'],            30)
    lay.Add(g_ui['rot_x'],                30)
    lay.Add(g_ui['rot_y'],                30)
    lay.Add(g_ui['rot_z'],                30)
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
