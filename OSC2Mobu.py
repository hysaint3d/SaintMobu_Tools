import os
import sys
import socket
import struct
import math
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# Clean up previous VMC state if it exists from older script versions
if hasattr(sys, "vmc_state") and sys.vmc_state is not None:
    if sys.vmc_state.sock:
        try: sys.vmc_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.vmc_state_idle_func)
    except: pass
    sys.vmc_state = None

class OSCState:
    def __init__(self):
        self.sock = None
        self.is_connected = False
        self.osc_data_cache = {}
        self.models = {}
        self.prop_cache = {}
        self.last_ui_update = 0.0
        self.last_applied_cache = {}

if hasattr(sys, "osc_state") and sys.osc_state is not None:
    if sys.osc_state.sock:
        try: sys.osc_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.osc_state_idle_func)
    except: pass
    
sys.osc_state = OSCState()
g_osc = sys.osc_state
g_ui = {} 

def parse_osc(data):
    try:
        addr_end = data.find(b'\0')
        if addr_end == -1: return None, None
        address = data[:addr_end].decode('utf-8')
        
        type_start = (addr_end + 4) & ~0x03
        if type_start >= len(data) or data[type_start] != ord(','): return address, []
        
        type_end = data.find(b'\0', type_start)
        if type_end == -1: return address, []
        type_tags = data[type_start+1:type_end].decode('utf-8')
        
        arg_start = (type_end + 4) & ~0x03
        args = []
        offset = arg_start
        for tag in type_tags:
            if offset >= len(data): break
            if tag == 'f':
                val = struct.unpack('>f', data[offset:offset+4])[0]
                args.append(val)
                offset += 4
            elif tag == 'i':
                val = struct.unpack('>i', data[offset:offset+4])[0]
                args.append(val)
                offset += 4
            elif tag == 's':
                s_end = data.find(b'\0', offset)
                if s_end == -1: break
                val = data[offset:s_end].decode('utf-8')
                args.append(val)
                offset = (s_end + 4) & ~0x03
        return address, args
    except:
        return None, None


def process_osc_message(address, args):
    if not address or not args:
        return
        
    safe_addr = address.strip("/").replace("/", "_")
    


    # 2. Key-Value mapping (like VMC /VMC/Ext/Blend/Val [name, value])
    if len(args) >= 2 and isinstance(args[0], str):
        key_name = args[0]
        if len(args) == 2 and isinstance(args[1], (int, float)):
            val = float(args[1])
            if "Blend" in address or "Expr" in address or "VMC" in address:
                val *= 100.0 # VMC blendshapes are also 0~1
            g_osc.osc_data_cache[key_name] = val
        else:
            # Multiple float arguments for a single string key (e.g. Bone pos)
            for i in range(1, len(args)):
                if isinstance(args[i], (int, float)):
                    g_osc.osc_data_cache[f"{key_name}_{safe_addr}_{i}"] = float(args[i])
        return

    # 3. Generic array of numbers (e.g. Facecap /HT [x, y, z])
    if len(args) == 1:
        if isinstance(args[0], (int, float)):
            g_osc.osc_data_cache[safe_addr] = float(args[0])
    else:
        for i, val in enumerate(args):
            if isinstance(val, (int, float)):
                g_osc.osc_data_cache[f"{safe_addr}_{i}"] = float(val)

def OnUIIdle(control, event):
    if not g_osc.is_connected or not g_osc.sock:
        return
        
    packets_processed = 0
    last_packet_size = 0
    while packets_processed < 2000:
        try:
            data, addr = g_osc.sock.recvfrom(65536)
            last_packet_size = len(data)
            
            address, args = parse_osc(data)
            
            if data.startswith(b'#bundle'):
                offset = 16
                while offset < len(data):
                    size = struct.unpack('>i', data[offset:offset+4])[0]
                    offset += 4
                    msg_data = data[offset:offset+size]
                    msg_address, msg_args = parse_osc(msg_data)
                    process_osc_message(msg_address, msg_args)
                    offset += size
            else:
                process_osc_message(address, args)
                
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
        if current_time - g_osc.last_ui_update > 0.1:
            try:
                g_ui["lbl_status"].Caption = "Receiving Data (Channels: {})".format(len(g_osc.osc_data_cache))
                g_osc.last_ui_update = current_time
            except Exception:
                try:
                    FBSystem().OnUIIdle.Remove(OnUIIdle)
                except: pass
            
    # Real-time update for properties
    if "OSC_Data" in g_osc.models:
        osc_node = g_osc.models["OSC_Data"]
        try:
            for prop_name, val in g_osc.osc_data_cache.items():
                prop = g_osc.prop_cache.get(prop_name)
                if not prop:
                    prop = osc_node.PropertyList.Find(prop_name)
                    if prop:
                        g_osc.prop_cache[prop_name] = prop
                if prop:
                    # Only update if the value changed significantly to prevent evaluation lag
                    last_val = g_osc.last_applied_cache.get(prop_name)
                    if last_val is None or abs(last_val - val) > 0.001:
                        prop.Data = float(val)
                        g_osc.last_applied_cache[prop_name] = val
        except:
            pass

def OnConnectClick(control, event):
    if not g_osc.is_connected:
        try:
            ip = g_ui["edit_ip"].Text
            port = int(g_ui["edit_port"].Value)
            g_osc.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_osc.sock.bind((ip, port))
            g_osc.sock.setblocking(False)
            g_osc.is_connected = True
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_status"].Caption = "Status: Connected ({})".format(port)
            print("OSC Receiver started on port", port)
            
            sys = FBSystem()
            sys.OnUIIdle.Remove(OnUIIdle)
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.osc_state_idle_func = OnUIIdle
            
        except Exception as e:
            g_ui["lbl_status"].Caption = "Status: Error binding port!"
            print("Failed to bind socket:", e)
    else:
        if g_osc.sock:
            g_osc.sock.close()
            g_osc.sock = None
        g_osc.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Status: Disconnected"
        sys = FBSystem()
        sys.OnUIIdle.Remove(OnUIIdle)
        print("OSC Receiver stopped.")

def OnCreateDataChannelsClick(control, event):
    if not g_osc.osc_data_cache:
        FBMessageBox("Warning", "No OSC data received yet!\nPlease send OSC data and wait a second.", "OK")
        return
        
    osc_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "OSC_Data":
            osc_node = m
            break
            
    if not osc_node:
        osc_node = FBModelNull("OSC_Data")
        osc_node.Show = True
        osc_node.Size = 50.0
        g_osc.models["OSC_Data"] = osc_node
    else:
        g_osc.models["OSC_Data"] = osc_node
        
    count = 0
    for prop_name in g_osc.osc_data_cache.keys():
        prop = osc_node.PropertyList.Find(prop_name)
        if not prop:
            prop = osc_node.PropertyCreate(prop_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    FBMessageBox("Success", "Created/Updated {} data channels on OSC_Data!".format(count), "OK")

def FindAnimationNode(parent_node, name):
    if not parent_node: return None
    for node in parent_node.Nodes:
        if node.Name == name:
            return node
        found = FindAnimationNode(node, name)
        if found: return found
    return None

def OnConnectToModelClick(control, event):
    osc_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "OSC_Data":
            osc_node = m
            break
            
    if not osc_node:
        FBMessageBox("Warning", "OSC_Data node not found! Please run 'Create Data Channels' first.", "OK")
        return
        
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select a model with blendshapes first!", "OK")
        return
        
    target_model = models[0]
    
    # Pre-pass: Expose matching blendshape properties on the target model by setting them to animated
    for prop in osc_node.PropertyList:
        if prop.IsUserProperty():
            target_prop = target_model.PropertyList.Find(prop.Name)
            if target_prop:
                try: target_prop.SetAnimated(True)
                except: pass
    
    relation = FBConstraintRelation("OSC_Expression_Link")
    relation.Active = False
    
    src_box = relation.SetAsSource(osc_node)
    trgt_box = relation.ConstrainObject(target_model)
    
    relation.SetBoxPosition(src_box, 100, 100)
    relation.SetBoxPosition(trgt_box, 400, 100)
    
    match_count = 0
    src_out_node = src_box.AnimationNodeOutGet()
    trgt_in_node = trgt_box.AnimationNodeInGet()
    
    if src_out_node and trgt_in_node:
        for prop in osc_node.PropertyList:
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
            if m.Name == "OSC_Data":
                m.FBDelete()
        except Exception:
            pass
                
    g_osc.models.clear()
    g_osc.osc_data_cache.clear()
    
    if g_osc.sock:
        try: g_osc.sock.close()
        except: pass
        g_osc.sock = None
    g_osc.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Status: Disconnected / Reset"
    try: FBSystem().OnUIIdle.Remove(OnUIIdle)
    except: pass
    
    FBMessageBox("Success", "Cleaned up OSC_Data and reset Network Port.", "OK")

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
    g_ui["edit_port"].Value = 39539
    g_ui["edit_port"].Precision = 0
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 70)
    g_ui["lyt_port"].Add(g_ui["edit_port"], 100)
    
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    
    g_ui["btn_create_data"] = FBButton()
    g_ui["btn_create_data"].Caption = "Create Data Channels on OSC_Data"
    g_ui["btn_create_data"].OnClick.Add(OnCreateDataChannelsClick)
    
    g_ui["btn_connect_model"] = FBButton()
    g_ui["btn_connect_model"].Caption = "Connect Channels to Selected Model"
    g_ui["btn_connect_model"].OnClick.Add(OnConnectToModelClick)
    
    g_ui["btn_delete"] = FBButton()
    g_ui["btn_delete"].Caption = "Delete OSC_Data & Reset"
    g_ui["btn_delete"].OnClick.Add(OnDeleteDataClick)
    
    def create_header(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl
        
    g_ui["hdr_connect"] = create_header("CONNECT")
    g_ui["hdr_data"] = create_header("OSC DATA")
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
    tool_name = "Saint's OSC Receiver"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
