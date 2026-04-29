import os
import sys
import socket
import struct
import math
from pyfbsdk import *
from pyfbsdk_additions import *

# Global state to prevent garbage collection
class VMCState:
    def __init__(self):
        self.sock = None
        self.is_connected = False
        self.bone_data_cache = {}
        self.blend_data_cache = {}
        self.blend_props_created = False
        self.models = {}
        self.prop_cache = {}
        self.last_applied_cache = {}
        self.force_recording = False

# Store in sys to persist across script re-runs and prevent port leaks
if hasattr(sys, "vmc_state") and sys.vmc_state is not None:
    if sys.vmc_state.sock:
        try: sys.vmc_state.sock.close()
        except: pass
    try: FBSystem().OnUIIdle.Remove(sys.vmc_state_idle_func)
    except: pass
    
sys.vmc_state = VMCState()
g_vmc = sys.vmc_state
g_ui = {} # Store UI elements

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

def vmc_to_mb(px, py, pz, qx, qy, qz, qw):
    mb_px = px * 100.0
    mb_py = py * 100.0
    mb_pz = -pz * 100.0
    
    x, y, z, w = qx, qy, -qz, -qw
    ysqr = y * y
    t0 = 2.0 * (w * x + y * z)
    t1 = 1.0 - 2.0 * (x * x + ysqr)
    euler_x = math.degrees(math.atan2(t0, t1))
    
    t2 = 2.0 * (w * y - z * x)
    t2 = max(-1.0, min(1.0, t2))
    euler_y = math.degrees(math.asin(t2))
    
    t3 = 2.0 * (w * z + x * y)
    t4 = 1.0 - 2.0 * (ysqr + z * z)
    euler_z = math.degrees(math.atan2(t3, t4))
    
    return FBVector3d(mb_px, mb_py, mb_pz), FBVector3d(euler_x, euler_y, euler_z)

def process_osc_message(address, args, is_recording=False):
    if not address or len(args) < 2:
        return
        
    if address == "/VMC/Ext/Bone/Pos" and len(args) >= 8:
        b_name = args[0]
        px, py, pz = args[1], args[2], args[3]
        qx, qy, qz, qw = args[4], args[5], args[6], args[7]
        
        g_vmc.bone_data_cache[b_name] = (px, py, pz, qx, qy, qz, qw)
        
        if b_name in g_vmc.models:
            m = g_vmc.models[b_name]
            mb_p, mb_r = vmc_to_mb(px, py, pz, qx, qy, qz, qw)
            try:
                m.SetVector(mb_p, FBModelTransformationType.kModelTranslation, False)
                m.SetVector(mb_r, FBModelTransformationType.kModelRotation, False)
                if is_recording:
                    try:
                        m.Translation.Key()
                        m.Rotation.Key()
                    except:
                        pass
            except:
                pass
            
    elif address == "/VMC/Ext/Root/Pos" and len(args) >= 8:
        b_name = args[0]
        px, py, pz = args[1], args[2], args[3]
        qx, qy, qz, qw = args[4], args[5], args[6], args[7]
        
        g_vmc.bone_data_cache["Root"] = (px, py, pz, qx, qy, qz, qw)
        if "Root" in g_vmc.models:
            m = g_vmc.models["Root"]
            mb_p, mb_r = vmc_to_mb(px, py, pz, qx, qy, qz, qw)
            try:
                m.SetVector(mb_p, FBModelTransformationType.kModelTranslation, False)
                m.SetVector(mb_r, FBModelTransformationType.kModelRotation, False)
                if is_recording:
                    try:
                        m.Translation.Key()
                        m.Rotation.Key()
                    except:
                        pass
            except:
                pass
            
    elif address == "/VMC/Ext/Blend/Val":
        b_name = args[0]
        val = args[1]
        # Multiply VMC's 0~1 range by 100 to match MotionBuilder's 0~100 range
        g_vmc.blend_data_cache[b_name] = val * 100.0

def OnUIIdle(control, event):
    if not g_vmc.is_connected or not g_vmc.sock:
        return
        
    # Check if MotionBuilder is currently recording and playing
    is_recording = False
    try:
        player = FBPlayerControl()
        is_recording = player.IsPlaying and (player.IsRecording or getattr(g_vmc, 'force_recording', False))
    except:
        pass

    packets_processed = 0
    last_packet_size = 0
    while packets_processed < 100:
        try:
            data, addr = g_vmc.sock.recvfrom(65536)
            last_packet_size = len(data)
            address, args = parse_osc(data)
            
            if data.startswith(b'#bundle'):
                offset = 16
                while offset < len(data):
                    size = struct.unpack('>i', data[offset:offset+4])[0]
                    offset += 4
                    msg_data = data[offset:offset+size]
                    msg_address, msg_args = parse_osc(msg_data)
                    process_osc_message(msg_address, msg_args, is_recording)
                    offset += size
            else:
                process_osc_message(address, args, is_recording)
                
            packets_processed += 1
            
        except BlockingIOError:
            break
        except socket.error as e:
            if e.errno == 10035: break
            break
        except Exception as e:
            break
            
    if last_packet_size > 0:
        try:
            if len(g_vmc.bone_data_cache) > 0:
                g_ui["lbl_status"].Caption = "Receiving Data (Bones: {}, Expr: {})".format(len(g_vmc.bone_data_cache), len(g_vmc.blend_data_cache))
            else:
                g_ui["lbl_status"].Caption = "Got RAW data ({} bytes), but no bones parsed yet.".format(last_packet_size)
        except Exception:
            try:
                FBSystem().OnUIIdle.Remove(OnUIIdle)
            except: pass
            
    # Real-time update for blendshapes
    if g_vmc.blend_props_created and "Facial" in g_vmc.models:
        facial_node = g_vmc.models["Facial"]
        try:
            for b_name, val in g_vmc.blend_data_cache.items():
                prop = g_vmc.prop_cache.get(b_name)
                if not prop:
                    prop = facial_node.PropertyList.Find(b_name)
                    if prop:
                        g_vmc.prop_cache[b_name] = prop
                if prop:
                    # Only update if value changed significantly
                    last_val = g_vmc.last_applied_cache.get(b_name)
                    value_changed = last_val is None or abs(last_val - val) > 0.001
                    if value_changed:
                        prop.Data = float(val)
                        g_vmc.last_applied_cache[b_name] = val
                        
                        # If recording, key the property
                        if is_recording:
                            try:
                                prop.Key()
                            except:
                                pass
        except:
            pass

def OnConnectClick(control, event):
    if not g_vmc.is_connected:
        try:
            ip = g_ui["edit_ip"].Text
            port = int(g_ui["edit_port"].Value)
            g_vmc.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            g_vmc.sock.bind((ip, port))
            g_vmc.sock.setblocking(False)
            g_vmc.is_connected = True
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_status"].Caption = "Status: Connected ({})".format(port)
            print("VMC Receiver started on port", port)
            
            sys = FBSystem()
            sys.OnUIIdle.Remove(OnUIIdle)
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.vmc_state_idle_func = OnUIIdle
            
        except Exception as e:
            g_ui["lbl_status"].Caption = "Status: Error binding port!"
            print("Failed to bind socket:", e)
    else:
        if g_vmc.sock:
            g_vmc.sock.close()
            g_vmc.sock = None
        g_vmc.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Status: Disconnected"
        sys = FBSystem()
        sys.OnUIIdle.Remove(OnUIIdle)
        print("VMC Receiver stopped.")

def OnGenerateClick(control, event):
    if not g_vmc.bone_data_cache:
        FBMessageBox("Warning", "No VMC data received yet!\nPlease send VMC data and wait a second.", "OK")
        return
        
    print("Generating Skeleton based on VMC data...")
    
    unity_hierarchy = {
        "Hips": None, "Spine": "Hips", "Chest": "Spine", "UpperChest": "Chest",
        "Neck": "UpperChest", "Head": "Neck",
        "LeftEye": "Head", "RightEye": "Head", "Jaw": "Head",
        "LeftShoulder": "UpperChest", "LeftUpperArm": "LeftShoulder", "LeftLowerArm": "LeftUpperArm", "LeftHand": "LeftLowerArm",
        "RightShoulder": "UpperChest", "RightUpperArm": "RightShoulder", "RightLowerArm": "RightUpperArm", "RightHand": "RightLowerArm",
        "LeftUpperLeg": "Hips", "LeftLowerLeg": "LeftUpperLeg", "LeftFoot": "LeftLowerLeg", "LeftToes": "LeftFoot",
        "RightUpperLeg": "Hips", "RightLowerLeg": "RightUpperLeg", "RightFoot": "RightLowerLeg", "RightToes": "RightFoot",
        
        # Fingers
        "LeftThumbProximal": "LeftHand", "LeftThumbIntermediate": "LeftThumbProximal", "LeftThumbDistal": "LeftThumbIntermediate",
        "LeftIndexProximal": "LeftHand", "LeftIndexIntermediate": "LeftIndexProximal", "LeftIndexDistal": "LeftIndexIntermediate",
        "LeftMiddleProximal": "LeftHand", "LeftMiddleIntermediate": "LeftMiddleProximal", "LeftMiddleDistal": "LeftMiddleIntermediate",
        "LeftRingProximal": "LeftHand", "LeftRingIntermediate": "LeftRingProximal", "LeftRingDistal": "LeftRingIntermediate",
        "LeftLittleProximal": "LeftHand", "LeftLittleIntermediate": "LeftLittleProximal", "LeftLittleDistal": "LeftLittleIntermediate",
        
        "RightThumbProximal": "RightHand", "RightThumbIntermediate": "RightThumbProximal", "RightThumbDistal": "RightThumbIntermediate",
        "RightIndexProximal": "RightHand", "RightIndexIntermediate": "RightIndexProximal", "RightIndexDistal": "RightIndexIntermediate",
        "RightMiddleProximal": "RightHand", "RightMiddleIntermediate": "RightMiddleProximal", "RightMiddleDistal": "RightMiddleIntermediate",
        "RightRingProximal": "RightHand", "RightRingIntermediate": "RightRingProximal", "RightRingDistal": "RightRingIntermediate",
        "RightLittleProximal": "RightHand", "RightLittleIntermediate": "RightLittleProximal", "RightLittleDistal": "RightLittleIntermediate"
    }
    
    for b_name in g_vmc.bone_data_cache.keys():
        if b_name == "Root": continue
        if b_name not in g_vmc.models:
            m = FBModelSkeleton("VMC_" + b_name)
            m.Show = True
            m.Size = 10.0
            m.Translation.SetAnimated(True)
            m.Rotation.SetAnimated(True)
            g_vmc.models[b_name] = m
            
    for b_name, m in g_vmc.models.items():
        parent_name = unity_hierarchy.get(b_name)
        if parent_name == "UpperChest" and "UpperChest" not in g_vmc.models:
            parent_name = "Chest"
        elif parent_name == "Chest" and "Chest" not in g_vmc.models:
            parent_name = "Spine"
            
        if parent_name and parent_name in g_vmc.models:
            m.Parent = g_vmc.models[parent_name]
            
    if "Root" not in g_vmc.models:
        m = FBModelNull("VMC_Root")
        m.Show = True
        m.Size = 50.0
        m.Translation.SetAnimated(True)
        m.Rotation.SetAnimated(True)
        g_vmc.models["Root"] = m
        if "Hips" in g_vmc.models:
            g_vmc.models["Hips"].Parent = m
            
    FBSystem().Scene.Evaluate()
    FBMessageBox("Success", "Skeleton Generated Successfully!", "OK")

def OnCharacterizeClick(control, event):
    if not g_vmc.models:
        FBMessageBox("Warning", "Please click 'Generate Skeleton' first!", "OK")
        return
        
    print("Auto-Characterizing...")
    char_name = "VMC_HIK_Character"
    char = None
    for c in FBSystem().Scene.Characters:
        if c.Name == char_name:
            char = c
            break
            
    if not char:
        char = FBCharacter(char_name)
        
    char.SetCharacterizeOn(False)
    
    mapping = {
        "Hips": "HipsLink", "Spine": "SpineLink", "Chest": "Spine1Link", "UpperChest": "Spine2Link",
        "Neck": "NeckLink", "Head": "HeadLink",
        "LeftShoulder": "LeftShoulderLink", "LeftUpperArm": "LeftArmLink", "LeftLowerArm": "LeftForeArmLink", "LeftHand": "LeftHandLink",
        "RightShoulder": "RightShoulderLink", "RightUpperArm": "RightArmLink", "RightLowerArm": "RightForeArmLink", "RightHand": "RightHandLink",
        "LeftUpperLeg": "LeftUpLegLink", "LeftLowerLeg": "LeftLegLink", "LeftFoot": "LeftFootLink", "LeftToes": "LeftToeBaseLink",
        "RightUpperLeg": "RightUpLegLink", "RightLowerLeg": "RightLegLink", "RightFoot": "RightFootLink", "RightToes": "RightToeBaseLink",
        
        # Left Fingers
        "LeftThumbProximal": "LeftHandThumb1Link", "LeftThumbIntermediate": "LeftHandThumb2Link", "LeftThumbDistal": "LeftHandThumb3Link",
        "LeftIndexProximal": "LeftHandIndex1Link", "LeftIndexIntermediate": "LeftHandIndex2Link", "LeftIndexDistal": "LeftHandIndex3Link",
        "LeftMiddleProximal": "LeftHandMiddle1Link", "LeftMiddleIntermediate": "LeftHandMiddle2Link", "LeftMiddleDistal": "LeftHandMiddle3Link",
        "LeftRingProximal": "LeftHandRing1Link", "LeftRingIntermediate": "LeftHandRing2Link", "LeftRingDistal": "LeftHandRing3Link",
        "LeftLittleProximal": "LeftHandPinky1Link", "LeftLittleIntermediate": "LeftHandPinky2Link", "LeftLittleDistal": "LeftHandPinky3Link",
        
        # Right Fingers
        "RightThumbProximal": "RightHandThumb1Link", "RightThumbIntermediate": "RightHandThumb2Link", "RightThumbDistal": "RightHandThumb3Link",
        "RightIndexProximal": "RightHandIndex1Link", "RightIndexIntermediate": "RightHandIndex2Link", "RightIndexDistal": "RightHandIndex3Link",
        "RightMiddleProximal": "RightHandMiddle1Link", "RightMiddleIntermediate": "RightHandMiddle2Link", "RightMiddleDistal": "RightHandMiddle3Link",
        "RightRingProximal": "RightHandRing1Link", "RightRingIntermediate": "RightHandRing2Link", "RightRingDistal": "RightHandRing3Link",
        "RightLittleProximal": "RightHandPinky1Link", "RightLittleIntermediate": "RightHandPinky2Link", "RightLittleDistal": "RightHandPinky3Link"
    }
    
    if "Spine" not in g_vmc.models and "Chest" in g_vmc.models:
        mapping["Chest"] = "SpineLink"
        
    for vmc_name, prop_name in mapping.items():
        if vmc_name in g_vmc.models:
            model = g_vmc.models[vmc_name]
            prop = char.PropertyList.Find(prop_name)
            if prop:
                prop.removeAll()
                try: prop.append(model)
                except: prop.insert(model)
            else:
                base_name = prop_name.replace("Link", "")
                for p in char.PropertyList:
                    if p.Name.endswith("Link") and base_name in p.Name:
                        p.removeAll()
                        try: p.append(model)
                        except: p.insert(model)
                        break
                        
    # --- Force T-Pose (User Verified) ---
    for b_name, m in g_vmc.models.items():
        if b_name == "Root":
            m.SetVector(FBVector3d(0, 180, 0), FBModelTransformationType.kModelRotation, False)
        else:
            m.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
            
    FBSystem().Scene.Evaluate()
                        
    success = char.SetCharacterizeOn(True)
    if success:
        FBMessageBox("Success", "HIK Characterized Successfully!", "OK")
    else:
        err = char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning", "Characterization failed.\nError: " + str(err) + "\nPlease check Python Console.", "OK")

def OnConnectExpressionsClick(control, event):
    if not g_vmc.blend_data_cache:
        FBMessageBox("Warning", "No Expression (Blendshape) data received yet from VMC!\nMake sure your face tracking is sending data.", "OK")
        return
        
    facial_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "VMC_Facial":
            facial_node = m
            break
            
    if not facial_node:
        facial_node = FBModelNull("VMC_Facial")
        facial_node.Show = True
        facial_node.Size = 50.0
        g_vmc.models["Facial"] = facial_node
    else:
        g_vmc.models["Facial"] = facial_node
        
    count = 0
    for b_name in g_vmc.blend_data_cache.keys():
        prop = facial_node.PropertyList.Find(b_name)
        if not prop:
            # Create properties as animatable
            prop = facial_node.PropertyCreate(b_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    g_vmc.blend_props_created = True
    FBMessageBox("Success", "Created {} expression properties on VMC_Facial!".format(count), "OK")

def FindAnimationNode(parent_node, name):
    if not parent_node: return None
    for node in parent_node.Nodes:
        if node.Name == name:
            return node
        found = FindAnimationNode(node, name)
        if found: return found
    return None

def OnConnectToModelClick(control, event):
    facial_node = None
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == "VMC_Facial":
            facial_node = m
            break
            
    if not facial_node:
        FBMessageBox("Warning", "VMC_Facial node not found! Please run 'Generate Expressions' first.", "OK")
        return
        
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select a model with blendshapes first!", "OK")
        return
        
    target_model = models[0]
    
    # Pre-pass: Expose matching blendshape properties on the target model by setting them to animated
    for prop in facial_node.PropertyList:
        if prop.IsUserProperty():
            target_prop = target_model.PropertyList.Find(prop.Name)
            if target_prop:
                try: target_prop.SetAnimated(True)
                except: pass
    
    relation = FBConstraintRelation("VMC_Expression_Link")
    relation.Active = False
    
    src_box = relation.SetAsSource(facial_node)
    trgt_box = relation.ConstrainObject(target_model)
    
    relation.SetBoxPosition(src_box, 100, 100)
    relation.SetBoxPosition(trgt_box, 400, 100)
    
    match_count = 0
    src_out_node = src_box.AnimationNodeOutGet()
    trgt_in_node = trgt_box.AnimationNodeInGet()
    
    if src_out_node and trgt_in_node:
        for prop in facial_node.PropertyList:
            if prop.IsUserProperty():
                prop_name = prop.Name
                out_n = FindAnimationNode(src_out_node, prop_name)
                in_n = FindAnimationNode(trgt_in_node, prop_name)
                
                if out_n and in_n:
                    FBConnect(out_n, in_n)
                    match_count += 1
                    
    relation.Active = True
    
    if match_count == 0:
        FBMessageBox("Message", "No match Facial channel，Please connect manually", "OK")
    else:
        FBMessageBox("Success", "Successfully connected {} expressions!".format(match_count), "OK")

def OnDeleteSkeletonClick(control, event):
    # 1. MUST delete character first, otherwise characterized bones are locked and cannot be deleted!
    char_name = "VMC_HIK_Character"
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name:
            c.SetCharacterizeOn(False)
            try: c.FBDelete()
            except: pass

    # 2. Delete models tracked in our dictionary
    for b_name, m in g_vmc.models.items():
        try: m.FBDelete()
        except: pass
            
    # 3. Clean up any leftovers in the scene starting with "VMC_"
    for m in list(FBSystem().Scene.Components):
        if hasattr(m, "Name") and m.Name and m.Name.startswith("VMC_"):
            if isinstance(m, FBModel):
                try: m.FBDelete()
                except: pass
                
    g_vmc.models.clear()
    g_vmc.bone_data_cache.clear()
    g_vmc.blend_data_cache.clear()
    g_vmc.blend_props_created = False
    
    # Also disconnect network as a hard reset
    if g_vmc.sock:
        try: g_vmc.sock.close()
        except: pass
        g_vmc.sock = None
    g_vmc.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Status: Disconnected / Reset"
    try: FBSystem().OnUIIdle.Remove(OnUIIdle)
    except: pass
    FBMessageBox("Success", "Cleaned up all VMC Skeletons, Characters, and reset Network Port.", "OK")

def OnForceRecordClick(control, event):
    import time
    g_vmc.force_recording = not getattr(g_vmc, 'force_recording', False)
    
    if g_vmc.force_recording:
        control.Caption = "⏹ Stop Recording VMC"
        
        try:
            take_name = "VMC_Take_" + time.strftime("%Y%m%d_%H%M%S")
            new_take = FBTake(take_name)
            if new_take not in FBSystem().Scene.Takes:
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
        
    else:
        control.Caption = "🔴 Record VMC"
        FBPlayerControl().Stop()
        
        try:
            stop_time = FBSystem().LocalTime
            take = FBSystem().CurrentTake
            if take:
                start_time = take.LocalTimeSpan.GetStart()
                take.LocalTimeSpan = FBTimeSpan(start_time, stop_time)
                FBPlayerControl().LoopStop = stop_time
        except Exception as e:
            print("Error setting out point:", e)

def PopulateTool(tool):
    tool.StartSizeX = 350
    tool.StartSizeY = 600
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    
    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])
    
    # IP Address
    g_ui["lyt_ip"] = FBHBoxLayout()
    g_ui["lbl_ip"] = FBLabel()
    g_ui["lbl_ip"].Caption = "Bind IP:"
    g_ui["edit_ip"] = FBEdit()
    g_ui["edit_ip"].Text = "0.0.0.0"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 70)
    g_ui["lyt_ip"].Add(g_ui["edit_ip"], 100)
    
    # Port
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
    
    g_ui["btn_gen_skeleton"] = FBButton()
    g_ui["btn_gen_skeleton"].Caption = "Generate Skeleton"
    g_ui["btn_gen_skeleton"].OnClick.Add(OnGenerateClick)
    
    g_ui["btn_characterize"] = FBButton()
    g_ui["btn_characterize"].Caption = "Characterize Skeleton"
    g_ui["btn_characterize"].OnClick.Add(OnCharacterizeClick)
    
    g_ui["btn_expr"] = FBButton()
    g_ui["btn_expr"].Caption = "Generate Expressions"
    g_ui["btn_expr"].OnClick.Add(OnConnectExpressionsClick)
    
    g_ui["btn_connect_model"] = FBButton()
    g_ui["btn_connect_model"].Caption = "Connect Expression"
    g_ui["btn_connect_model"].OnClick.Add(OnConnectToModelClick)
    
    g_ui["btn_delete"] = FBButton()
    g_ui["btn_delete"].Caption = "Delete Skeleton"
    g_ui["btn_delete"].OnClick.Add(OnDeleteSkeletonClick)
    
    def create_header(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl
        
    g_ui["hdr_connect"] = create_header("CONNECT")
    g_ui["hdr_skeleton"] = create_header("SKELETON")
    g_ui["hdr_facial"] = create_header("FACIAL")
    g_ui["hdr_recording"] = create_header("RECORDING")
    g_ui["hdr_reset"] = create_header("RESET")
    
    g_ui["lbl_status"] = FBLabel()
    g_ui["lbl_status"].Caption = "Status: Disconnected"
    
    g_ui["lyt_record_len"] = FBHBoxLayout()
    g_ui["lbl_record_len"] = FBLabel()
    g_ui["lbl_record_len"].Caption = "Rec length (sec):"
    g_ui["edit_record_len"] = FBEditNumber()
    g_ui["edit_record_len"].Value = 600
    g_ui["edit_record_len"].Precision = 0
    g_ui["lyt_record_len"].Add(g_ui["lbl_record_len"], 120)
    g_ui["lyt_record_len"].Add(g_ui["edit_record_len"], 80)
    
    g_ui["btn_force_record"] = FBButton()
    g_ui["btn_force_record"].Caption = "🔴 Record VMC"
    g_ui["btn_force_record"].OnClick.Add(OnForceRecordClick)
    
    g_ui["main_layout"].Add(g_ui["hdr_connect"], 25)
    g_ui["main_layout"].Add(g_ui["lyt_ip"], 30)
    g_ui["main_layout"].Add(g_ui["lyt_port"], 30)
    g_ui["main_layout"].Add(g_ui["btn_connect"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_skeleton"], 25)
    g_ui["main_layout"].Add(g_ui["btn_gen_skeleton"], 35)
    g_ui["main_layout"].Add(g_ui["btn_characterize"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_facial"], 25)
    g_ui["main_layout"].Add(g_ui["btn_expr"], 35)
    g_ui["main_layout"].Add(g_ui["btn_connect_model"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_recording"], 25)
    g_ui["main_layout"].Add(g_ui["lyt_record_len"], 30)
    g_ui["main_layout"].Add(g_ui["btn_force_record"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_reset"], 25)
    g_ui["main_layout"].Add(g_ui["btn_delete"], 35)
    
    g_ui["main_layout"].Add(g_ui["lbl_status"], 35)

def CreateTool():
    tool_name = "Saint's VMC Receiver"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
