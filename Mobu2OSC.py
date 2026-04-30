import os
import sys
import socket
import struct
import math
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# Clean up previous state
if hasattr(sys, "mobu2osc_state") and sys.mobu2osc_state is not None:
    try: FBSystem().OnUIIdle.Remove(sys.mobu2osc_idle_func)
    except: pass
    if sys.mobu2osc_state.sock:
        try: sys.mobu2osc_state.sock.close()
        except: pass
    sys.mobu2osc_state = None

class Mobu2OSCState:
    def __init__(self):
        self.sock = None
        self.is_sending = False
        self.target_ip = "127.0.0.1"
        self.target_port = 39540
        self.selected_models = {}  # Dictionary to hold models {Name: FBModel}
        self.fps_limit = 60
        self.last_send_time = 0.0
        self.frame_counter = 0

sys.mobu2osc_state = Mobu2OSCState()
g_sender = sys.mobu2osc_state
g_ui = {}

# ── OSC Encoding ──────────────────────────────────────────────────────────────
def encode_osc_str(s):
    b = s.encode('utf-8') + b'\x00'
    pad = (4 - len(b) % 4) % 4
    return b + b'\x00' * pad

def encode_osc_message_3f(address, f1, f2, f3):
    return (encode_osc_str(address) +
            encode_osc_str(",fff") +
            struct.pack('>3f', f1, f2, f3))

def encode_osc_message_1f(address, f1):
    return (encode_osc_str(address) +
            encode_osc_str(",f") +
            struct.pack('>f', f1))

# ── UDP Sending Loop ──────────────────────────────────────────────────────────
def OnUIIdle(control, event):
    try:
        if not g_sender.is_sending or not g_sender.sock:
            return
            
        current_time = time.time()
        if current_time - g_sender.last_send_time < (1.0 / g_sender.fps_limit):
            return
        g_sender.last_send_time = current_time

        messages = []
        debug_info = []
        
        # Iterate through all tracked models
        for name, model in list(g_sender.selected_models.items()):
            if not model:
                continue
                
            safe_name = name.replace("/", "_").replace(" ", "_")
            
            # 1. Get Translation, Rotation, Scaling
            pos = FBVector3d()
            rot = FBVector3d()
            scale = FBVector3d()
            model.GetVector(pos, FBModelTransformationType.kModelTranslation, False)
            model.GetVector(rot, FBModelTransformationType.kModelRotation, False)
            model.GetVector(scale, FBModelTransformationType.kModelScaling, False)
            
            messages.append(encode_osc_message_3f(f"/{safe_name}/Translation", pos[0], pos[1], pos[2]))
            debug_info.append(f"/{safe_name}/Translation: {pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}")
            
            messages.append(encode_osc_message_3f(f"/{safe_name}/Rotation", rot[0], rot[1], rot[2]))
            debug_info.append(f"/{safe_name}/Rotation: {rot[0]:.2f}, {rot[1]:.2f}, {rot[2]:.2f}")
            
            messages.append(encode_osc_message_3f(f"/{safe_name}/Scaling", scale[0], scale[1], scale[2]))
            debug_info.append(f"/{safe_name}/Scaling: {scale[0]:.2f}, {scale[1]:.2f}, {scale[2]:.2f}")
            
            # 2. Get Custom / Animated Properties
            for prop in model.PropertyList:
                try:
                    is_animated = prop.IsAnimatable() and hasattr(prop, 'IsAnimated') and prop.IsAnimated()
                    is_user = hasattr(prop, 'IsUserProperty') and prop.IsUserProperty()
                    
                    if is_animated or is_user:
                        if prop.PropertyType in (FBPropertyType.kFBPT_double, FBPropertyType.kFBPT_float, FBPropertyType.kFBPT_int):
                            prop_name = prop.Name.replace("/", "_").replace(" ", "_")
                            try:
                                val = float(prop.Data)
                                messages.append(encode_osc_message_1f(f"/{safe_name}/{prop_name}", val))
                                debug_info.append(f"/{safe_name}/{prop_name}: {val:.2f}")
                            except:
                                pass
                except:
                    pass
                            
        if not messages:
            g_sender.frame_counter += 1
            if g_sender.frame_counter % 30 == 0:
                if "lbl_status" in g_ui:
                    g_ui["lbl_status"].Caption = "Status: No models or data to send"
                if "memo_debug" in g_ui:
                    g_ui["memo_debug"].Text = "No models tracked. Please add models."
            return
        
        try:
            target = (g_sender.target_ip, g_sender.target_port)
            for msg in messages:
                g_sender.sock.sendto(msg, target)
        except Exception as e:
            pass # We don't want UDP errors to crash the UI thread entirely
            
        g_sender.frame_counter += 1
        if g_sender.frame_counter % 30 == 0:
            if "lbl_status" in g_ui:
                g_ui["lbl_status"].Caption = f"Status: Sending {len(messages)} msgs to {target[0]}:{target[1]}"
            if "memo_debug" in g_ui:
                g_ui["memo_debug"].Text = "\n".join(debug_info)
    except Exception as e:
        if "lbl_status" in g_ui:
            g_ui["lbl_status"].Caption = f"Crash in Idle Loop: {e}"


# ── UI Callbacks ──────────────────────────────────────────────────────────────
def UpdateModelListUI():
    g_ui["list_models"].Items.removeAll()
    for name in g_sender.selected_models.keys():
        g_ui["list_models"].Items.append(name)
        
def OnAddModelsClick(control, event):
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select at least one object in the scene!", "OK")
        return
        
    count = 0
    for m in models:
        if m.Name not in g_sender.selected_models:
            g_sender.selected_models[m.Name] = m
            count += 1
            
    UpdateModelListUI()
    if count > 0:
        FBMessageBox("Success", f"Added {count} objects to OSC stream.", "OK")

def OnRemoveModelClick(control, event):
    idx = g_ui["list_models"].ItemIndex
    if idx >= 0 and idx < len(g_ui["list_models"].Items):
        name = g_ui["list_models"].Items[idx]
        if name in g_sender.selected_models:
            del g_sender.selected_models[name]
        UpdateModelListUI()

def OnClearModelsClick(control, event):
    g_sender.selected_models.clear()
    UpdateModelListUI()

def OnStartStreamingClick(control, event):
    if not g_sender.is_sending:
        try:
            ip = g_ui["edit_ip"].Text
            port = int(g_ui["edit_port"].Value)
            g_sender.target_ip = ip
            g_sender.target_port = port
            
            g_sender.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_sender.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            g_sender.is_sending = True
            
            g_ui["btn_stream"].Caption = "Stop Streaming"
            g_ui["lbl_status"].Caption = f"Status: Streaming to {ip}:{port}"
            
            sys = FBSystem()
            try: sys.OnUIIdle.Remove(OnUIIdle)
            except: pass
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.mobu2osc_idle_func = OnUIIdle
            
        except Exception as e:
            FBMessageBox("Error", f"Could not start socket: {e}", "OK")
    else:
        if g_sender.sock:
            try: g_sender.sock.close()
            except: pass
            g_sender.sock = None
            
        g_sender.is_sending = False
        g_ui["btn_stream"].Caption = "Start Streaming"
        g_ui["lbl_status"].Caption = "Status: Stopped"
        
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass


# ── UI Creation ───────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 380
    tool.StartSizeY = 800
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    
    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])
    
    def create_header(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl
        
    # --- Network Connection ---
    g_ui["lyt_ip"] = FBHBoxLayout()
    g_ui["lbl_ip"] = FBLabel(); g_ui["lbl_ip"].Caption = "Target IP:"
    g_ui["edit_ip"] = FBEdit(); g_ui["edit_ip"].Text = "127.0.0.1"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 70)
    g_ui["lyt_ip"].Add(g_ui["edit_ip"], 100)
    
    g_ui["lyt_port"] = FBHBoxLayout()
    g_ui["lbl_port"] = FBLabel(); g_ui["lbl_port"].Caption = "Target Port:"
    g_ui["edit_port"] = FBEditNumber()
    g_ui["edit_port"].Value = 39540
    g_ui["edit_port"].Precision = 0
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 70)
    g_ui["lyt_port"].Add(g_ui["edit_port"], 100)
    
    # --- Source Objects ---
    g_ui["lyt_list"] = FBHBoxLayout()
    g_ui["list_models"] = FBList()
    g_ui["lyt_list"].Add(g_ui["list_models"], 200)
    
    g_ui["lyt_list_btns"] = FBVBoxLayout()
    g_ui["btn_add"] = FBButton(); g_ui["btn_add"].Caption = "Add Selected"
    g_ui["btn_add"].OnClick.Add(OnAddModelsClick)
    g_ui["btn_rem"] = FBButton(); g_ui["btn_rem"].Caption = "Remove"
    g_ui["btn_rem"].OnClick.Add(OnRemoveModelClick)
    g_ui["btn_clr"] = FBButton(); g_ui["btn_clr"].Caption = "Clear All"
    g_ui["btn_clr"].OnClick.Add(OnClearModelsClick)
    
    g_ui["lyt_list_btns"].Add(g_ui["btn_add"], 30)
    g_ui["lyt_list_btns"].Add(g_ui["btn_rem"], 30)
    g_ui["lyt_list_btns"].Add(g_ui["btn_clr"], 30)
    g_ui["lyt_list"].Add(g_ui["lyt_list_btns"], 100)
    
    # --- Stream Button ---
    g_ui["btn_stream"] = FBButton()
    g_ui["btn_stream"].Caption = "Start Streaming"
    g_ui["btn_stream"].OnClick.Add(OnStartStreamingClick)
    
    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "Status: Stopped"
    
    # --- Debug Output ---
    g_ui["lyt_debug"] = FBHBoxLayout()
    g_ui["memo_debug"] = FBMemo()
    g_ui["lyt_debug"].Add(g_ui["memo_debug"], 330)
    
    # --- Layout Assembly ---
    g_ui["main_layout"].Add(create_header("NETWORK"), 25)
    g_ui["main_layout"].Add(g_ui["lyt_ip"], 30)
    g_ui["main_layout"].Add(g_ui["lyt_port"], 30)
    
    g_ui["main_layout"].Add(create_header("STREAMING SOURCE"), 25)
    g_ui["main_layout"].Add(g_ui["lyt_list"], 120)
    
    g_ui["main_layout"].Add(create_header("CONTROL"), 25)
    g_ui["main_layout"].Add(g_ui["btn_stream"], 40)
    g_ui["main_layout"].Add(g_ui["lbl_status"], 30)
    
    g_ui["main_layout"].Add(create_header("DEBUG OUTPUT"), 25)
    g_ui["main_layout"].Add(g_ui["lyt_debug"], 180)

def CreateTool():
    tool_name = "Saint's Mobu2OSC Sender"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
