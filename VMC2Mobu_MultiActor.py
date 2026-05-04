"""
VMC2Mobu_MultiActor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Receive VMC (Virtual Motion Capture) bone and blendshape data via OSC/UDP
from senders such as Warudo and drive skeleton models inside MotionBuilder.
Supports up to 3 simultaneous actors, each with an independent namespace
(VMC1/VMC2/VMC3) and UDP port.

Workflow:
  1. Select Actor → Set Bind IP & Port → Connect
  2. Generate Skeleton (requires live VMC bone data)
  3. Characterize Skeleton as HIK
  4. (Optional) Generate Expressions → Connect Expression to selected model
  5. (Optional) Record VMC — creates a new timestamped Take

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import os
import sys
import socket
import struct
import math
from pyfbsdk import *
from pyfbsdk_additions import *

class VMCState:
    def __init__(self, actor_id):
        self.actor_id = actor_id
        self.sock = None
        self.is_connected = False
        self.bone_data_cache = {}
        self.blend_data_cache = {}
        self.blend_props_created = False
        self.models = {}
        self.ip = "0.0.0.0"
        self.port = 39539 + (actor_id - 1)
        self.prop_cache = {}
        self.last_applied_cache = {}
        self.force_recording = False

# Store in sys to persist across script re-runs and prevent port leaks
if hasattr(sys, "vmc_multiactor_states") and sys.vmc_multiactor_states is not None:
    for state in sys.vmc_multiactor_states.values():
        if state.sock:
            try: state.sock.close()
            except: pass
    try: FBSystem().OnUIIdle.Remove(sys.vmc_multiactor_idle_func)
    except: pass
    
sys.vmc_multiactor_states = {1: VMCState(1), 2: VMCState(2), 3: VMCState(3)}
g_vmc_states = sys.vmc_multiactor_states
g_ui = {} # Store UI elements

def current_actor():
    try: return g_ui["list_actor"].ItemIndex + 1
    except: return 1

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

def process_osc_message(state, address, args, is_recording=False):
    if not address or len(args) < 2:
        return
        
    if address == "/VMC/Ext/Bone/Pos" and len(args) >= 8:
        b_name = args[0]
        px, py, pz = args[1], args[2], args[3]
        qx, qy, qz, qw = args[4], args[5], args[6], args[7]
        
        state.bone_data_cache[b_name] = (px, py, pz, qx, qy, qz, qw)
        
        if b_name in state.models:
            m = state.models[b_name]
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
        
        state.bone_data_cache["Root"] = (px, py, pz, qx, qy, qz, qw)
        if "Root" in state.models:
            m = state.models["Root"]
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
        state.blend_data_cache[b_name] = val * 100.0

def OnUIIdle(control, event):
    # Check if MotionBuilder is currently recording and playing
    is_recording = False
    try:
        player = FBPlayerControl()
        global_force_recording = any(getattr(state, 'force_recording', False) for state in g_vmc_states.values())
        is_recording = player.IsPlaying and (player.IsRecording or global_force_recording)
    except:
        pass

    for state in g_vmc_states.values():
        if not state.is_connected or not state.sock:
            continue
            
        packets_processed = 0
        while packets_processed < 100:
            try:
                data, addr = state.sock.recvfrom(65536)
                address, args = parse_osc(data)
                
                if data.startswith(b'#bundle'):
                    offset = 16
                    while offset < len(data):
                        size = struct.unpack('>i', data[offset:offset+4])[0]
                        offset += 4
                        msg_data = data[offset:offset+size]
                        msg_address, msg_args = parse_osc(msg_data)
                        process_osc_message(state, msg_address, msg_args, is_recording)
                        offset += size
                else:
                    process_osc_message(state, address, args, is_recording)
                    
                packets_processed += 1
                
            except BlockingIOError:
                break
            except socket.error as e:
                if e.errno == 10035: break
                break
            except Exception as e:
                break
                
        # Real-time update for blendshapes
        if state.blend_props_created and "Facial" in state.models:
            facial_node = state.models["Facial"]
            try:
                for b_name, val in state.blend_data_cache.items():
                    prop = state.prop_cache.get(b_name)
                    if not prop:
                        prop = facial_node.PropertyList.Find(b_name)
                        if prop:
                            state.prop_cache[b_name] = prop
                    if prop:
                        # Only update if value changed significantly
                        last_val = state.last_applied_cache.get(b_name)
                        value_changed = last_val is None or abs(last_val - val) > 0.001
                        if value_changed:
                            prop.Data = float(val)
                            state.last_applied_cache[b_name] = val
                            
                            # If recording, key the property
                            if is_recording:
                                try:
                                    prop.Key()
                                except:
                                    pass
            except:
                pass
                
    # Update UI for current actor
    act_id = current_actor()
    act_state = g_vmc_states[act_id]
    if act_state.is_connected:
        if len(act_state.bone_data_cache) > 0:
            g_ui["lbl_status"].Caption = "Actor {} Receiving Data (Bones: {}, Expr: {})".format(act_id, len(act_state.bone_data_cache), len(act_state.blend_data_cache))
        else:
            g_ui["lbl_status"].Caption = "Actor {} Connected (Port: {}), but no bones parsed yet.".format(act_id, act_state.port)
    else:
        g_ui["lbl_status"].Caption = "Actor {} Disconnected".format(act_id)

def OnActorChange(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    g_ui["edit_ip"].Text = state.ip
    g_ui["edit_port"].Value = state.port
    g_ui["btn_connect"].Caption = "Disconnect" if state.is_connected else "Connect"
    if state.is_connected:
        g_ui["lbl_status"].Caption = "Actor {} Connected (Port: {})".format(act_id, state.port)
    else:
        g_ui["lbl_status"].Caption = "Actor {} Disconnected".format(act_id)

def OnConnectClick(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    
    if not state.is_connected:
        try:
            state.ip = g_ui["edit_ip"].Text
            state.port = int(g_ui["edit_port"].Text.strip())
            state.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            state.sock.bind((state.ip, state.port))
            state.sock.setblocking(False)
            state.is_connected = True
            
            g_ui["btn_connect"].Caption = "Disconnect"
            g_ui["lbl_status"].Caption = "Actor {} Connected (Port: {})".format(act_id, state.port)
            print("Actor {} Receiver started on port {}".format(act_id, state.port))
            
            sys = FBSystem()
            sys.OnUIIdle.Remove(OnUIIdle)
            sys.OnUIIdle.Add(OnUIIdle)
            import sys as python_sys
            python_sys.vmc_multiactor_idle_func = OnUIIdle
            
        except Exception as e:
            g_ui["lbl_status"].Caption = "Actor {} Error binding port!".format(act_id)
            print("Failed to bind socket for Actor {}: {}".format(act_id, e))
    else:
        if state.sock:
            state.sock.close()
            state.sock = None
        state.is_connected = False
        g_ui["btn_connect"].Caption = "Connect"
        g_ui["lbl_status"].Caption = "Actor {} Disconnected".format(act_id)
        print("Actor {} Receiver stopped.".format(act_id))

def OnGenerateClick(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    
    if not state.bone_data_cache:
        FBMessageBox("Warning", "No VMC data received yet for Actor {}!".format(act_id), "OK")
        return
        
    print("Generating Skeleton for Actor {}...".format(act_id))
    prefix = "VMC{}:".format(act_id)
    
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
    
    for b_name in state.bone_data_cache.keys():
        if b_name == "Root": continue
        if b_name not in state.models:
            m = FBModelSkeleton(prefix + "VMC_" + b_name)
            m.Show = True
            m.Size = 10.0
            m.Translation.SetAnimated(True)
            m.Rotation.SetAnimated(True)
            state.models[b_name] = m
            
    for b_name, m in state.models.items():
        parent_name = unity_hierarchy.get(b_name)
        
        # Robust fallback: keep looking up the hierarchy chain until we find a parent that actually exists in the incoming data
        while parent_name and parent_name not in state.models:
            parent_name = unity_hierarchy.get(parent_name)
            
        if parent_name and parent_name in state.models:
            m.Parent = state.models[parent_name]
            
    if "Ref" not in state.models:
        m_ref = FBModelNull(prefix + "VMC_Ref")
        m_ref.Show = True
        m_ref.Size = 80.0
        m_ref.Translation.SetAnimated(True)
        m_ref.Rotation.SetAnimated(True)
        state.models["Ref"] = m_ref

    if "Root" not in state.models:
        m = FBModelNull(prefix + "VMC_Root")
        m.Show = True
        m.Size = 50.0
        m.Translation.SetAnimated(True)
        m.Rotation.SetAnimated(True)
        state.models["Root"] = m
        if "Hips" in state.models:
            state.models["Hips"].Parent = m
        
        m.Parent = state.models["Ref"]
            
    FBSystem().Scene.Evaluate()
    FBMessageBox("Success", "Skeleton Generated for Actor {}!".format(act_id), "OK")

def OnCharacterizeClick(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    
    if not state.models:
        FBMessageBox("Warning", "Please generate skeleton for Actor {} first!".format(act_id), "OK")
        return
        
    print("Auto-Characterizing Actor {}...".format(act_id))
    prefix = "VMC{}:".format(act_id)
    char_name = prefix + "VMC_HIK_Character"
    char = None
    for c in FBSystem().Scene.Characters:
        if c.Name == char_name or (hasattr(c, "LongName") and c.LongName == char_name):
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
    
    if "Spine" not in state.models and "Chest" in state.models:
        mapping["Chest"] = "SpineLink"
        
    for vmc_name, prop_name in mapping.items():
        if vmc_name in state.models:
            model = state.models[vmc_name]
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
    for b_name, m in state.models.items():
        if b_name == "Root":
            m.SetVector(FBVector3d(0, 180, 0), FBModelTransformationType.kModelRotation, False)
        else:
            m.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
            
    FBSystem().Scene.Evaluate()
                        
    success = char.SetCharacterizeOn(True)
    if success:
        FBMessageBox("Success", "Actor {} HIK Characterized!".format(act_id), "OK")
    else:
        err = char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning", "Characterization failed.\nError: " + str(err) + "\nPlease check Python Console.", "OK")

def OnConnectExpressionsClick(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    
    if not state.blend_data_cache:
        FBMessageBox("Warning", "No Expression data for Actor {}!".format(act_id), "OK")
        return
        
    prefix = "VMC{}:".format(act_id)
    facial_name = prefix + "VMC_Facial"
    facial_node = None
    
    for m in FBSystem().Scene.RootModel.Children:
        if m.Name == facial_name or (hasattr(m, "LongName") and m.LongName == facial_name):
            facial_node = m
            break
            
    if not facial_node:
        facial_node = FBModelNull(facial_name)
        facial_node.Show = True
        facial_node.Size = 50.0
        state.models["Facial"] = facial_node
    else:
        state.models["Facial"] = facial_node
        
    count = 0
    for b_name in state.blend_data_cache.keys():
        prop = facial_node.PropertyList.Find(b_name)
        if not prop:
            prop = facial_node.PropertyCreate(b_name, FBPropertyType.kFBPT_double, "Number", True, True, None)
            if prop:
                prop.SetAnimated(True)
                count += 1
                
    state.blend_props_created = True
    FBMessageBox("Success", "Created {} expression properties for Actor {}!".format(count, act_id), "OK")

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
    state = g_vmc_states[act_id]
    
    if "Facial" not in state.models:
        FBMessageBox("Warning", "Actor {} Facial node not found! Generate Expressions first.".format(act_id), "OK")
        return
        
    facial_node = state.models["Facial"]
    
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Warning", "Please select a model with blendshapes first!", "OK")
        return
        
    target_model = models[0]
    
    for prop in facial_node.PropertyList:
        if prop.IsUserProperty():
            target_prop = target_model.PropertyList.Find(prop.Name)
            if target_prop:
                try: target_prop.SetAnimated(True)
                except: pass
    
    relation = FBConstraintRelation("VMC{}_Expression_Link".format(act_id))
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
    
    FBMessageBox("Success", "Connected {} expressions for Actor {}!".format(match_count, act_id), "OK")

def OnDeleteSkeletonClick(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    prefix = "VMC{}:".format(act_id)
    
    char_name = prefix + "VMC_HIK_Character"
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name or (hasattr(c, "LongName") and c.LongName == char_name):
            c.SetCharacterizeOn(False)
            try: c.FBDelete()
            except: pass

    for b_name, m in state.models.items():
        try: m.FBDelete()
        except: pass
            
    for m in list(FBSystem().Scene.Components):
        if hasattr(m, "Name") and m.Name:
            if m.Name.startswith(prefix) or (hasattr(m, "LongName") and m.LongName and m.LongName.startswith(prefix)):
                if isinstance(m, FBModel):
                    try: m.FBDelete()
                    except: pass
                    
    state.models.clear()
    state.bone_data_cache.clear()
    state.blend_data_cache.clear()
    state.blend_props_created = False
    
    if state.sock:
        try: state.sock.close()
        except: pass
        state.sock = None
    state.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Actor {} Disconnected / Reset".format(act_id)
    FBMessageBox("Success", "Cleaned up Actor {} Skeletons, Characters, and Network.".format(act_id), "OK")

def OnForceRecordClick(control, event):
    import time
    act_id = current_actor()
    state = g_vmc_states[act_id]
    state.force_recording = not getattr(state, 'force_recording', False)
    
    if state.force_recording:
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

def OnRotateRef90Click(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    if "Ref" in state.models:
        m_ref = state.models["Ref"]
        rot = FBVector3d()
        m_ref.GetVector(rot, FBModelTransformationType.kModelRotation, False)
        m_ref.SetVector(FBVector3d(rot[0], rot[1] + 90.0, rot[2]), FBModelTransformationType.kModelRotation, False)
        FBSystem().Scene.Evaluate()

def OnRotateRef180Click(control, event):
    act_id = current_actor()
    state = g_vmc_states[act_id]
    if "Ref" in state.models:
        m_ref = state.models["Ref"]
        rot = FBVector3d()
        m_ref.GetVector(rot, FBModelTransformationType.kModelRotation, False)
        m_ref.SetVector(FBVector3d(rot[0], rot[1] + 180.0, rot[2]), FBModelTransformationType.kModelRotation, False)
        FBSystem().Scene.Evaluate()

def PopulateTool(tool):
    tool.StartSizeX = 240
    tool.StartSizeY = 680
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    
    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])
    
    # Actor Selector
    g_ui["lyt_actor"] = FBHBoxLayout()
    g_ui["lbl_actor"] = FBLabel()
    g_ui["lbl_actor"].Caption = "Select Actor:"
    g_ui["list_actor"] = FBList()
    g_ui["list_actor"].Items.append("VMC1 (Actor 1)")
    g_ui["list_actor"].Items.append("VMC2 (Actor 2)")
    g_ui["list_actor"].Items.append("VMC3 (Actor 3)")
    g_ui["list_actor"].ItemIndex = 0
    g_ui["list_actor"].OnChange.Add(OnActorChange)
    g_ui["lyt_actor"].Add(g_ui["lbl_actor"], 75)
    g_ui["lyt_actor"].Add(g_ui["list_actor"], 140)
    
    # IP Address
    g_ui["lyt_ip"] = FBHBoxLayout()
    g_ui["lbl_ip"] = FBLabel()
    g_ui["lbl_ip"].Caption = "Bind IP:"
    g_ui["edit_ip"] = FBEdit()
    g_ui["edit_ip"].Text = "0.0.0.0"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 65)
    g_ui["lyt_ip"].Add(g_ui["edit_ip"], 140)
    
    # Port
    g_ui["lyt_port"] = FBHBoxLayout()
    g_ui["lbl_port"] = FBLabel()
    g_ui["lbl_port"].Caption = "UDP Port:"
    
    g_ui["edit_port"] = FBEdit()
    g_ui["edit_port"].Text = "39539"
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 65)
    g_ui["lyt_port"].Add(g_ui["edit_port"], 140)
    
    g_ui["btn_connect"] = FBButton()
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    
    g_ui["btn_gen_skeleton"] = FBButton()
    g_ui["btn_gen_skeleton"].Caption = "Generate Skeleton"
    g_ui["btn_gen_skeleton"].OnClick.Add(OnGenerateClick)
    
    g_ui["btn_characterize"] = FBButton()
    g_ui["btn_characterize"].Caption = "Characterize Skeleton"
    g_ui["btn_characterize"].OnClick.Add(OnCharacterizeClick)
    
    g_ui["lyt_rot"] = FBHBoxLayout()
    g_ui["btn_rot_90"] = FBButton()
    g_ui["btn_rot_90"].Caption = "Rot Ref Y +90"
    g_ui["btn_rot_90"].OnClick.Add(OnRotateRef90Click)
    
    g_ui["btn_rot_180"] = FBButton()
    g_ui["btn_rot_180"].Caption = "Rot Ref Y +180"
    g_ui["btn_rot_180"].OnClick.Add(OnRotateRef180Click)
    
    g_ui["lyt_rot"].Add(g_ui["btn_rot_90"], 105)
    g_ui["lyt_rot"].Add(g_ui["btn_rot_180"], 105)
    
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
    g_ui["lyt_record_len"].Add(g_ui["lbl_record_len"], 110)
    g_ui["lyt_record_len"].Add(g_ui["edit_record_len"], 90)
    
    g_ui["btn_force_record"] = FBButton()
    g_ui["btn_force_record"].Caption = "🔴 Record VMC"
    g_ui["btn_force_record"].OnClick.Add(OnForceRecordClick)
    
    g_ui["main_layout"].Add(g_ui["lyt_actor"], 30)
    g_ui["main_layout"].Add(g_ui["hdr_connect"], 25)
    g_ui["main_layout"].Add(g_ui["lyt_ip"], 30)
    g_ui["main_layout"].Add(g_ui["lyt_port"], 30)
    g_ui["main_layout"].Add(g_ui["btn_connect"], 35)
    
    g_ui["main_layout"].Add(g_ui["hdr_skeleton"], 25)
    g_ui["main_layout"].Add(g_ui["btn_gen_skeleton"], 35)
    g_ui["main_layout"].Add(g_ui["btn_characterize"], 35)
    g_ui["main_layout"].Add(g_ui["lyt_rot"], 35)
    
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
