"""
Mobu2NDI_Out.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MotionBuilder → NDI Output Toolkit
Streams MotionBuilder viewport to any NDI receiver (OBS, vMix, NDI Monitor...).

Features:
  - No Python pip dependencies (ctypes + system NDI DLL only)
  - Auto-detect NDI 5 / NDI 6 runtime
  - Camera selector: pick any FBCamera and switch viewport instantly
  - Configurable capture region (X/Y/W/H) for multi-monitor setups
  - Configurable FPS and NDI Source Name
  - Low-latency GDI screen capture (Windows)

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import time
import ctypes
import struct
import threading
from pyfbsdk import *
from pyfbsdk_additions import *

# ── NDI Library Loader ────────────────────────────────────────────────────────
class NDILoader:
    def __init__(self):
        self.lib = None
        self.dll_path = ""
        self._find_dll()

    def _find_dll(self):
        # Common search paths for NDI 6 and 5
        paths = [
            # NDI 6 — Tools bundle (most common for end users)
            r"C:\Program Files\NDI\NDI 6 Tools\Runtime\Processing.NDI.Lib.x64.dll",
            # NDI 6 — standalone Runtime installer
            r"C:\Program Files\NDI\NDI 6 Runtime\v6\Processing.NDI.Lib.x64.dll",
            # NDI 5 fallbacks
            r"C:\Program Files\NDI\NDI 5 Runtime\v5\Processing.NDI.Lib.x64.dll",
            r"C:\Program Files\NDI\NDI 5 SDK\Bin\x64\Processing.NDI.Lib.x64.dll",
        ]
        # Check environment variables set by NDI installer
        for env_key in ("NDI_RUNTIME_DIR_V6", "NDI_RUNTIME_DIR_V5"):
            env_dir = os.environ.get(env_key)
            if env_dir:
                paths.insert(0, os.path.join(env_dir, "Processing.NDI.Lib.x64.dll"))

        for p in paths:
            if os.path.exists(p):
                try:
                    self.lib = ctypes.CDLL(p)
                    self.dll_path = p
                    print(f"[NDI] Found and loaded: {p}")
                    return
                except Exception as e:
                    print(f"[NDI] Failed to load {p}: {e}")
        
        print("[NDI] ERROR: Could not find NDI Runtime DLL. Please install NDI Tools/Runtime.")

    def is_valid(self):
        return self.lib is not None

# ── NDI Structs & Constants ───────────────────────────────────────────────────
# Constants
NDIlib_FourCC_type_BGRX = 0x58524742
NDIlib_FourCC_type_BGRA = 0x41524742

class NDIlib_send_create_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_groups", ctypes.c_char_p),
        ("clock_video", ctypes.c_bool),
        ("clock_audio", ctypes.c_bool)
    ]

class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_int),
        ("yres", ctypes.c_int),
        ("FourCC", ctypes.c_int),
        ("frame_rate_N", ctypes.c_int),
        ("frame_rate_D", ctypes.c_int),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", ctypes.c_int),
        ("timecode", ctypes.c_longlong),
        ("p_data", ctypes.c_void_p),
        ("line_stride_in_bytes", ctypes.c_int),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_longlong)
    ]

# ── Windows GDI Screen Capture (no pip required) ─────────────────────────────
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
    """Capture screen region to a ctypes BGRA buffer (top-down, 32bpp)."""
    hdc_screen = _user32.GetDC(None)
    hdc_mem    = _gdi32.CreateCompatibleDC(hdc_screen)
    hbmp       = _gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    _gdi32.SelectObject(hdc_mem, hbmp)
    _gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y, 0x00CC0020)  # SRCCOPY

    bmi = _BITMAPINFOHEADER()
    bmi.biSize        = ctypes.sizeof(_BITMAPINFOHEADER)
    bmi.biWidth       = w
    bmi.biHeight      = -h   # negative = top-down row order
    bmi.biPlanes      = 1
    bmi.biBitCount    = 32
    bmi.biCompression = 0    # BI_RGB

    buf = (ctypes.c_ubyte * (w * h * 4))()
    _gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    _gdi32.DeleteObject(hbmp)
    _gdi32.DeleteDC(hdc_mem)
    _user32.ReleaseDC(None, hdc_screen)
    return buf

# ── Win32 Window Helpers ──────────────────────────────────────────────────────
_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

def find_video_out_window():
    """Search for the MotionBuilder Video Output window and return its rect."""
    result = {"hwnd": None, "rect": None}
    
    def callback(hwnd, lparam):
        length = _user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            _user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
            # Search for "Video Out" in title
            if "Video Out" in title and "MotionBuilder" in title:
                rect = _RECT()
                _user32.GetWindowRect(hwnd, ctypes.byref(rect))
                result["hwnd"] = hwnd
                result["rect"] = rect
                return False # Stop enumeration
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return result

# ── Global State & Cleanup ───────────────────────────────────────────────────
if not hasattr(sys, "mocaplab_ndi_state"):
    sys.mocaplab_ndi_state = {
        "is_streaming": False,
        "ndi_instance": None,
        "last_time":    0,
        "fps_limit":    30,
        "source_name":  "MocapLab_Viewport",
        "cap_x":  0,
        "cap_y":  0,
        "cap_w":  1280,
        "cap_h":  720,
    }
else:
    _d = sys.mocaplab_ndi_state
    _d.setdefault("cap_x", 0)
    _d.setdefault("cap_y", 0)
    _d.setdefault("cap_w", 1280)
    _d.setdefault("cap_h", 720)
    _d.setdefault("source_name", "MocapLab_Viewport")
    _d.setdefault("fps_limit", 30)

    # Destroy stale NDI sender from previous run before re-creating.
    # g_ndi_loader isn't defined yet, so load the DLL temporarily here.
    _old_inst = _d.get("ndi_instance")
    if _old_inst:
        try:
            _tmp_paths = [
                r"C:\Program Files\NDI\NDI 6 Tools\Runtime\Processing.NDI.Lib.x64.dll",
                r"C:\Program Files\NDI\NDI 6 Runtime\v6\Processing.NDI.Lib.x64.dll",
                r"C:\Program Files\NDI\NDI 5 Runtime\v5\Processing.NDI.Lib.x64.dll",
            ]
            _tmp_lib = None
            for _p in _tmp_paths:
                if os.path.exists(_p):
                    _tmp_lib = ctypes.CDLL(_p); break
            if _tmp_lib:
                _tmp_lib.NDIlib_initialize()
                _tmp_lib.NDIlib_send_destroy(ctypes.c_void_p(_old_inst))
                print("[NDI] Stale sender destroyed.")
        except Exception as _e:
            print("[NDI] Stale destroy error (ignored):", _e)
    _d["ndi_instance"] = None
    _d["is_streaming"] = False

try: FBSystem().OnUIIdle.Remove(sys.get("mocaplab_ndi_idle_fn", None))
except: pass

g_ndi_loader = NDILoader()
g_state = sys.mocaplab_ndi_state
g_ui = {}

# ── NDI Controller ────────────────────────────────────────────────────────────
def StartNDI():
    if not g_ndi_loader.is_valid():
        FBMessageBox("NDI Error", "NDI Runtime DLL not found. Cannot start streaming.", "OK")
        return False

    if g_state["is_streaming"]: return True

    try:
        # Step 0: Initialize NDI
        g_ndi_loader.lib.NDIlib_initialize.restype = ctypes.c_bool
        ok = g_ndi_loader.lib.NDIlib_initialize()
        print("[NDI] NDIlib_initialize() ->", ok)
        if not ok:
            print("[NDI] NDIlib_initialize() failed!")
            return False

        # Step 1: Create sender with custom name via struct.
        #         Cleanup at top already destroyed any stale sender.
        g_ndi_loader.lib.NDIlib_send_create.restype  = ctypes.c_void_p

        create_settings = NDIlib_send_create_t()
        create_settings.p_ndi_name  = g_state["source_name"].encode('utf-8')
        create_settings.p_groups    = None
        create_settings.clock_video = False
        create_settings.clock_audio = False
        g_ndi_loader.lib.NDIlib_send_create.argtypes = [ctypes.POINTER(NDIlib_send_create_t)]
        inst = g_ndi_loader.lib.NDIlib_send_create(ctypes.pointer(create_settings))
        print("[NDI] NDIlib_send_create(struct:'{}') -> {}".format(g_state["source_name"], inst))

        if not inst:
            # Fallback: NULL struct (NDI uses hostname as name)
            g_ndi_loader.lib.NDIlib_send_create.argtypes = [ctypes.c_void_p]
            inst = g_ndi_loader.lib.NDIlib_send_create(None)
            print("[NDI] NDIlib_send_create(NULL fallback) ->", inst)

        if not inst:
            print("[NDI] Failed to create sender instance.")
            return False

        g_state["ndi_instance"] = inst
        g_state["is_streaming"] = True
        FBSystem().OnUIIdle.Add(OnUIIdle)
        print("[NDI] Streaming started: '{}' @ {}x{} {}fps".format(
            g_state["source_name"], g_state["cap_w"], g_state["cap_h"], g_state["fps_limit"]))
        return True
    except Exception as e:
        print("[NDI] Start error: {}".format(e))
        return False

def StopNDI():
    if not g_state["is_streaming"]: return

    g_state["is_streaming"] = False
    try: FBSystem().OnUIIdle.Remove(OnUIIdle)
    except: pass

    if g_state["ndi_instance"] and g_ndi_loader.is_valid():
        g_ndi_loader.lib.NDIlib_send_destroy(
            ctypes.c_void_p(g_state["ndi_instance"]))
        g_state["ndi_instance"] = None

    print("[NDI] Streaming stopped.")

def OnUIIdle(control, event):
    if not g_state["is_streaming"] or not g_state["ndi_instance"]:
        return

    # FPS Control
    now = time.time()
    interval = 1.0 / g_state["fps_limit"]
    if now - g_state["last_time"] < interval:
        return
    g_state["last_time"] = now

    try:
        x = g_state["cap_x"]
        y = g_state["cap_y"]
        w = g_state["cap_w"]
        h = g_state["cap_h"]

        # Capture screen region via Windows GDI (no pip required)
        buf = _gdi_capture(x, y, w, h)

        # Build NDI video frame (GDI BGRA matches NDI BGRX — alpha byte ignored)
        vf = NDIlib_video_frame_v2_t()
        vf.xres                 = w
        vf.yres                 = h
        vf.FourCC               = NDIlib_FourCC_type_BGRX
        vf.frame_rate_N         = g_state["fps_limit"]
        vf.frame_rate_D         = 1
        vf.picture_aspect_ratio = float(w) / float(h)
        vf.frame_format_type    = 0   # Progressive
        vf.timecode             = 0
        vf.p_data               = ctypes.cast(buf, ctypes.c_void_p).value
        vf.line_stride_in_bytes = w * 4
        vf.p_metadata           = None
        vf.timestamp            = 0

        g_ndi_loader.lib.NDIlib_send_send_video_v2(
            ctypes.c_void_p(g_state["ndi_instance"]),
            ctypes.byref(vf)
        )
    except Exception as e:
        print("[NDI] Frame send error: {}".format(e))

# ── Camera Helpers ────────────────────────────────────────────────────────────
def _get_cameras():
    """Return all FBCamera objects from the current scene."""
    cams = []
    user_cams = []
    sys_cams  = []
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBCamera):
            if comp.SystemCamera:
                sys_cams.append(comp)
            else:
                user_cams.append(comp)
    # User cameras first, then system cameras
    return user_cams + sys_cams

def OnRefreshCameras(control, event):
    lst = g_ui.get("cam_list")
    if not lst: return
    lst.Items.removeAll()
    for c in _get_cameras():
        lst.Items.append(c.Name)
    print("[NDI] Camera list refreshed: {} cameras found.".format(lst.Items.count))

def OnSetCameraClick(control, event):
    lst = g_ui.get("cam_list")
    if not lst: return
    idx = lst.ItemIndex
    if idx < 0:
        print("[NDI] No camera selected."); return
    cams = _get_cameras()
    if idx >= len(cams):
        print("[NDI] Camera index out of range."); return
    cam = cams[idx]
    try:
        FBSystem().Renderer.SetCameraInPane(cam, 0)
        print("[NDI] Viewport camera set to: {}".format(cam.Name))
        g_state["active_camera"] = cam.Name
    except Exception as e:
        print("[NDI] SetCameraInPane error: {}".format(e))

def OnDetectVideoOut(control, event):
    res = find_video_out_window()
    if res["rect"]:
        r = res["rect"]
        w = r.right - r.left
        h = r.bottom - r.top
        g_ui["edit_x"].Text = str(r.left)
        g_ui["edit_y"].Text = str(r.top)
        g_ui["edit_w"].Text = str(w)
        g_ui["edit_h"].Text = str(h)
        print("[NDI] Detected Video Out at: {},{} ({}x{})".format(r.left, r.top, w, h))
    else:
        FBMessageBox("NDI Out", "Could not find 'Video Out' window.\nMake sure Video Output device is enabled in Mobu.", "OK")

# ── UI Layout ─────────────────────────────────────────────────────────────────
def OnToggleClick(control, event):
    if not g_state["is_streaming"]:
        g_state["source_name"] = g_ui["edit_name"].Text.strip() or "MocapLab_Viewport"
        try: g_state["fps_limit"] = max(1, int(g_ui["edit_fps"].Text))
        except: g_state["fps_limit"] = 30
        try: g_state["cap_x"] = int(g_ui["edit_x"].Text)
        except: g_state["cap_x"] = 0
        try: g_state["cap_y"] = int(g_ui["edit_y"].Text)
        except: g_state["cap_y"] = 0
        try: g_state["cap_w"] = max(64, int(g_ui["edit_w"].Text))
        except: g_state["cap_w"] = 1280
        try: g_state["cap_h"] = max(64, int(g_ui["edit_h"].Text))
        except: g_state["cap_h"] = 720

        if StartNDI():
            g_ui["btn_toggle"].Caption = "Stop NDI Stream"
            g_ui["lbl_status"].Caption = "🔴 STREAMING  ●"
    else:
        StopNDI()
        g_ui["btn_toggle"].Caption = "Start NDI Stream"
        g_ui["lbl_status"].Caption = "⬜ OFFLINE"

def PopulateTool(tool):
    tool.StartSizeX = 310
    tool.StartSizeY = 520

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)

    layout = FBVBoxLayout()
    tool.SetControl("main", layout)

    def hdr(txt):
        l = FBLabel()
        l.Caption = "--- {} ---".format(txt)
        l.Justify = FBTextJustify.kFBTextJustifyCenter
        return l

    def row(label_text, key, default):
        lyt = FBHBoxLayout()
        lbl = FBLabel(); lbl.Caption = label_text
        lyt.Add(lbl, 100)
        edt = FBEdit(); edt.Text = str(default)
        g_ui[key] = edt
        lyt.Add(edt, 160)
        return lyt

    layout.Add(hdr("MOCAPLAB NDI OUT"), 25)
    layout.Add(row("Source Name:", "edit_name", g_state["source_name"]), 30)
    layout.Add(row("FPS Limit:",   "edit_fps",  g_state["fps_limit"]),   30)

    # ── Camera Selection ───────────────────────────────────────────────
    layout.Add(hdr("Camera"), 22)

    g_ui["cam_list"] = FBList()
    g_ui["cam_list"].Style = FBListStyle.kFBVerticalList
    for c in _get_cameras():
        g_ui["cam_list"].Items.append(c.Name)
    layout.Add(g_ui["cam_list"], 90)

    lyt_cam_btns = FBHBoxLayout()
    btn_refresh = FBButton(); btn_refresh.Caption = "Refresh"
    btn_refresh.OnClick.Add(OnRefreshCameras)
    lyt_cam_btns.Add(btn_refresh, 100)
    btn_set_cam = FBButton(); btn_set_cam.Caption = "Set Camera to Viewport"
    btn_set_cam.OnClick.Add(OnSetCameraClick)
    lyt_cam_btns.Add(btn_set_cam, 170)
    layout.Add(lyt_cam_btns, 30)

    # ── Capture Region ───────────────────────────────────────────────
    layout.Add(hdr("Capture Region (screen px)"), 22)
    lyt_xy = FBHBoxLayout()
    lbl_x = FBLabel(); lbl_x.Caption = "X:"
    lyt_xy.Add(lbl_x, 20)
    g_ui["edit_x"] = FBEdit(); g_ui["edit_x"].Text = str(g_state["cap_x"])
    lyt_xy.Add(g_ui["edit_x"], 55)
    lbl_y = FBLabel(); lbl_y.Caption = "  Y:"
    lyt_xy.Add(lbl_y, 30)
    g_ui["edit_y"] = FBEdit(); g_ui["edit_y"].Text = str(g_state["cap_y"])
    lyt_xy.Add(g_ui["edit_y"], 55)
    layout.Add(lyt_xy, 30)

    lyt_wh = FBHBoxLayout()
    lbl_w = FBLabel(); lbl_w.Caption = "W:"
    lyt_wh.Add(lbl_w, 20)
    g_ui["edit_w"] = FBEdit(); g_ui["edit_w"].Text = str(g_state["cap_w"])
    lyt_wh.Add(g_ui["edit_w"], 55)
    lbl_h = FBLabel(); lbl_h.Caption = "  H:"
    lyt_wh.Add(lbl_h, 30)
    g_ui["edit_h"] = FBEdit(); g_ui["edit_h"].Text = str(g_state["cap_h"])
    lyt_wh.Add(g_ui["edit_h"], 55)
    layout.Add(lyt_wh, 30)

    btn_detect = FBButton(); btn_detect.Caption = "Auto-Detect Video Out Window"
    btn_detect.OnClick.Add(OnDetectVideoOut)
    layout.Add(btn_detect, 35)

    # ── Control ─────────────────────────────────────────────────────
    layout.Add(hdr("Stream Control"), 22)
    g_ui["btn_toggle"] = FBButton()
    g_ui["btn_toggle"].Caption = "Stop NDI Stream" if g_state["is_streaming"] else "Start NDI Stream"
    g_ui["btn_toggle"].OnClick.Add(OnToggleClick)
    layout.Add(g_ui["btn_toggle"], 40)

    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "STREAMING" if g_state["is_streaming"] else "OFFLINE"
    g_ui["lbl_status"].Justify = FBTextJustify.kFBTextJustifyCenter
    g_ui["lbl_status"].Style = FBTextStyle.kFBTextStyleBold
    layout.Add(g_ui["lbl_status"], 25)

    lbl_dll = FBLabel()
    lbl_dll.Caption = "DLL: " + (os.path.basename(g_ndi_loader.dll_path) if g_ndi_loader.dll_path else "NOT FOUND")
    lbl_dll.Justify = FBTextJustify.kFBTextJustifyCenter
    layout.Add(lbl_dll, 20)

def CreateTool():
    tool_name = "Mobu2NDI_Out"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)

CreateTool()
