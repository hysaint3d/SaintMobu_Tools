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

sys.vcam_gen_state = {
    'camera':          None,
    'offset_null':     None,
    'target_model':    None,
    'fov':             60.0,
    'is_recording':    False,
    'gamepad_enabled': True,
    'gamepad_index':   0,
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
    skip = {'SaintVCam', 'SaintVCam_Offset'}
    seen, out = set(), []
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, (FBCamera, FBLight)): continue
        if not isinstance(comp, FBModel): continue
        if comp.Name in skip: continue
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

    # FBCamera → child of offset
    cam = FBCamera('SaintVCam')
    cam.Parent = offset
    try: cam.FieldOfView.SetAnimated(True)
    except: pass
    cam.FieldOfView = g_state['fov']
    cam.NearPlane   = 1.0
    cam.FarPlane    = 50000.0
    cam.Show        = True

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

# ── Snapshot ────────────────────────────────────────────────────────────────────
def OnBrowseSnapClick(c, e):
    popup = FBFolderPopup()
    popup.Caption = 'Select Snapshot Folder'
    if popup.Execute() and 'edit_snap_path' in g_ui:
        g_ui['edit_snap_path'].Text = popup.Path

def OnSnapshotClick(c, e):
    save_dir = g_ui['edit_snap_path'].Text.strip() if 'edit_snap_path' in g_ui else os.path.expanduser('~')
    if not os.path.isdir(save_dir):
        try: os.makedirs(save_dir)
        except:
            FBMessageBox('Error', 'Invalid path:\n' + save_dir, 'OK'); return

    ts  = time.strftime('%Y%m%d_%H%M%S')
    fp  = os.path.join(save_dir, 'VCam_{}.png'.format(ts)).replace('\\', '/')
    ps  = (
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
    _update_status('Snapshot saved → VCam_{}.png'.format(ts))

# ── UIIdle (Gamepad polling + FOV keyframing) ──────────────────────────────────
def OnUIIdle(c, e):
    global _prev_btn
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
    tool.StartSizeX = 320
    tool.StartSizeY = 680

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
            row.Add(btn('{:+d}'.format(deg), _make_rot_cb(axis, deg)), 44)
        return row

    # ── RIGID BODY SOURCE
    lyt_src = FBHBoxLayout()
    g_ui['list_models']  = FBList()
    g_ui['btn_refresh']  = btn('Refresh', OnRefreshClick)
    lyt_src.Add(g_ui['list_models'],  200)
    lyt_src.Add(g_ui['btn_refresh'],   78)

    g_ui['btn_create'] = btn('Create & Attach Camera', OnCreateCameraClick)

    lyt_cam_ctrl = FBHBoxLayout()
    g_ui['btn_set_active'] = btn('Set Active Camera', OnSetActiveClick)
    g_ui['btn_detach']     = btn('Detach', OnDetachClick)
    g_ui['btn_del_vcam']   = btn('Delete VCam', OnDeleteVCamClick)
    lyt_cam_ctrl.Add(g_ui['btn_set_active'], 140)
    lyt_cam_ctrl.Add(g_ui['btn_detach'],      78)
    lyt_cam_ctrl.Add(g_ui['btn_del_vcam'],    82)

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
    lyt_fov.Add(lbl_fov, 35); lyt_fov.Add(g_ui['edit_fov'], 245)

    lyt_zoom = FBHBoxLayout()
    g_ui['btn_wider']   = btn('◀ Wider',    OnZoomOutClick)
    g_ui['btn_tighter'] = btn('Tighter ▶',  OnZoomInClick)
    lyt_zoom.Add(g_ui['btn_wider'],  148); lyt_zoom.Add(g_ui['btn_tighter'], 148)

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
    lyt_gp.Add(g_ui['chk_gamepad'], 165)
    lyt_gp.Add(lbl_gi, 38); lyt_gp.Add(g_ui['edit_gp_idx'], 55)

    # ── CAPTURE
    lyt_snap_path = FBHBoxLayout()
    lbl_sp = FBLabel(); lbl_sp.Caption = 'Save:'
    g_ui['edit_snap_path'] = FBEdit()
    g_ui['edit_snap_path'].Text = os.path.join(os.path.expanduser('~'), 'Desktop')
    g_ui['btn_browse_snap'] = btn('Browse', OnBrowseSnapClick)
    lyt_snap_path.Add(lbl_sp, 38)
    lyt_snap_path.Add(g_ui['edit_snap_path'], 175)
    lyt_snap_path.Add(g_ui['btn_browse_snap'], 80)

    lyt_capture = FBHBoxLayout()
    g_ui['btn_record']   = btn('🔴 Record',    OnRecordClick)
    g_ui['btn_snapshot'] = btn('📷 Snapshot',  OnSnapshotClick)
    lyt_capture.Add(g_ui['btn_record'],   148)
    lyt_capture.Add(g_ui['btn_snapshot'], 148)

    # ── Status
    g_ui['lbl_status'] = FBLabel()
    g_ui['lbl_status'].Caption = 'Status: Ready'

    # ── Assemble layout
    lay = g_ui['main_layout']
    lay.Add(hdr('RIGID BODY SOURCE'),     25)
    lay.Add(lyt_src,                      32)
    lay.Add(g_ui['btn_create'],           35)
    lay.Add(lyt_cam_ctrl,                 35)
    lay.Add(hdr('MOUNTING OFFSET'),       25)
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
