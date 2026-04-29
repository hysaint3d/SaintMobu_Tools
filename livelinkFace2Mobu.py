import os
import sys
import socket
import struct
import math
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# Clean up previous state if it exists from older script versions
if hasattr(sys, "livelink_state") and sys.livelink_state is not None:
    if sys.livelink_state.sock:
        try: sys.livelink_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.livelink_state_idle_func)
    except: pass
    sys.livelink_state = None

class LiveLinkState:
    def __init__(self):
        self.sock = None
        self.is_connected = False
        self.livelink_data_cache = {}
        self.models = {}
        self.prop_cache = {}
        self.last_ui_update = 0.0
        self.last_applied_cache = {}

sys.livelink_state = LiveLinkState()
g_livelink = sys.livelink_state
g_ui = {} 

arkit_blendshapes = [
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft", "eyeLookUpLeft",
    "eyeSquintLeft", "eyeWideLeft", "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight",
    "eyeLookOutRight", "eyeLookUpRight", "eyeSquintRight", "eyeWideRight", "jawForward",
    "jawLeft", "jawRight", "jawOpen", "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft",
    "mouthRight", "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft", "mouthStretchRight",
    "mouthRollLower", "mouthRollUpper", "mouthShrugLower", "mouthShrugUpper", "mouthPressLeft",
    "mouthPressRight", "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpLeft",
    "mouthUpperUpRight", "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight", "cheekPuff", "cheekSquintLeft", "cheekSquintRight", "noseSneerLeft",
    "noseSneerRight", "tongueOut",
    "HeadYaw", "HeadPitch", "HeadRoll",
    "LeftEyeYaw", "LeftEyePitch", "LeftEyeRoll",
    "RightEyeYaw", "RightEyePitch", "RightEyeRoll"
]

def parse_livelink(data):
    try:
        if len(data) < 62: return False
        version = struct.unpack('<i', data[0:4])[0]
        if version != 6: return False
        
        name_length = struct.unpack('!i', data[41:45])[0]
        name_end_pos = 45 + name_length
        if len(data) < name_end_pos + 17 + (61 * 4): return False
        
        frame_number, sub_frame, fps, denominator, data_length = struct.unpack(
            "!if2ib", data[name_end_pos:name_end_pos + 17])
            
        if data_length != 61: return False
        
        blend_data = struct.unpack("!61f", data[name_end_pos + 17 : name_end_pos + 17 + (61 * 4)])
        
        for idx, val in enumerate(blend_data):
            if idx < len(arkit_blendshapes):
                bs_name = arkit_blendshapes[idx]
                g_livelink.livelink_data_cache[bs_name] = val * 100.0
        return True
    except:
        return False

def OnUIIdle(control, event):
    if not g_livelink.is_connected or not g_livelink.sock:
        return
        
    packets_processed = 0
    last_packet_size = 0
    while packets_processed < 2000:
        try:
            data, addr = g_livelink.sock.recvfrom(65536)
            last_packet_size = len(data)
            
            # Check if it's a LiveLink Face binary packet (starts with int 6)
            if len(data) > 0 and data[0] == 6:
                parse_livelink(data)
                
            packets_processed += 1
            
        except BlockingIOError:
            break
        except socket.error as e:
            if e.errno == 10035: break
            break
        except Exception as e:
            break
            
    if last_packet_size > 0:
        current_time = time.time()
        if current_time - g_livelink.last_ui_update > 0.1:
            try:
                g_ui["lbl_status"].Caption = "Receiving Data (Channels: {})".format(len(g_livelink.livelink_data_cache))
                g_livelink.last_ui_update = current_time
            except Exception:
                try:
                    FBSystem().OnUIIdle.Remove(OnUIIdle)
                except: pass
            
    # Real-time update for properties
    if "LiveLink_Data" in g_livelink.models:
        ll_node = g_livelink.models["LiveLink_Data"]
        try:
            for prop_name, val in g_livelink.livelink_data_cache.items():
                prop = g_livelink.prop_cache.get(prop_name)
                if not prop:
                    prop = ll_node.PropertyList.Find(prop_name)
                    if prop:
                        g_livelink.prop_cache[prop_name] = prop
                if prop:
                    # Only update if the value changed significantly to prevent evaluation lag
                    last_val = g_livelink.last_applied_cache.get(prop_name)
                    if last_val is None or abs(last_val - val) > 0.001:
                        prop.Data = float(val)
                        g_livelink.last_applied_cache[prop_name] = val
        except:
            pass

def OnConnectClick(control, event):
    if not g_livelink.is_connected:
        try:
            ip = g_ui["edit_ip"].Text
            port = int(g_ui["edit_port"].Value)
            g_livelink.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_livelink.sock.bind((ip, port))
            g_livelink.sock.setblocking(False)
            g_livelink.is_connected = True
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_status"].Caption = "Status: Connected ({})".format(port)
            print("Live Link Receiver started on port", port)
            
            sys = FBSystem()
            sys.OnUIIdle.Remove(OnUIIdle)
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.livelink_state_idle_func = OnUIIdle
            
        except Exception as e:
            g_ui["lbl_status"].Caption = "Status: Error binding port!"
            print("Failed to bind socket:", e)
    else:
        if g_livelink.sock:
            g_livelink.sock.close()
            g_livelink.sock = None
        g_livelink.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Status: Disconnected"
        sys = FBSystem()
        sys.OnUIIdle.Remove(OnUIIdle)
        print("Live Link Receiver stopped.")

def OnCreateDataChannelsClick(control, event):
    if not g_livelink.livelink_data_cache:
        FBMessageBox("Warning", "No Live Link data received yet!\nPlease send data and wait a second.", "OK")
        return
        
    ll_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "LiveLink_Data":
            ll_node = m
            break
            
    if not ll_node:
        ll_node = FBModelNull("LiveLink_Data")
        ll_node.Show = True
        ll_node.Size = 50.0
        g_livelink.models["LiveLink_Data"] = ll_node
    else:
        g_livelink.models["LiveLink_Data"] = ll_node
        
    count = 0
    for prop_name in g_livelink.livelink_data_cache.keys():
        prop = ll_node.PropertyList.Find(prop_name)
        if not prop:
            prop = ll_node.PropertyCreate(prop_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    FBMessageBox("Success", "Created/Updated {} data channels on LiveLink_Data!".format(count), "OK")

def FindAnimationNode(parent_node, name):
    if not parent_node: return None
    for node in parent_node.Nodes:
        if node.Name == name:
            return node
        found = FindAnimationNode(node, name)
        if found: return found
    return None

def OnConnectToModelClick(control, event):
    ll_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "LiveLink_Data":
            ll_node = m
            break
            
    if not ll_node:
        FBMessageBox("Warning", "LiveLink_Data node not found! Please run 'Create Data Channels' first.", "OK")
        return
        
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select a model with blendshapes first!", "OK")
        return
        
    target_model = models[0]
    
    # Pre-pass: Expose matching blendshape properties on the target model by setting them to animated
    for prop in ll_node.PropertyList:
        if prop.IsUserProperty():
            target_prop = target_model.PropertyList.Find(prop.Name)
            if target_prop:
                try: target_prop.SetAnimated(True)
                except: pass
    
    relation = FBConstraintRelation("LiveLink_Expression_Link")
    relation.Active = False
    
    src_box = relation.SetAsSource(ll_node)
    trgt_box = relation.ConstrainObject(target_model)
    
    relation.SetBoxPosition(src_box, 100, 100)
    relation.SetBoxPosition(trgt_box, 400, 100)
    
    match_count = 0
    src_out_node = src_box.AnimationNodeOutGet()
    trgt_in_node = trgt_box.AnimationNodeInGet()
    
    if src_out_node and trgt_in_node:
        for prop in ll_node.PropertyList:
            if prop.IsUserProperty():
                prop_name = prop.Name
                out_n = FindAnimationNode(src_out_node, prop_name)
                in_n = FindAnimationNode(trgt_in_node, prop_name)
                
                if out_n and in_n:
                    FBConnect(out_n, in_n)
                    match_count += 1
                    
    relation.Active = True
    
    if match_count == 0:
        FBMessageBox("Message", "No matching channels found! Please connect manually.", "OK")
    else:
        FBMessageBox("Success", "Successfully connected {} channels!".format(match_count), "OK")

def OnDeleteDataClick(control, event):
    for m in list(FBSystem().Scene.RootModel.Children):
        try:
            if m.Name == "LiveLink_Data":
                m.FBDelete()
        except Exception:
            pass
                
    g_livelink.models.clear()
    g_livelink.livelink_data_cache.clear()
    
    if g_livelink.sock:
        try: g_livelink.sock.close()
        except: pass
        g_livelink.sock = None
    g_livelink.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Status: Disconnected / Reset"
    try: FBSystem().OnUIIdle.Remove(OnUIIdle)
    except: pass
    
    FBMessageBox("Success", "Cleaned up LiveLink_Data and reset Network Port.", "OK")

def PopulateTool(tool):
    tool.StartSizeX = 350
    tool.StartSizeY = 400
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    
    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])
    
    g_ui["lyt_ip"] = FBHBoxLayout()
    g_ui["lbl_ip"] = FBLabel()
    g_ui["lbl_ip"].Caption = "Bind IP:"
    g_ui["edit_ip"] = FBEdit()
    g_ui["edit_ip"].Text = "0.0.0.0"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 70)
    g_ui["lyt_ip"].Add(g_ui["edit_ip"], 100)
    
    g_ui["lyt_port"] = FBHBoxLayout()
    g_ui["lbl_port"] = FBLabel()
    g_ui["lbl_port"].Caption = "UDP Port:"
    g_ui["edit_port"] = FBEditNumber()
    g_ui["edit_port"].Value = 11111 # Live Link Face default port
    g_ui["edit_port"].Precision = 0
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 70)
    g_ui["lyt_port"].Add(g_ui["edit_port"], 100)
    
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    
    g_ui["btn_create_data"] = FBButton()
    g_ui["btn_create_data"].Caption = "Create Data Channels on LiveLink_Data"
    g_ui["btn_create_data"].OnClick.Add(OnCreateDataChannelsClick)
    
    g_ui["btn_connect_model"] = FBButton()
    g_ui["btn_connect_model"].Caption = "Connect Channels to Selected Model"
    g_ui["btn_connect_model"].OnClick.Add(OnConnectToModelClick)
    
    g_ui["btn_delete"] = FBButton()
    g_ui["btn_delete"].Caption = "Delete LiveLink_Data & Reset"
    g_ui["btn_delete"].OnClick.Add(OnDeleteDataClick)
    
    def create_header(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl
        
    g_ui["hdr_connect"] = create_header("CONNECT")
    g_ui["hdr_data"] = create_header("LIVELINK DATA")
    g_ui["hdr_reset"] = create_header("RESET")
    
    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "Status: Disconnected"
    
    g_ui["main_layout"].Add(g_ui["hdr_connect"], 25)
    g_ui["main_layout"].Add(g_ui["lyt_ip"], 30)
    g_ui["main_layout"].Add(g_ui["lyt_port"], 30)
    g_ui["main_layout"].Add(g_ui["btn_connect"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_data"], 25)
    g_ui["main_layout"].Add(g_ui["btn_create_data"], 35)
    g_ui["main_layout"].Add(g_ui["btn_connect_model"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_reset"], 25)
    g_ui["main_layout"].Add(g_ui["btn_delete"], 35)
    
    g_ui["main_layout"].Add(g_ui["lbl_status"], 35)

def CreateTool():
    tool_name = "Saint's Live Link Receiver"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
