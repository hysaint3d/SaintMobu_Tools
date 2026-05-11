"""
LiveLinkFace_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receive Apple LiveLink Face (ARKit) blendshape data via UDP and stream it
into MotionBuilder as animatable custom properties on a LiveLink_Data null.

Workflow:
  1. Select Actor (1-3)
  2. Set Bind IP & UDP Port → Connect
  3. Create Data Channels on LiveLink_Data
  4. Connect Channels to Selected Model (Relation Constraint)
  5. (Optional) Record LiveLink — creates a new timestamped Take

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import sys
import socket
import struct
import math
import time
import csv
from pyfbsdk import *
from pyfbsdk_additions import *

# Clean up previous state if it exists from older script versions
if hasattr(sys, "livelink_states") and sys.livelink_states is not None:
    try: FBSystem().OnUIIdle.Remove(sys.livelink_state_idle_func)
    except: pass
    for state in sys.livelink_states.values():
        if getattr(state, "sock", None):
            try: state.sock.close()
            except: pass

if hasattr(sys, "livelink_state") and sys.livelink_state is not None:
    if getattr(sys.livelink_state, "sock", None):
        try: sys.livelink_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.livelink_state_idle_func)
    except: pass
    sys.livelink_state = None

class LiveLinkState:
    def __init__(self, actor_id):
        self.actor_id = actor_id
        self.sock = None
        self.is_connected = False
        self.bind_ip = "0.0.0.0"
        self.port = 11111 + (actor_id - 1)
        self.livelink_data_cache = {}
        self.models = {}
        self.prop_cache = {}
        self.last_ui_update = 0.0
        self.last_applied_cache = {}
        self.force_recording = False
        self.import_csv_path = ""
        self.trigger_app = False
        self.app_ip = "192.168.1.100"
        self.app_port = 8000
        self.device_name = "Unknown"
        self.last_eval_time = 0.0

sys.livelink_states = {1: LiveLinkState(1), 2: LiveLinkState(2), 3: LiveLinkState(3)}
g_livelink_states = sys.livelink_states
g_ui = {} 

def current_actor():
    try: return g_ui["list_actor"].ItemIndex + 1
    except: return 1

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

def parse_livelink(data, state):
    try:
        if len(data) < 62: return False
        version = struct.unpack('<i', data[0:4])[0]
        if version != 6: return False
        
        name_length = struct.unpack('!i', data[41:45])[0]
        name_end_pos = 45 + name_length
        if len(data) < name_end_pos + 17 + (61 * 4): return False

        # Only decode device name if it's currently unknown
        if state.device_name == "Unknown":
            name_bytes = data[45:name_end_pos]
            state.device_name = name_bytes.decode('utf-8', errors='ignore').rstrip('\x00')
        
        frame_number, sub_frame, fps, denominator, data_length = struct.unpack(
            "!if2ib", data[name_end_pos:name_end_pos + 17])
            
        if data_length != 61: return False
        
        blend_data = struct.unpack("!61f", data[name_end_pos + 17 : name_end_pos + 17 + (61 * 4)])
        
        for idx, val in enumerate(blend_data):
            if idx < len(arkit_blendshapes):
                bs_name = arkit_blendshapes[idx]
                state.livelink_data_cache[bs_name] = val * 100.0
        return True
    except:
        return False

def encode_osc_str(s):
    b = s.encode('utf-8') + b'\x00'
    pad = (4 - len(b) % 4) % 4
    return b + b'\x00' * pad

def send_osc_record_start(ip, port, slate_name):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = encode_osc_str("/RecordStart") + encode_osc_str(",si") + encode_osc_str(slate_name) + struct.pack('>i', 1)
        sock.sendto(msg, (ip, port))
        sock.close()
        print(f"Sent /RecordStart to {ip}:{port} with slate '{slate_name}'")
    except Exception as e:
        print("Failed to send /RecordStart:", e)

def send_osc_record_stop(ip, port):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        msg = encode_osc_str("/RecordStop")
        sock.sendto(msg, (ip, port))
        sock.close()
        print(f"Sent /RecordStop to {ip}:{port}")
    except Exception as e:
        print("Failed to send /RecordStop:", e)

def OnUIIdle(control, event):
    for act_id, state in g_livelink_states.items():
        if not state.is_connected or not state.sock:
            continue
            
        packets_processed = 0
        last_packet_size = 0
        # Reduced limit to 500 to prevent blocking main thread too long
        while packets_processed < 500:
            try:
                data, addr = state.sock.recvfrom(65536)
                last_packet_size = len(data)
                
                # Check if it's a LiveLink Face binary packet (starts with int 6)
                if len(data) > 0 and data[0] == 6:
                    parse_livelink(data, state)
                    
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
            if current_time - state.last_ui_update > 0.1:
                state.last_ui_update = current_time
                if current_actor() == act_id:
                    try:
                        status_text = "Receiving: [{}] (Channels: {})".format(state.device_name, len(state.livelink_data_cache))
                        g_ui["lbl_status"].Caption = status_text
                    except: pass
                
        # Real-time update for properties (Throttled to max 120fps to save CPU)
        current_time = time.time()
        if current_time - state.last_eval_time < 0.008: # ~120fps limit
            continue
        state.last_eval_time = current_time

        node_name = "LiveLink_Data_{}".format(act_id)
        if node_name in state.models:
            ll_node = state.models[node_name]
            
            # Check if MotionBuilder is currently recording and playing
            is_recording = False
            try:
                player = FBPlayerControl()
                is_recording = player.IsPlaying and (player.IsRecording or getattr(state, 'force_recording', False) or getattr(sys, 'mobu_master_recording', False))
            except:
                pass
                
            try:
                for prop_name, val in state.livelink_data_cache.items():
                    prop = state.prop_cache.get(prop_name)
                    if not prop:
                        prop = ll_node.PropertyList.Find(prop_name)
                        if prop:
                            state.prop_cache[prop_name] = prop
                    if prop:
                        # Only update if the value changed significantly to prevent evaluation lag
                        last_val = state.last_applied_cache.get(prop_name)
                        value_changed = last_val is None or abs(last_val - val) > 0.001
                        if value_changed:
                            prop.Data = float(val)
                            state.last_applied_cache[prop_name] = val
                            
                            # If recording, key the property
                            if is_recording:
                                try:
                                    prop.Key()
                                except:
                                    pass
            except:
                pass

def OnActorChange(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    
    g_ui["edit_ip"].Text = state.bind_ip
    g_ui["edit_port"].Text = str(state.port)
    
    if state.is_connected:
        g_ui["btn_connect"].Caption = "Disconnect"
        if len(state.livelink_data_cache) > 0:
            g_ui["lbl_status"].Caption = "Receiving Data (Channels: {})".format(len(state.livelink_data_cache))
        else:
            g_ui["lbl_status"].Caption = "Status: Connected ({})".format(state.port)
    else:
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Status: Disconnected"
        
    g_ui["edit_csv_path"].Text = os.path.basename(state.import_csv_path) if state.import_csv_path else ""

def OnConnectClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    if not state.is_connected:
        try:
            state.bind_ip = g_ui["edit_ip"].Text
            state.port = int(g_ui["edit_port"].Text.strip())
            state.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            state.sock.bind((state.bind_ip, state.port))
            state.sock.setblocking(False)
            state.is_connected = True
            
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_status"].Caption = "Status: Connected ({})".format(state.port)
            print("Actor {} Receiver started on port {}".format(act_id, state.port))
            
            sys = FBSystem()
            sys.OnUIIdle.Remove(OnUIIdle)
            sys.OnUIIdle.Add(OnUIIdle)
            sys.livelink_state_idle_func = OnUIIdle
            
            # Performance: Increase socket receive buffer to 1MB
            try: state.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)
            except: pass
            
        except Exception as e:
            g_ui["lbl_status"].Caption = "Status: Error binding port!"
            print("Failed to bind socket:", e)
    else:
        if state.sock:
            state.sock.close()
            state.sock = None
        state.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Status: Disconnected"
        print("Actor {} Receiver stopped.".format(act_id))

def OnCreateDataChannelsClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    if not state.livelink_data_cache:
        FBMessageBox("Warning", "No Live Link data received yet for Actor {}!\nPlease send data and wait a second.".format(act_id), "OK")
        return
        
    node_name = "LiveLink_Data_{}".format(act_id)
    ll_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == node_name:
            ll_node = m
            break
            
    if not ll_node:
        ll_node = FBModelNull(node_name)
        ll_node.Show = True
        ll_node.Size = 50.0
    
    state.models[node_name] = ll_node
        
    count = 0
    for prop_name in state.livelink_data_cache.keys():
        prop = ll_node.PropertyList.Find(prop_name)
        if not prop:
            prop = ll_node.PropertyCreate(prop_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    FBMessageBox("Success", "Created/Updated {} data channels on {}!".format(count, node_name), "OK")

def FindAnimationNode(parent_node, name):
    if not parent_node: return None
    for node in parent_node.Nodes:
        if node.Name == name:
            return node
        found = FindAnimationNode(node, name)
        if found: return found
    return None

def OnConnectToModelClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    node_name = "LiveLink_Data_{}".format(act_id)
    
    ll_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == node_name:
            ll_node = m
            break
            
    if not ll_node:
        FBMessageBox("Warning", "{} node not found! Please run 'Create Data Channels' first.".format(node_name), "OK")
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
    
    relation = FBConstraintRelation("LiveLink_Expression_Link_{}".format(act_id))
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
    act_id = current_actor()
    state = g_livelink_states[act_id]
    node_name = "LiveLink_Data_{}".format(act_id)
    
    for m in list(FBSystem().Scene.RootModel.Children):
        try:
            if m.Name == node_name:
                m.FBDelete()
        except Exception:
            pass
                
    state.models.clear()
    state.livelink_data_cache.clear()
    state.prop_cache.clear()
    state.last_applied_cache.clear()
    
    if state.sock:
        try: state.sock.close()
        except: pass
        state.sock = None
    state.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Status: Disconnected / Reset"
    
    # Do not remove OnUIIdle if other actors are still connected
    active_connections = sum(1 for s in g_livelink_states.values() if s.is_connected)
    if active_connections == 0:
        try: FBSystem().OnUIIdle.Remove(OnUIIdle)
        except: pass
    
    FBMessageBox("Success", "Cleaned up {} and reset Network Port.".format(node_name), "OK")

def OnForceRecordClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    import time
    state.force_recording = not getattr(state, 'force_recording', False)
    
    if state.force_recording:
        control.Caption = "⏹ Stop Recording LiveLink"
        
        try:
            take_name = "LiveLink_Take_" + time.strftime("%Y%m%d_%H%M%S")
            new_take = None
            for take in FBSystem().Scene.Takes:
                if take.Name == take_name:
                    new_take = take
                    break
            
            if not new_take:
                new_take = FBTake(take_name)
                FBSystem().Scene.Takes.append(new_take)
            
            FBSystem().CurrentTake = new_take
            
            # Read duration from UI, fallback to 10 mins (600s)
            duration_sec = 600.0
            try:
                if "edit_record_len" in g_ui:
                    val = float(g_ui["edit_record_len"].Value)
                    if val > 0: duration_sec = val
            except: pass
            
            # Set a very long duration so it doesn't stop prematurely
            long_end_time = FBTime()
            try: long_end_time.SetSecondDouble(duration_sec)
            except: pass
            
            start_time = FBTime(0)
            new_take.LocalTimeSpan = FBTimeSpan(start_time, long_end_time)
            
            player = FBPlayerControl()
            player.LoopStop = long_end_time
            
        except Exception as e:
            print("Error creating take:", e)
            
        try: FBPlayerControl().GotoStart()
        except: pass
        FBPlayerControl().Play()
        
        # Trigger OSC App Recording
        if g_ui["chk_trigger_app"].State == 1:
            app_ip = g_ui["edit_app_ip"].Text.strip()
            try:
                app_port = int(g_ui["edit_app_port"].Text.strip())
                take_name = new_take.Name if new_take else "MobuTake"
                send_osc_record_start(app_ip, app_port, take_name)
            except Exception as e:
                print("Invalid App Port or IP:", e)
        
    else:
        control.Caption = "🔴 Record LiveLink"
        FBPlayerControl().Stop()
        
        # Trigger OSC App Stop
        if g_ui["chk_trigger_app"].State == 1:
            app_ip = g_ui["edit_app_ip"].Text.strip()
            try:
                app_port = int(g_ui["edit_app_port"].Text.strip())
                send_osc_record_stop(app_ip, app_port)
            except Exception as e:
                print("Invalid App Port or IP:", e)
        
        try:
            stop_time = FBSystem().LocalTime
            take = FBSystem().CurrentTake
            if take:
                start_time = take.LocalTimeSpan.GetStart()
                take.LocalTimeSpan = FBTimeSpan(start_time, stop_time)
                FBPlayerControl().LoopStop = stop_time
        except Exception as e:
            print("Error setting out point:", e)

# ── CSV Importer Logic ────────────────────────────────────────────────────────
def OnBrowseCSVClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    file_popup = FBFilePopup()
    file_popup.Style = FBFilePopupStyle.kFBFilePopupOpen
    file_popup.Filter = "*.csv"
    file_popup.Caption = "Select LiveLink Face CSV"
    if file_popup.Execute():
        state.import_csv_path = file_popup.FullFilename
        g_ui["edit_csv_path"].Text = os.path.basename(state.import_csv_path)

def OnImportCSVClick(control, event):
    act_id = current_actor()
    state = g_livelink_states[act_id]
    node_name = "LiveLink_Data_{}".format(act_id)
    
    csv_path = state.import_csv_path
    if not csv_path or not os.path.exists(csv_path):
        FBMessageBox("Error", "Please select a valid CSV file first.", "OK")
        return

    # Find or create Null
    ll_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == node_name:
            ll_node = m; break
    if not ll_node:
        ll_node = FBModelNull(node_name)
        ll_node.Show = True; ll_node.Size = 50.0
    state.models[node_name] = ll_node

    try:
        with open(csv_path, 'r') as f:
            reader = list(csv.DictReader(f))
    except Exception as e:
        FBMessageBox("Error", f"Failed to read CSV:\n{e}", "OK")
        return

    if not reader:
        FBMessageBox("Error", "CSV file is empty.", "OK")
        return

    # Process keys
    progress = FBProgress()
    progress.Caption = "Baking CSV Keys..."
    
    baked_props = 0
    total_frames = len(reader)
    progress.MaxValue = total_frames
    
    # We assume 60fps or the timecode in CSV. Standard app export is usually 60fps.
    # Let's use FBTime for each frame index.
    for i, row in enumerate(reader):
        t = FBTime(0)
        t.SetFrame(i)
        for prop_name, val_str in row.items():
            if prop_name in ["Timecode", "BlendshapeCount"]: continue
            
            prop = ll_node.PropertyList.Find(prop_name)
            if not prop:
                prop = ll_node.PropertyCreate(prop_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
                if prop: prop.SetAnimated(True)
            
            if prop:
                try:
                    val = float(val_str) * 100.0 # Standardize to 0-100 range for MB
                    anim_node = prop.GetAnimationNode()
                    if anim_node:
                        anim_node.KeyAdd(t, val)
                except: pass
        
        if i % 100 == 0:
            FBSystem().Scene.Evaluate()
            progress.StepIt()

    progress.Done()
    FBMessageBox("Success", f"Baking complete!\nImported {total_frames} frames to {node_name}.", "OK")

def create_header(text):
    lbl = FBLabel()
    lbl.Caption = "--- " + text + " ---"
    lbl.Justify = FBTextJustify.kFBTextJustifyCenter
    return lbl

def BuildLiveView(view):
    view.Add(create_header("CONNECT"), 25)
    view.Add(g_ui["lyt_ip"], 30)
    view.Add(g_ui["lyt_port"], 30)
    view.Add(g_ui["btn_connect"], 35)
    
    view.Add(create_header("LIVELINK DATA"), 25)
    view.Add(g_ui["btn_create_data"], 35)
    view.Add(g_ui["btn_connect_model"], 35)
    
    view.Add(create_header("RECORDING"), 25)
    view.Add(g_ui["lyt_record_len"], 30)
    view.Add(g_ui["btn_force_record"], 35)
    
    view.Add(create_header("RESET"), 25)
    view.Add(g_ui["btn_delete"], 35)
    view.Add(g_ui["lbl_status"], 35)

def BuildImporterView(view):
    view.Add(create_header("CSV FILE"), 25)
    lyt_file = FBHBoxLayout()
    g_ui["edit_csv_path"] = FBEdit(); g_ui["edit_csv_path"].Enabled = False
    btn_browse = FBButton(); btn_browse.Caption = "..."; btn_browse.OnClick.Add(OnBrowseCSVClick)
    lyt_file.Add(g_ui["edit_csv_path"], 150)
    lyt_file.Add(btn_browse, 40)
    view.Add(lyt_file, 30)
    
    btn_bake = FBButton(); btn_bake.Caption = "Bake CSV to Node"; btn_bake.OnClick.Add(OnImportCSVClick)
    view.Add(btn_bake, 35)
    
    view.Add(create_header("CONSTRAIN"), 25)
    btn_conn = FBButton(); btn_conn.Caption = "Link Null to Selected Model"; btn_conn.OnClick.Add(OnConnectToModelClick)
    view.Add(btn_conn, 35)
    
    lbl_hint = FBLabel()
    lbl_hint.Caption = "Note: Matches property names\nexactly to ARKit standard."
    view.Add(lbl_hint, 40)

def BuildAppSyncView(view):
    view.Add(create_header("APP OSC CONTROL"), 25)
    
    g_ui["chk_trigger_app"] = FBButton()
    g_ui["chk_trigger_app"].Style = FBButtonStyle.kFBCheckbox
    g_ui["chk_trigger_app"].Caption = "Trigger App Recording via OSC"
    g_ui["chk_trigger_app"].State = 0
    view.Add(g_ui["chk_trigger_app"], 30)
    
    lyt_app_ip = FBHBoxLayout()
    lbl_app_ip = FBLabel(); lbl_app_ip.Caption = "App IP:"
    g_ui["edit_app_ip"] = FBEdit(); g_ui["edit_app_ip"].Text = "192.168.1.100"
    lyt_app_ip.Add(lbl_app_ip, 60)
    lyt_app_ip.Add(g_ui["edit_app_ip"], 130)
    view.Add(lyt_app_ip, 30)
    
    lyt_app_port = FBHBoxLayout()
    lbl_app_port = FBLabel(); lbl_app_port.Caption = "App Port:"
    g_ui["edit_app_port"] = FBEdit(); g_ui["edit_app_port"].Text = "8000"
    lyt_app_port.Add(lbl_app_port, 60)
    lyt_app_port.Add(g_ui["edit_app_port"], 130)
    view.Add(lyt_app_port, 30)
    
    lbl_sync_hint = FBLabel()
    lbl_sync_hint.Caption = "Note: Slate name will match\nMotionBuilder Take name."
    view.Add(lbl_sync_hint, 40)

def PopulateTool(tool):
    tool.StartSizeX = 220
    tool.StartSizeY = 570 # Increased for actor list
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    
    # Actor List Region
    y_actor = FBAddRegionParam(25, FBAttachType.kFBAttachNone, "")
    tool.AddRegion("actor", "actor", x, y, w, y_actor)
    
    # Tab Region
    y_tab_top = FBAddRegionParam(5, FBAttachType.kFBAttachBottom, "actor")
    y_tab_bot = FBAddRegionParam(25, FBAttachType.kFBAttachNone, "")
    tool.AddRegion("tab", "tab", x, y_tab_top, w, y_tab_bot)
    
    y_content = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "tab")
    tool.AddRegion("main", "main", x, y_content, w, h)
    
    # Actor UI
    g_ui["list_actor"] = FBList()
    g_ui["list_actor"].Style = FBListStyle.kFBDropDownList
    g_ui["list_actor"].Items.append("👤 Actor 1 Face")
    g_ui["list_actor"].Items.append("👤 Actor 2 Face")
    g_ui["list_actor"].Items.append("👤 Actor 3 Face")
    g_ui["list_actor"].ItemIndex = 0
    g_ui["list_actor"].OnChange.Add(OnActorChange)
    tool.SetControl("actor", g_ui["list_actor"])
    
    # Tabs
    tab_panel = FBTabPanel()
    tab_panel.Items.append("Live")
    tab_panel.Items.append("App Sync")
    tab_panel.Items.append("Importer")
    tool.SetControl("tab", tab_panel)
    
    g_ui["lyt_ip"] = FBHBoxLayout()
    g_ui["lbl_ip"] = FBLabel()
    g_ui["lbl_ip"].Caption = "Bind IP:"
    g_ui["edit_ip"] = FBEdit()
    g_ui["edit_ip"].Text = "0.0.0.0"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 60)
    g_ui["lyt_ip"].Add(g_ui["edit_ip"], 130)
    
    g_ui["lyt_port"] = FBHBoxLayout()
    g_ui["lbl_port"] = FBLabel()
    g_ui["lbl_port"].Caption = "UDP Port:"
    g_ui["edit_port"] = FBEdit()
    g_ui["edit_port"].Text = "11111" # Live Link Face default port
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 60)
    g_ui["lyt_port"].Add(g_ui["edit_port"], 130)
    
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    
    g_ui["btn_create_data"] = FBButton()
    g_ui["btn_create_data"].Caption = "Create Data Channels"
    g_ui["btn_create_data"].OnClick.Add(OnCreateDataChannelsClick)
    
    g_ui["btn_connect_model"] = FBButton()
    g_ui["btn_connect_model"].Caption = "Connect to Selected Model"
    g_ui["btn_connect_model"].OnClick.Add(OnConnectToModelClick)
    
    g_ui["btn_delete"] = FBButton()
    g_ui["btn_delete"].Caption = "Delete Data & Reset"
    g_ui["btn_delete"].OnClick.Add(OnDeleteDataClick)
    
    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "Status: Disconnected"
    
    g_ui["lyt_record_len"] = FBHBoxLayout()
    g_ui["lbl_record_len"] = FBLabel()
    g_ui["lbl_record_len"].Caption = "Rec length (sec):"
    g_ui["edit_record_len"] = FBEditNumber()
    g_ui["edit_record_len"].Value = 600
    g_ui["edit_record_len"].Precision = 0
    g_ui["lyt_record_len"].Add(g_ui["lbl_record_len"], 100)
    g_ui["lyt_record_len"].Add(g_ui["edit_record_len"], 90)
    
    g_ui["btn_force_record"] = FBButton()
    g_ui["btn_force_record"].Caption = "🔴 Record LiveLink"
    g_ui["btn_force_record"].OnClick.Add(OnForceRecordClick)
    
    view_live = FBVBoxLayout()
    BuildLiveView(view_live)
    
    view_importer = FBVBoxLayout()
    BuildImporterView(view_importer)
    
    view_app_sync = FBVBoxLayout()
    BuildAppSyncView(view_app_sync)
    
    # Default view
    tool.SetControl("main", view_live)
    
    def OnTabChange(control, event):
        if control.ItemIndex == 0:
            tool.SetControl("main", view_live)
        elif control.ItemIndex == 1:
            tool.SetControl("main", view_app_sync)
        else:
            tool.SetControl("main", view_importer)
            
    tab_panel.OnChange.Add(OnTabChange)
    
    # Initialize UI state
    OnActorChange(None, None)

def CreateTool():
    tool_name = "LiveLinkFace_Toolkit"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
