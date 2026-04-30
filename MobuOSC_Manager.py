import os
import sys
import socket
import struct
import math
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# Clean up previous states from old plugins or this plugin
for state_name in ["mobu2osc_state", "osc_state", "osc_mgr_state"]:
    if hasattr(sys, state_name) and getattr(sys, state_name) is not None:
        state = getattr(sys, state_name)
        if hasattr(state, "sock") and state.sock:
            try: state.sock.close()
            except: pass
        if hasattr(state, "send_sock") and state.send_sock:
            try: state.send_sock.close()
            except: pass
        if hasattr(state, "recv_sock") and state.recv_sock:
            try: state.recv_sock.close()
            except: pass
        try: FBSystem().OnUIIdle.Remove(sys.mobu2osc_idle_func)
        except: pass
        try: FBSystem().OnUIIdle.Remove(sys.osc_state_idle_func)
        except: pass
        try: FBSystem().OnUIIdle.Remove(sys.osc_mgr_idle_func)
        except: pass
        setattr(sys, state_name, None)

class OSCManager_State:
    def __init__(self):
        # Sender state
        self.send_sock = None
        self.is_sending = False
        self.target_ip = "127.0.0.1"
        self.target_port = 10001
        self.selected_models = {}  # Dictionary to hold models {Name: FBModel}
        self.fps_limit = 60
        self.last_send_time = 0.0
        self.frame_counter = 0
        
        # Receiver state
        self.recv_sock = None
        self.is_connected = False
        self.osc_data_cache = {}
        self.recv_models = {}
        self.prop_cache = {}
        self.last_ui_update = 0.0
        self.last_applied_cache = {}

sys.osc_mgr_state = OSCManager_State()
g_state = sys.osc_mgr_state
g_ui = {}

# ── OSC Encoding (Sender) ────────────────────────────────────────────────────────
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

# ── OSC Parsing (Receiver) ───────────────────────────────────────────────────────
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

    if len(args) >= 2 and isinstance(args[0], str):
        key_name = args[0]
        if len(args) == 2 and isinstance(args[1], (int, float)):
            val = float(args[1])
            if "Blend" in address or "Expr" in address or "VMC" in address:
                val *= 100.0 # VMC blendshapes scale to 0-100 in Mobu
            g_state.osc_data_cache[key_name] = val
        else:
            for i in range(1, len(args)):
                if isinstance(args[i], (int, float)):
                    g_state.osc_data_cache[f"{key_name}_{safe_addr}_{i}"] = float(args[i])
        return

    if len(args) == 1:
        if isinstance(args[0], (int, float)):
            g_state.osc_data_cache[safe_addr] = float(args[0])
    else:
        for i, val in enumerate(args):
            if isinstance(val, (int, float)):
                g_state.osc_data_cache[f"{safe_addr}_{i}"] = float(val)

# ── Main Idle Loop ─────────────────────────────────────────────────────────────
def OnUIIdle(control, event):
    # --- Receiver Logic ---
    if g_state.is_connected and g_state.recv_sock:
        packets_processed = 0
        last_packet_size = 0
        while packets_processed < 2000:
            try:
                data, addr = g_state.recv_sock.recvfrom(65536)
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
            except BlockingIOError: break
            except socket.error as e:
                if e.errno == 10035: break
                break
            except Exception as e: break
                
        if last_packet_size > 0:
            current_time = time.time()
            if current_time - g_state.last_ui_update > 0.1:
                try:
                    if "lbl_recv_status" in g_ui:
                        g_ui["lbl_recv_status"].Caption = "Receiving Data (Channels: {})".format(len(g_state.osc_data_cache))
                    g_state.last_ui_update = current_time
                except Exception: pass
                
        if "OSC_Data" in g_state.recv_models:
            osc_node = g_state.recv_models["OSC_Data"]
            try:
                for prop_name, val in g_state.osc_data_cache.items():
                    prop = g_state.prop_cache.get(prop_name)
                    if not prop:
                        prop = osc_node.PropertyList.Find(prop_name)
                        if prop:
                            g_state.prop_cache[prop_name] = prop
                    if prop:
                        last_val = g_state.last_applied_cache.get(prop_name)
                        if last_val is None or abs(last_val - val) > 0.001:
                            prop.Data = float(val)
                            g_state.last_applied_cache[prop_name] = val
            except: pass

    # --- Sender Logic ---
    if g_state.is_sending and g_state.send_sock:
        current_time = time.time()
        if current_time - g_state.last_send_time >= (1.0 / g_state.fps_limit):
            g_state.last_send_time = current_time

            messages = []
            debug_info = []
            
            for name, model in list(g_state.selected_models.items()):
                if not model: continue
                safe_name = name.replace("/", "_").replace(" ", "_")
                
                pos = FBVector3d()
                rot = FBVector3d()
                scale = FBVector3d()
                model.GetVector(pos, FBModelTransformationType.kModelTranslation, False)
                model.GetVector(rot, FBModelTransformationType.kModelRotation, False)
                model.GetVector(scale, FBModelTransformationType.kModelScaling, False)
                
                messages.append(encode_osc_message_3f(f"/{safe_name}/Translation", pos[0], pos[1], pos[2]))
                messages.append(encode_osc_message_3f(f"/{safe_name}/Rotation", rot[0], rot[1], rot[2]))
                messages.append(encode_osc_message_3f(f"/{safe_name}/Scaling", scale[0], scale[1], scale[2]))
                
                if g_state.frame_counter % 30 == 0:
                    debug_info.append(f"/{safe_name}/Translation: {pos[0]:.2f}, {pos[1]:.2f}, {pos[2]:.2f}")
                    debug_info.append(f"/{safe_name}/Rotation: {rot[0]:.2f}, {rot[1]:.2f}, {rot[2]:.2f}")
                
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
                                    if g_state.frame_counter % 30 == 0:
                                        debug_info.append(f"/{safe_name}/{prop_name}: {val:.2f}")
                                except: pass
                    except: pass
                                
            if not messages:
                g_state.frame_counter += 1
                if g_state.frame_counter % 30 == 0:
                    if "lbl_send_status" in g_ui:
                        g_ui["lbl_send_status"].Caption = "Status: No models or data to send"
            else:
                try:
                    target = (g_state.target_ip, g_state.target_port)
                    for msg in messages:
                        g_state.send_sock.sendto(msg, target)
                except Exception as e:
                    pass
                    
                g_state.frame_counter += 1
                if g_state.frame_counter % 30 == 0:
                    if "lbl_send_status" in g_ui:
                        g_ui["lbl_send_status"].Caption = f"Status: Sending {len(messages)} msgs to {target[0]}:{target[1]}"

# ── Sender Callbacks ───────────────────────────────────────────────────────────
def UpdateModelListUI():
    if "list_models" in g_ui:
        g_ui["list_models"].Items.removeAll()
        for name in g_state.selected_models.keys():
            g_ui["list_models"].Items.append(name)
        
def OnAddModelsClick(control, event):
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select at least one object in the scene!", "OK")
        return
        
    count = 0
    for m in models:
        if m.Name not in g_state.selected_models:
            g_state.selected_models[m.Name] = m
            count += 1
            
    UpdateModelListUI()
    if count > 0: FBMessageBox("Success", f"Added {count} objects to OSC stream.", "OK")

def OnRemoveModelClick(control, event):
    idx = g_ui["list_models"].ItemIndex
    if idx >= 0 and idx < len(g_ui["list_models"].Items):
        name = g_ui["list_models"].Items[idx]
        if name in g_state.selected_models:
            del g_state.selected_models[name]
        UpdateModelListUI()

def OnStartStreamingClick(control, event):
    if not g_state.is_sending:
        try:
            ip = g_ui["edit_send_ip"].Text.strip()
            port = int(g_ui["edit_send_port"].Text.strip())
            g_state.target_ip = ip
            g_state.target_port = port
            
            g_state.send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_state.send_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            g_state.is_sending = True
            
            g_ui["btn_stream"].Caption = "Stop Streaming"
            g_ui["lbl_send_status"].Caption = f"Status: Streaming to {ip}:{port}"
            
            sys = FBSystem()
            try: sys.OnUIIdle.Remove(OnUIIdle)
            except: pass
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.osc_mgr_idle_func = OnUIIdle
        except Exception as e:
            FBMessageBox("Error", f"Could not start socket: {e}", "OK")
    else:
        if g_state.send_sock:
            try: g_state.send_sock.close()
            except: pass
            g_state.send_sock = None
            
        g_state.is_sending = False
        g_ui["btn_stream"].Caption = "Start Streaming"
        g_ui["lbl_send_status"].Caption = "Status: Stopped"
        
        if not g_state.is_connected:
            try: FBSystem().OnUIIdle.Remove(OnUIIdle)
            except: pass

# ── Receiver Callbacks ─────────────────────────────────────────────────────────
def OnConnectClick(control, event):
    if not g_state.is_connected:
        try:
            ip = g_ui["edit_recv_ip"].Text.strip()
            port = int(g_ui["edit_recv_port"].Text.strip())
            g_state.recv_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_state.recv_sock.bind((ip, port))
            g_state.recv_sock.setblocking(False)
            g_state.is_connected = True
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_recv_status"].Caption = f"Status: Listening on {port}"
            
            sys = FBSystem()
            try: sys.OnUIIdle.Remove(OnUIIdle)
            except: pass
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.osc_mgr_idle_func = OnUIIdle
        except Exception as e:
            g_ui["lbl_recv_status"].Caption = "Status: Error binding port!"
            FBMessageBox("Error", f"Could not bind port {port}: {e}", "OK")
    else:
        if g_state.recv_sock:
            try: g_state.recv_sock.close()
            except: pass
            g_state.recv_sock = None
        g_state.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_recv_status"].Caption = "Status: Disconnected"
        
        if not g_state.is_sending:
            try: FBSystem().OnUIIdle.Remove(OnUIIdle)
            except: pass

def OnCreateDataChannelsClick(control, event):
    if not g_state.osc_data_cache:
        FBMessageBox("Warning", "No OSC data received yet!\nPlease wait for OSC data.", "OK")
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
        g_state.recv_models["OSC_Data"] = osc_node
    else:
        g_state.recv_models["OSC_Data"] = osc_node
        
    count = 0
    for prop_name in g_state.osc_data_cache.keys():
        prop = osc_node.PropertyList.Find(prop_name)
        if not prop:
            prop = osc_node.PropertyCreate(prop_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    FBMessageBox("Success", f"Created/Updated {count} data channels on OSC_Data!", "OK")

def FindAnimationNode(parent_node, name):
    if not parent_node: return None
    for node in parent_node.Nodes:
        if node.Name == name: return node
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
        FBMessageBox("Warning", "OSC_Data node not found! Please Create Data Channels first.", "OK")
        return
        
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select a target model first!", "OK")
        return
        
    target_model = models[0]
    
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
    FBMessageBox("Result", f"Successfully connected {match_count} channels!", "OK")

def OnDeleteDataClick(control, event):
    for m in list(FBSystem().Scene.RootModel.Children):
        try:
            if m.Name == "OSC_Data":
                m.FBDelete()
        except: pass
                
    g_state.recv_models.clear()
    g_state.osc_data_cache.clear()
    
    if g_state.recv_sock:
        try: g_state.recv_sock.close()
        except: pass
        g_state.recv_sock = None
    g_state.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_recv_status"].Caption = "Status: Disconnected / Reset"
    
    if not g_state.is_sending:
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
    
    FBMessageBox("Success", "Cleaned up OSC_Data and Reset Receiver.", "OK")

# ── UI Creation ───────────────────────────────────────────────────────────────
def create_header(text):
    lbl = FBLabel()
    lbl.Caption = "--- " + text + " ---"
    lbl.Justify = FBTextJustify.kFBTextJustifyCenter
    return lbl

def BuildSenderView(view):
    view.Add(create_header("STREAMING SOURCE"), 25)
    
    g_ui["list_models"] = FBList()
    view.Add(g_ui["list_models"], 60)
    
    lyt_list_btns = FBHBoxLayout()
    btn_add = FBButton(); btn_add.Caption = "Add Selected"; btn_add.OnClick.Add(OnAddModelsClick)
    btn_rem = FBButton(); btn_rem.Caption = "Remove"; btn_rem.OnClick.Add(OnRemoveModelClick)
    lyt_list_btns.Add(btn_add, 100)
    lyt_list_btns.Add(btn_rem, 100)
    view.Add(lyt_list_btns, 30)
    
    view.Add(create_header("NETWORK (Sender)"), 25)
    
    lyt_ip = FBHBoxLayout()
    lbl_ip = FBLabel(); lbl_ip.Caption = "Target IP:"
    g_ui["edit_send_ip"] = FBEdit(); g_ui["edit_send_ip"].Text = "127.0.0.1"
    lyt_ip.Add(lbl_ip, 70)
    lyt_ip.Add(g_ui["edit_send_ip"], 100)
    view.Add(lyt_ip, 30)
    
    lyt_port = FBHBoxLayout()
    lbl_port = FBLabel(); lbl_port.Caption = "Target Port:"
    g_ui["edit_send_port"] = FBEdit()
    g_ui["edit_send_port"].Text = "10001"
    lyt_port.Add(lbl_port, 70)
    lyt_port.Add(g_ui["edit_send_port"], 100)
    view.Add(lyt_port, 30)
    
    g_ui["btn_stream"] = FBButton()
    g_ui["btn_stream"].Caption = "Start Streaming"
    g_ui["btn_stream"].OnClick.Add(OnStartStreamingClick)
    view.Add(g_ui["btn_stream"], 40)
    
    g_ui["lbl_send_status"] = FBLabel()
    g_ui["lbl_send_status"].Caption = "Status: Stopped"
    view.Add(g_ui["lbl_send_status"], 30)

def BuildReceiverView(view):
    view.Add(create_header("NETWORK (Receiver)"), 25)
    
    lyt_ip = FBHBoxLayout()
    lbl_ip = FBLabel(); lbl_ip.Caption = "Bind IP:"
    g_ui["edit_recv_ip"] = FBEdit(); g_ui["edit_recv_ip"].Text = "0.0.0.0"
    lyt_ip.Add(lbl_ip, 70)
    lyt_ip.Add(g_ui["edit_recv_ip"], 100)
    view.Add(lyt_ip, 30)
    
    lyt_port = FBHBoxLayout()
    lbl_port = FBLabel(); lbl_port.Caption = "UDP Port:"
    g_ui["edit_recv_port"] = FBEdit()
    g_ui["edit_recv_port"].Text = "10000"
    lyt_port.Add(lbl_port, 70)
    lyt_port.Add(g_ui["edit_recv_port"], 100)
    view.Add(lyt_port, 30)
    
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    view.Add(g_ui["btn_connect"], 35)
    
    view.Add(create_header("OSC DATA"), 25)
    btn_create_data = FBButton(); btn_create_data.Caption = "Create Data Channels on OSC_Data"
    btn_create_data.OnClick.Add(OnCreateDataChannelsClick)
    view.Add(btn_create_data, 35)
    
    btn_connect_model = FBButton(); btn_connect_model.Caption = "Connect Channels to Selected Model"
    btn_connect_model.OnClick.Add(OnConnectToModelClick)
    view.Add(btn_connect_model, 35)
    
    view.Add(create_header("RESET"), 25)
    btn_delete = FBButton(); btn_delete.Caption = "Delete OSC_Data & Reset"
    btn_delete.OnClick.Add(OnDeleteDataClick)
    view.Add(btn_delete, 35)
    
    g_ui["lbl_recv_status"] = FBLabel()
    g_ui["lbl_recv_status"].Caption = "Status: Disconnected"
    view.Add(g_ui["lbl_recv_status"], 35)

def PopulateTool(tool):
    tool.StartSizeX = 300
    tool.StartSizeY = 430
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    
    y_tab = FBAddRegionParam(25, FBAttachType.kFBAttachNone, "")
    tool.AddRegion("tab", "tab", x, y, w, y_tab)
    
    tab_panel = FBTabPanel()
    tab_panel.Items.append("Receiver (OSC -> Mobu)")
    tab_panel.Items.append("Sender (Mobu -> OSC)")
    tool.SetControl("tab", tab_panel)
    
    y_content_start = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "tab")
    tool.AddRegion("content", "content", x, y_content_start, w, h)
    
    view_receiver = FBVBoxLayout()
    BuildReceiverView(view_receiver)
    
    view_sender = FBVBoxLayout()
    BuildSenderView(view_sender)
    
    # Default view
    tool.SetControl("content", view_receiver)
    
    def OnTabChange(control, event):
        if control.ItemIndex == 0:
            tool.SetControl("content", view_receiver)
        else:
            tool.SetControl("content", view_sender)
            
    tab_panel.OnChange.Add(OnTabChange)

def CreateTool():
    tool_name = "Saint's MobuOSC Manager"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\n整合了 Sender 與 Receiver 功能\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
