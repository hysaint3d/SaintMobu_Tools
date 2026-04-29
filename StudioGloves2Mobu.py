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
        self.models = {}

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

def process_osc_message(address, args):
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
            except:
                pass
            
            
def OnUIIdle(control, event):
    if not g_vmc.is_connected or not g_vmc.sock:
        return
        
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
        try:
            if len(g_vmc.bone_data_cache) > 0:
                g_ui["lbl_status"].Caption = "Receiving Data (Bones: {})".format(len(g_vmc.bone_data_cache))
            else:
                g_ui["lbl_status"].Caption = "Got RAW data ({} bytes), but no bones parsed yet.".format(last_packet_size)
        except Exception:
            try:
                FBSystem().OnUIIdle.Remove(OnUIIdle)
            except: pass

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
        "RightIndexProximal": "RightHand", "RightIndexIntermediate": "RightIndexProximal", "RightIndexDistal": "RightHandIndexIntermediate", # Fixed hierarchy
        "RightMiddleProximal": "RightHand", "RightMiddleIntermediate": "RightMiddleProximal", "RightMiddleDistal": "RightMiddleIntermediate",
        "RightRingProximal": "RightHand", "RightRingIntermediate": "RightRingProximal", "RightRingDistal": "RightRingIntermediate",
        "RightLittleProximal": "RightHand", "RightLittleIntermediate": "RightLittleProximal", "RightLittleDistal": "RightLittleIntermediate"
    }
    
    # Correct mapping for some finger distal points
    unity_hierarchy["RightIndexDistal"] = "RightIndexIntermediate"
    
    for b_name in g_vmc.bone_data_cache.keys():
        if b_name == "Root": continue
        if b_name not in g_vmc.models:
            m = FBModelSkeleton("VMC_" + b_name)
            m.Show = True
            m.Size = 10.0
            g_vmc.models[b_name] = m
            
    for b_name, m in g_vmc.models.items():
        parent_name = unity_hierarchy.get(b_name)
        if parent_name == "UpperChest" and "UpperChest" not in g_vmc.models:
            parent_name = "Chest"
        elif parent_name == "Chest" and "Chest" not in g_vmc.models:
            parent_name = "Spine"
            
        if parent_name and parent_name in g_vmc.models:
            m.Parent = g_vmc.models[parent_name]
            
    # --- StudioGloves Specialized Roots ---
    if "VMC_LeftHandRoot" not in g_vmc.models:
        m_l = FBModelNull("VMC_LeftHandRoot")
        m_l.Show = True
        m_l.Size = 50.0
        g_vmc.models["VMC_LeftHandRoot"] = m_l
        m_l.SetVector(FBVector3d(0, 180, 0), FBModelTransformationType.kModelRotation, False)
            
    if "VMC_RightHandRoot" not in g_vmc.models:
        m_r = FBModelNull("VMC_RightHandRoot")
        m_r.Show = True
        m_r.Size = 50.0
        g_vmc.models["VMC_RightHandRoot"] = m_r
        m_r.SetVector(FBVector3d(0, 180, 0), FBModelTransformationType.kModelRotation, False)

    # Parent Hand bones to Roots
    if "LeftHand" in g_vmc.models and "VMC_LeftHandRoot" in g_vmc.models:
        g_vmc.models["LeftHand"].Parent = g_vmc.models["VMC_LeftHandRoot"]
    if "RightHand" in g_vmc.models and "VMC_RightHandRoot" in g_vmc.models:
        g_vmc.models["RightHand"].Parent = g_vmc.models["VMC_RightHandRoot"]

    # Parent Fingers directly to Hand Roots
    finger_proximals_l = ["LeftThumbProximal", "LeftIndexProximal", "LeftMiddleProximal", "LeftRingProximal", "LeftLittleProximal"]
    finger_proximals_r = ["RightThumbProximal", "RightIndexProximal", "RightMiddleProximal", "RightRingProximal", "RightLittleProximal"]
    
    for f in finger_proximals_l:
        if f in g_vmc.models and "VMC_LeftHandRoot" in g_vmc.models:
            g_vmc.models[f].Parent = g_vmc.models["VMC_LeftHandRoot"]
            
    for f in finger_proximals_r:
        if f in g_vmc.models and "VMC_RightHandRoot" in g_vmc.models:
            g_vmc.models[f].Parent = g_vmc.models["VMC_RightHandRoot"]
            
    FBSystem().Scene.Evaluate()
    FBMessageBox("Success", "StudioGloves Skeleton Generated Successfully!", "OK")

def OnZeroRotationClick(control, event):
    if g_vmc.is_connected:
        OnConnectClick(None, None)
    if not g_vmc.models:
        FBMessageBox("Warning", "No skeleton models found to reset.", "OK")
        return
    for b_name, m in g_vmc.models.items():
        try:
            if b_name in ["VMC_LeftHandRoot", "VMC_RightHandRoot"]:
                m.SetVector(FBVector3d(0, 180, 0), FBModelTransformationType.kModelRotation, False)
            else:
                m.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelRotation, False)
        except: pass
    FBSystem().Scene.Evaluate()
    FBMessageBox("Success", "All rotations have been reset to zero.", "OK")

def OnConnectToSkeletonClick(control, event):
    sel_list = FBModelList()
    FBGetSelectedModels(sel_list)
    if len(sel_list) == 0:
        FBMessageBox("Warning", "Please select the target character or hand bones first!", "OK")
        return
    target_l = None
    target_r = None
    char = FBApplication().CurrentCharacter
    if char:
        prop_l = char.PropertyList.Find("LeftHandLink")
        prop_r = char.PropertyList.Find("RightHandLink")
        if prop_l:
            for m in prop_l: target_l = m; break
        if prop_r:
            for m in prop_r: target_r = m; break

    if not target_l or not target_r:
        search_names_l = ["LeftHand", "LHand", "L_Hand", "Hand_L", "Left_Hand"]
        search_names_r = ["RightHand", "RHand", "R_Hand", "Hand_R", "Right_Hand"]
        all_models = sel_list if len(sel_list) > 0 else FBSystem().Scene.Components
        for m in all_models:
            if not isinstance(m, FBModel): continue
            m_name = m.Name.lower()
            if not target_l:
                if any(n.lower() == m_name for n in search_names_l) or \
                   (any(n.lower() in m_name for n in search_names_l) and ("left" in m_name or "_l" in m_name)):
                    target_l = m
            if not target_r:
                if any(n.lower() == m_name for n in search_names_r) or \
                   (any(n.lower() in m_name for n in search_names_r) and ("right" in m_name or "_r" in m_name)):
                    target_r = m

    if not target_l or not target_r:
        FBMessageBox("Warning", "Could not identify LeftHand and RightHand bones.", "OK")
        return

    count = 0
    if "VMC_LeftHandRoot" in g_vmc.models:
        root_l = g_vmc.models["VMC_LeftHandRoot"]
        root_l.Parent = target_l
        root_l.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelTranslation, False)
        count += 1
    if "VMC_RightHandRoot" in g_vmc.models:
        root_r = g_vmc.models["VMC_RightHandRoot"]
        root_r.Parent = target_r
        root_r.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelTranslation, False)
        count += 1

    FBSystem().Scene.Evaluate()
    if count > 0:
        FBMessageBox("Success", "Successfully parented VMC Hand Roots to target bones!", "OK")
    else:
        FBMessageBox("Warning", "Hand Roots not found. Did you click 'Generate Skeleton'?", "OK")

def OnCharacterizeClick(control, event):
    """Clone selected character, replace finger definition with VMC fingers."""
    if not g_vmc.models:
        FBMessageBox("Warning", "Please click 'Generate Skeleton' first!", "OK")
        return
    
    src_char = FBApplication().CurrentCharacter
    if not src_char:
        FBMessageBox("Warning", "Please select a target character in Character Controls first.", "OK")
        return
    
    print(">>> Cloning character: " + src_char.Name)
    new_char_name = src_char.Name + "_VMC"
    
    # Delete old combined character if exists
    for c in list(FBSystem().Scene.Characters):
        if c.Name == new_char_name:
            c.SetCharacterizeOn(False)
            try: c.FBDelete()
            except: pass
            break
    
    # === Clone the source character (preserves all body bone links) ===
    new_char = src_char.Clone()
    new_char.Name = new_char_name
    
    # === Set as current and unlock ===
    FBApplication().CurrentCharacter = new_char
    new_char.SetCharacterizeOn(False)
    
    # Temporarily unlock other chars for HIK finger init
    other_chars = []
    for c in FBSystem().Scene.Characters:
        if c != new_char:
            prop_c = c.PropertyList.Find("Characterize")
            if prop_c and prop_c.Data:
                c.SetCharacterizeOn(False)
                other_chars.append(c)
    
    # === Clear existing finger slots and initialize fresh finger properties ===
    for side in ["Left", "Right"]:
        p_cnt = new_char.PropertyList.Find(side + "HandFingerCount")
        if p_cnt: p_cnt.Data = 5
        for f in ["Thumb", "Index", "Middle", "Ring", "Pinky"]:
            p_act = new_char.PropertyList.Find(side + "Hand" + f + "Active")
            if p_act: p_act.Data = True
    FBSystem().Scene.Evaluate()
    
    # === Map VMC fingers NOW (while other chars are still unlocked) ===
    finger_mapping = {
        "LeftThumbProximal": "LeftHandThumb1Link", "LeftThumbIntermediate": "LeftHandThumb2Link", "LeftThumbDistal": "LeftHandThumb3Link",
        "LeftIndexProximal": "LeftHandIndex1Link", "LeftIndexIntermediate": "LeftHandIndex2Link", "LeftIndexDistal": "LeftHandIndex3Link",
        "LeftMiddleProximal": "LeftHandMiddle1Link", "LeftMiddleIntermediate": "LeftHandMiddle2Link", "LeftMiddleDistal": "LeftHandMiddle3Link",
        "LeftRingProximal": "LeftHandRing1Link", "LeftRingIntermediate": "LeftHandRing2Link", "LeftRingDistal": "LeftHandRing3Link",
        "LeftLittleProximal": "LeftHandPinky1Link", "LeftLittleIntermediate": "LeftHandPinky2Link", "LeftLittleDistal": "LeftHandPinky3Link",
        "RightThumbProximal": "RightHandThumb1Link", "RightThumbIntermediate": "RightHandThumb2Link", "RightThumbDistal": "RightHandThumb3Link",
        "RightIndexProximal": "RightHandIndex1Link", "RightIndexIntermediate": "RightHandIndex2Link", "RightIndexDistal": "RightHandIndex3Link",
        "RightMiddleProximal": "RightHandMiddle1Link", "RightMiddleIntermediate": "RightHandMiddle2Link", "RightMiddleDistal": "RightHandMiddle3Link",
        "RightRingProximal": "RightHandRing1Link", "RightRingIntermediate": "RightHandRing2Link", "RightRingDistal": "RightHandRing3Link",
        "RightLittleProximal": "RightHandPinky1Link", "RightLittleIntermediate": "RightHandPinky2Link", "RightLittleDistal": "RightHandPinky3Link"
    }
    
    finger_count = 0
    for vmc_name, prop_name in finger_mapping.items():
        prop = new_char.PropertyList.Find(prop_name)
        if prop:
            prop.removeAll()
        if vmc_name in g_vmc.models:
            model = g_vmc.models[vmc_name]
            if prop:
                try: prop.append(model)
                except: prop.insert(model)
                finger_count += 1
    
    print(">>> Mapped {} fingers".format(finger_count))
    
    # Restore other chars AFTER finger mapping is done
    for c in other_chars:
        try: c.SetCharacterizeOn(True)
        except: pass
    FBApplication().CurrentCharacter = new_char
    FBSystem().Scene.Evaluate()
    
    success = new_char.SetCharacterizeOn(True)
    if success:
        FBMessageBox("Success", "Created '{}'\nFingers: {}".format(new_char_name, finger_count), "OK")
    else:
        err = new_char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning", "Characterize failed.\nFingers: {}\nError: {}".format(finger_count, err), "OK")

def OnAddFingersToCurrentCharClick(control, event):
    """Add VMC finger mapping to the currently selected character in Character Controls."""
    if not g_vmc.models:
        FBMessageBox("Warning", "Please click 'Generate Skeleton' first!", "OK")
        return
    
    target_char = FBApplication().CurrentCharacter
    if not target_char:
        FBMessageBox("Warning", "Please select a character in Character Controls first.", "OK")
        return
    
    print(">>> Adding fingers to: " + target_char.Name)
    
    # Temporarily unlock ALL other characters to release HIK global lock
    other_chars = []
    for c in FBSystem().Scene.Characters:
        if c != target_char:
            prop_c = c.PropertyList.Find("Characterize")
            was_on = prop_c.Data if prop_c else False
            if was_on:
                c.SetCharacterizeOn(False)
                other_chars.append(c)
    
    # Unlock target and force it as CurrentCharacter
    target_char.SetCharacterizeOn(False)
    FBApplication().CurrentCharacter = target_char
    
    # Enable finger properties on THIS character
    for side in ["Left", "Right"]:
        p_cnt = target_char.PropertyList.Find(side + "HandFingerCount")
        if p_cnt: p_cnt.Data = 5
        for f in ["Thumb", "Index", "Middle", "Ring", "Pinky"]:
            p_act = target_char.PropertyList.Find(side + "Hand" + f + "Active")
            if p_act: p_act.Data = True
    FBSystem().Scene.Evaluate()
    FBApplication().CurrentCharacter = target_char
    
    # Map VMC fingers
    finger_mapping = {
        "LeftThumbProximal": "LeftHandThumb1Link", "LeftThumbIntermediate": "LeftHandThumb2Link", "LeftThumbDistal": "LeftHandThumb3Link",
        "LeftIndexProximal": "LeftHandIndex1Link", "LeftIndexIntermediate": "LeftHandIndex2Link", "LeftIndexDistal": "LeftHandIndex3Link",
        "LeftMiddleProximal": "LeftHandMiddle1Link", "LeftMiddleIntermediate": "LeftHandMiddle2Link", "LeftMiddleDistal": "LeftHandMiddle3Link",
        "LeftRingProximal": "LeftHandRing1Link", "LeftRingIntermediate": "LeftHandRing2Link", "LeftRingDistal": "LeftHandRing3Link",
        "LeftLittleProximal": "LeftHandPinky1Link", "LeftLittleIntermediate": "LeftHandPinky2Link", "LeftLittleDistal": "LeftHandPinky3Link",
        "RightThumbProximal": "RightHandThumb1Link", "RightThumbIntermediate": "RightHandThumb2Link", "RightThumbDistal": "RightHandThumb3Link",
        "RightIndexProximal": "RightHandIndex1Link", "RightIndexIntermediate": "RightHandIndex2Link", "RightIndexDistal": "RightHandIndex3Link",
        "RightMiddleProximal": "RightHandMiddle1Link", "RightMiddleIntermediate": "RightHandMiddle2Link", "RightMiddleDistal": "RightHandMiddle3Link",
        "RightRingProximal": "RightHandRing1Link", "RightRingIntermediate": "RightHandRing2Link", "RightRingDistal": "RightHandRing3Link",
        "RightLittleProximal": "RightHandPinky1Link", "RightLittleIntermediate": "RightHandPinky2Link", "RightLittleDistal": "RightHandPinky3Link"
    }
    
    finger_count = 0
    for vmc_name, prop_name in finger_mapping.items():
        if vmc_name in g_vmc.models:
            model = g_vmc.models[vmc_name]
            prop = target_char.PropertyList.Find(prop_name)
            if prop:
                prop.removeAll()
                try: prop.append(model)
                except: prop.insert(model)
                finger_count += 1
            else:
                base_name = prop_name.replace("Link", "")
                for p in target_char.PropertyList:
                    if p.Name.endswith("Link") and base_name in p.Name:
                        p.removeAll()
                        try: p.append(model)
                        except: p.insert(model)
                        finger_count += 1
                        break
    
    # Restore other characters
    for c in other_chars:
        try: c.SetCharacterizeOn(True)
        except: pass
    FBApplication().CurrentCharacter = target_char
    
    # Re-characterize
    success = target_char.SetCharacterizeOn(True)
    if success:
        FBMessageBox("Success", "Added {} fingers to '{}'!".format(finger_count, target_char.Name), "OK")
    else:
        err = target_char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning", "Fingers mapped ({}) but Characterize failed.\nError: {}".format(finger_count, err), "OK")

def OnDeleteSkeletonClick(control, event):
    char_name = "VMC_HIK_Character"
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name or "_VMC_Hands" in c.Name:
            c.SetCharacterizeOn(False)
            try: c.FBDelete()
            except: pass
    for b_name, m in g_vmc.models.items():
        try: m.FBDelete()
        except: pass
    for m in list(FBSystem().Scene.Components):
        try:
            if hasattr(m, "Name") and m.Name and m.Name.startswith("VMC_"):
                if isinstance(m, FBModel):
                    try: m.FBDelete()
                    except: pass
        except: continue
    g_vmc.models.clear()
    g_vmc.bone_data_cache.clear()
    if g_vmc.sock:
        try: g_vmc.sock.close()
        except: pass
        g_vmc.sock = None
    g_vmc.is_connected = False
    g_ui["btn_connect"].Caption = "Connect"
    g_ui["lbl_status"].Caption = "Status: Disconnected / Reset"
    try: FBSystem().OnUIIdle.Remove(sys.vmc_state_idle_func)
    except: pass
    FBMessageBox("Success", "Cleaned up all VMC Skeletons and Characters.", "OK")

def PopulateTool(tool):
    tool.StartSizeX = 350
    tool.StartSizeY = 550
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])
    g_ui["lyt_ip"] = FBHBoxLayout(); g_ui["lbl_ip"] = FBLabel(); g_ui["lbl_ip"].Caption = "Bind IP:"; g_ui["edit_ip"] = FBEdit(); g_ui["edit_ip"].Text = "0.0.0.0"; g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 70); g_ui["lyt_ip"].Add(g_ui["edit_ip"], 100)
    g_ui["lyt_port"] = FBHBoxLayout(); g_ui["lbl_port"] = FBLabel(); g_ui["lbl_port"].Caption = "UDP Port:"; g_ui["edit_port"] = FBEditNumber(); g_ui["edit_port"].Value = 39539; g_ui["edit_port"].Precision = 0; g_ui["lyt_port"].Add(g_ui["lbl_port"], 70); g_ui["lyt_port"].Add(g_ui["edit_port"], 100)
    g_ui["btn_connect"] = FBButton(); g_ui["btn_connect"].Caption = "Connect"; g_ui["btn_connect"].OnClick.Add(OnConnectClick)
    g_ui["btn_gen_skeleton"] = FBButton(); g_ui["btn_gen_skeleton"].Caption = "Generate Skeleton"; g_ui["btn_gen_skeleton"].OnClick.Add(OnGenerateClick)
    g_ui["btn_zero"] = FBButton(); g_ui["btn_zero"].Caption = "Zero Rotation (歸零)"; g_ui["btn_zero"].OnClick.Add(OnZeroRotationClick)
    g_ui["btn_connect_skel"] = FBButton(); g_ui["btn_connect_skel"].Caption = "Connect to Target (連接目標)"; g_ui["btn_connect_skel"].OnClick.Add(OnConnectToSkeletonClick)
    g_ui["btn_add_definition"] = FBButton(); g_ui["btn_add_definition"].Caption = "Characterize (角色化)"; g_ui["btn_add_definition"].OnClick.Add(OnCharacterizeClick)
    g_ui["btn_add_fingers"] = FBButton(); g_ui["btn_add_fingers"].Caption = "Add Fingers to Target (加入手指到選定角色)"; g_ui["btn_add_fingers"].OnClick.Add(OnAddFingersToCurrentCharClick)
    g_ui["btn_delete"] = FBButton(); g_ui["btn_delete"].Caption = "Delete Skeleton"; g_ui["btn_delete"].OnClick.Add(OnDeleteSkeletonClick)
    def create_header(text):
        lbl = FBLabel(); lbl.Caption = "--- " + text + " ---"; lbl.Justify = FBTextJustify.kFBTextJustifyCenter; return lbl
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Status: Disconnected"
    g_ui["main_layout"].Add(create_header("CONNECT"), 25); g_ui["main_layout"].Add(g_ui["lyt_ip"], 30); g_ui["main_layout"].Add(g_ui["lyt_port"], 30); g_ui["main_layout"].Add(g_ui["btn_connect"], 35)
    g_ui["main_layout"].Add(create_header("SKELETON"), 25); g_ui["main_layout"].Add(g_ui["btn_gen_skeleton"], 35); g_ui["main_layout"].Add(g_ui["btn_zero"], 35); g_ui["main_layout"].Add(g_ui["btn_connect_skel"], 35); g_ui["main_layout"].Add(g_ui["btn_add_definition"], 35); g_ui["main_layout"].Add(g_ui["btn_add_fingers"], 35)
    g_ui["main_layout"].Add(create_header("RESET"), 25); g_ui["main_layout"].Add(g_ui["btn_delete"], 35); g_ui["main_layout"].Add(g_ui["lbl_status"], 35)

def CreateTool():
    tool_name = "Saint's StudioGloves VMC"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
    else:
        print("Error creating tool")

CreateTool()
