import sys
import socket
import struct
import math
import time
from pyfbsdk import *
from pyfbsdk_additions import *

# ── Global State ──────────────────────────────────────────────────────────────
class Mobu2VMCState:
    def __init__(self):
        self.sock           = None
        self.is_sending     = False
        self.target_ip      = "127.0.0.1"
        self.target_port    = 39539
        self.bone_cache     = {}
        self.root_cache     = None
        self.frame_count    = 0
        self.fps_limit      = 30
        self.last_send_time = 0.0
        self.hip_scale_x    = 1.0   # Scale factor for Hips local X (sideways)
        self.hip_scale_z    = 1.0   # Scale factor for Hips local Z (forward)
        self.vmc2mobu_mode  = False  # True = VMC2Mobu skeleton (Root Y~180)

if hasattr(sys, "mobu2vmc_state") and sys.mobu2vmc_state is not None:
    try: FBSystem().OnUIIdle.Remove(sys.mobu2vmc_idle_func)
    except: pass
    if sys.mobu2vmc_state.sock:
        try: sys.mobu2vmc_state.sock.close()
        except: pass

sys.mobu2vmc_state = Mobu2VMCState()
g_sender = sys.mobu2vmc_state
g_ui = {}

# ── Data Tables ───────────────────────────────────────────────────────────────
VMC_BONE_NAMES = set([
    "Hips","Spine","Chest","UpperChest","Neck","Head",
    "LeftEye","RightEye","Jaw",
    "LeftShoulder","LeftUpperArm","LeftLowerArm","LeftHand",
    "RightShoulder","RightUpperArm","RightLowerArm","RightHand",
    "LeftUpperLeg","LeftLowerLeg","LeftFoot","LeftToes",
    "RightUpperLeg","RightLowerLeg","RightFoot","RightToes",
    "LeftThumbProximal","LeftThumbIntermediate","LeftThumbDistal",
    "LeftIndexProximal","LeftIndexIntermediate","LeftIndexDistal",
    "LeftMiddleProximal","LeftMiddleIntermediate","LeftMiddleDistal",
    "LeftRingProximal","LeftRingIntermediate","LeftRingDistal",
    "LeftLittleProximal","LeftLittleIntermediate","LeftLittleDistal",
    "RightThumbProximal","RightThumbIntermediate","RightThumbDistal",
    "RightIndexProximal","RightIndexIntermediate","RightIndexDistal",
    "RightMiddleProximal","RightMiddleIntermediate","RightMiddleDistal",
    "RightRingProximal","RightRingIntermediate","RightRingDistal",
    "RightLittleProximal","RightLittleIntermediate","RightLittleDistal",
])

# Standard T-Pose global positions (cm, Y-up, ~170cm character)
STANDARD_POSITIONS = {
    "Hips":(0,96,0),"Spine":(0,104,0),"Chest":(0,116,0),"UpperChest":(0,126,0),
    "Neck":(0,140,0),"Head":(0,150,0),
    "RightEye":(-3,158,7),"LeftEye":(3,158,7),"Jaw":(0,148,5),
    "RightShoulder":(-7,137,0),"RightUpperArm":(-18,137,0),
    "RightLowerArm":(-42,137,0),"RightHand":(-64,137,0),
    "LeftShoulder":(7,137,0),"LeftUpperArm":(18,137,0),
    "LeftLowerArm":(42,137,0),"LeftHand":(64,137,0),
    "RightUpperLeg":(-9,96,0),"RightLowerLeg":(-9,52,0),
    "RightFoot":(-9,8,0),"RightToes":(-9,0,8),
    "LeftUpperLeg":(9,96,0),"LeftLowerLeg":(9,52,0),
    "LeftFoot":(9,8,0),"LeftToes":(9,0,8),
    # Left Fingers (Left=+X, all joints at Y=137)
    "LeftThumbProximal":(66,137,3),"LeftThumbIntermediate":(68,137,5),"LeftThumbDistal":(70,137,7),
    "LeftIndexProximal":(68,137,2),"LeftIndexIntermediate":(72,137,2),"LeftIndexDistal":(75,137,2),
    "LeftMiddleProximal":(68,137,0),"LeftMiddleIntermediate":(72,137,0),"LeftMiddleDistal":(75,137,0),
    "LeftRingProximal":(68,137,-2),"LeftRingIntermediate":(72,137,-2),"LeftRingDistal":(75,137,-2),
    "LeftLittleProximal":(67,137,-4),"LeftLittleIntermediate":(70,137,-4),"LeftLittleDistal":(73,137,-4),
    # Right Fingers (Right=-X, mirror)
    "RightThumbProximal":(-66,137,3),"RightThumbIntermediate":(-68,137,5),"RightThumbDistal":(-70,137,7),
    "RightIndexProximal":(-68,137,2),"RightIndexIntermediate":(-72,137,2),"RightIndexDistal":(-75,137,2),
    "RightMiddleProximal":(-68,137,0),"RightMiddleIntermediate":(-72,137,0),"RightMiddleDistal":(-75,137,0),
    "RightRingProximal":(-68,137,-2),"RightRingIntermediate":(-72,137,-2),"RightRingDistal":(-75,137,-2),
    "RightLittleProximal":(-67,137,-4),"RightLittleIntermediate":(-70,137,-4),"RightLittleDistal":(-73,137,-4),
}

UNITY_HIERARCHY = {
    "Hips":None,"Spine":"Hips","Chest":"Spine","UpperChest":"Chest",
    "Neck":"UpperChest","Head":"Neck",
    "LeftEye":"Head","RightEye":"Head","Jaw":"Head",
    "LeftShoulder":"UpperChest","LeftUpperArm":"LeftShoulder",
    "LeftLowerArm":"LeftUpperArm","LeftHand":"LeftLowerArm",
    "RightShoulder":"UpperChest","RightUpperArm":"RightShoulder",
    "RightLowerArm":"RightUpperArm","RightHand":"RightLowerArm",
    "LeftUpperLeg":"Hips","LeftLowerLeg":"LeftUpperLeg",
    "LeftFoot":"LeftLowerLeg","LeftToes":"LeftFoot",
    "RightUpperLeg":"Hips","RightLowerLeg":"RightUpperLeg",
    "RightFoot":"RightLowerLeg","RightToes":"RightFoot",
    # Left Fingers
    "LeftThumbProximal":"LeftHand","LeftThumbIntermediate":"LeftThumbProximal","LeftThumbDistal":"LeftThumbIntermediate",
    "LeftIndexProximal":"LeftHand","LeftIndexIntermediate":"LeftIndexProximal","LeftIndexDistal":"LeftIndexIntermediate",
    "LeftMiddleProximal":"LeftHand","LeftMiddleIntermediate":"LeftMiddleProximal","LeftMiddleDistal":"LeftMiddleIntermediate",
    "LeftRingProximal":"LeftHand","LeftRingIntermediate":"LeftRingProximal","LeftRingDistal":"LeftRingIntermediate",
    "LeftLittleProximal":"LeftHand","LeftLittleIntermediate":"LeftLittleProximal","LeftLittleDistal":"LeftLittleIntermediate",
    # Right Fingers
    "RightThumbProximal":"RightHand","RightThumbIntermediate":"RightThumbProximal","RightThumbDistal":"RightThumbIntermediate",
    "RightIndexProximal":"RightHand","RightIndexIntermediate":"RightIndexProximal","RightIndexDistal":"RightIndexIntermediate",
    "RightMiddleProximal":"RightHand","RightMiddleIntermediate":"RightMiddleProximal","RightMiddleDistal":"RightMiddleIntermediate",
    "RightRingProximal":"RightHand","RightRingIntermediate":"RightRingProximal","RightRingDistal":"RightRingIntermediate",
    "RightLittleProximal":"RightHand","RightLittleIntermediate":"RightLittleProximal","RightLittleDistal":"RightLittleIntermediate",
}

HIK_MAPPING = {
    "Hips":"HipsLink","Spine":"SpineLink","Chest":"Spine1Link",
    "UpperChest":"Spine2Link","Neck":"NeckLink","Head":"HeadLink",
    "LeftShoulder":"LeftShoulderLink","LeftUpperArm":"LeftArmLink",
    "LeftLowerArm":"LeftForeArmLink","LeftHand":"LeftHandLink",
    "RightShoulder":"RightShoulderLink","RightUpperArm":"RightArmLink",
    "RightLowerArm":"RightForeArmLink","RightHand":"RightHandLink",
    "LeftUpperLeg":"LeftUpLegLink","LeftLowerLeg":"LeftLegLink",
    "LeftFoot":"LeftFootLink","LeftToes":"LeftToeBaseLink",
    "RightUpperLeg":"RightUpLegLink","RightLowerLeg":"RightLegLink",
    "RightFoot":"RightFootLink","RightToes":"RightToeBaseLink",
    "LeftThumbProximal":"LeftHandThumb1Link","LeftThumbIntermediate":"LeftHandThumb2Link","LeftThumbDistal":"LeftHandThumb3Link",
    "LeftIndexProximal":"LeftHandIndex1Link","LeftIndexIntermediate":"LeftHandIndex2Link","LeftIndexDistal":"LeftHandIndex3Link",
    "LeftMiddleProximal":"LeftHandMiddle1Link","LeftMiddleIntermediate":"LeftHandMiddle2Link","LeftMiddleDistal":"LeftHandMiddle3Link",
    "LeftRingProximal":"LeftHandRing1Link","LeftRingIntermediate":"LeftHandRing2Link","LeftRingDistal":"LeftHandRing3Link",
    "LeftLittleProximal":"LeftHandPinky1Link","LeftLittleIntermediate":"LeftHandPinky2Link","LeftLittleDistal":"LeftHandPinky3Link",
    "RightThumbProximal":"RightHandThumb1Link","RightThumbIntermediate":"RightHandThumb2Link","RightThumbDistal":"RightHandThumb3Link",
    "RightIndexProximal":"RightHandIndex1Link","RightIndexIntermediate":"RightHandIndex2Link","RightIndexDistal":"RightHandIndex3Link",
    "RightMiddleProximal":"RightHandMiddle1Link","RightMiddleIntermediate":"RightHandMiddle2Link","RightMiddleDistal":"RightHandMiddle3Link",
    "RightRingProximal":"RightHandRing1Link","RightRingIntermediate":"RightHandRing2Link","RightRingDistal":"RightHandRing3Link",
    "RightLittleProximal":"RightHandPinky1Link","RightLittleIntermediate":"RightHandPinky2Link","RightLittleDistal":"RightHandPinky3Link",
}

# ── OSC Encoding ──────────────────────────────────────────────────────────────
def encode_osc_str(s):
    b = s.encode('utf-8') + b'\x00'
    pad = (4 - len(b) % 4) % 4
    return b + b'\x00' * pad

def encode_bone_msg(address, bone_name, px, py, pz, qx, qy, qz, qw):
    return (encode_osc_str(address) +
            encode_osc_str(",sfffffff") +
            encode_osc_str(bone_name) +
            struct.pack('>7f', px, py, pz, qx, qy, qz, qw))

def encode_ok_msg(loaded, calib_state, calib_mode):
    """Send /VMC/Ext/OK — tells receiver the sender state (required by VMC spec)."""
    return (encode_osc_str("/VMC/Ext/OK") +
            encode_osc_str(",iii") +
            struct.pack('>3i', loaded, calib_state, calib_mode))

# ── Coordinate Conversion ─────────────────────────────────────────────────────
def euler_to_quat(ex_deg, ey_deg, ez_deg):
    ex = math.radians(ex_deg)
    ey = math.radians(ey_deg)
    ez = math.radians(ez_deg)
    cx=math.cos(ex*.5); sx=math.sin(ex*.5)
    cy=math.cos(ey*.5); sy=math.sin(ey*.5)
    cz=math.cos(ez*.5); sz=math.sin(ez*.5)
    qw = cx*cy*cz + sx*sy*sz
    qx = sx*cy*cz - cx*sy*sz
    qy = cx*sy*cz + sx*cy*sz
    qz = cx*cy*sz - sx*sy*cz
    return qx, qy, qz, qw

def mb_to_vmc(model):
    pos = FBVector3d()
    rot = FBVector3d()
    model.GetVector(pos, FBModelTransformationType.kModelTranslation, False)
    model.GetVector(rot, FBModelTransformationType.kModelRotation,    False)
    vmc_px = -pos[0] / 100.0
    vmc_py =  pos[1] / 100.0
    vmc_pz =  pos[2] / 100.0  # Fixed Z-axis inversion
    qx, qy, qz, qw = euler_to_quat(rot[0], rot[1], rot[2])
    if g_sender.vmc2mobu_mode:
        # VMC2Mobu skeleton (Root Y~180): exact inverse of vmc_to_mb
        return vmc_px, vmc_py, vmc_pz, -qx, -qy, qz, qw
    else:
        # New skeleton (Root Y=0): standard MB→VMC handedness flip
        return vmc_px, vmc_py, vmc_pz, -qx, qy, qz, -qw

# ── Scene Scan ────────────────────────────────────────────────────────────────
def scan_vmc_bones():
    """Scan VMC_ bones. Detect skeleton type via VMC_Source property on VMC_Root.
    If property is 'Mobu2VMC' -> new skeleton (mode=False).
    If property absent    -> VMC2Mobu imported skeleton (mode=True)."""
    root_model = None
    bones = {}
    try:
        for comp in FBSystem().Scene.Components:
            try:
                if not isinstance(comp, FBModel): continue
                name = comp.Name
                if name == "VMC_Root":
                    root_model = comp
                elif name.startswith("VMC_"):
                    bn = name[4:]
                    if bn in VMC_BONE_NAMES:
                        bones[bn] = comp
            except: continue
    except: pass
    if root_model:
        src_prop = root_model.PropertyList.Find("VMC_Source")
        is_new = (src_prop is not None and src_prop.Data == "Mobu2VMC")
        g_sender.vmc2mobu_mode = not is_new
    else:
        g_sender.vmc2mobu_mode = False
    return root_model, bones

# ── Generate Standard Skeleton ────────────────────────────────────────────────
def OnGenerateSkeletonClick(control, event):
    # Check if VMC_Root already exists
    for comp in FBSystem().Scene.Components:
        try:
            if isinstance(comp, FBModel) and comp.Name == "VMC_Root":
                FBMessageBox("Warning",
                    "VMC_Root already exists in scene!\n"
                    "Please delete it first before generating.", "OK")
                return
        except: continue

    models = {}

    # Create Root null (Y=0 — stable T-pose for HIK characterization)
    root = FBModelNull("VMC_Root")
    root.Show = True; root.Size = 50.0
    # Stamp a property so Mobu2VMC can identify this as a self-generated skeleton
    try:
        p = root.PropertyCreate("VMC_Source", FBPropertyType.kFBPT_charptr, "String", False, True, None)
        if p: p.Data = "Mobu2VMC"
    except: pass
    models["Root"] = root

    # Create bones and set positions BEFORE parenting.
    # With no parent yet, local = world, so STANDARD_POSITIONS are world coords.
    # Left arm is at +X, right at -X — correct T-pose facing +Z.
    for b_name, pos in STANDARD_POSITIONS.items():
        m = FBModelSkeleton("VMC_" + b_name)
        m.Show = True; m.Size = 50.0
        m.SetVector(FBVector3d(pos[0], pos[1], pos[2]),
                    FBModelTransformationType.kModelTranslation, False)
        models[b_name] = m

    # Establish hierarchy — MB auto-computes local positions relative to each parent
    for b_name, parent_name in UNITY_HIERARCHY.items():
        if b_name not in models: continue
        if parent_name is None:
            models[b_name].Parent = root
        elif parent_name in models:
            models[b_name].Parent = models[parent_name]

    # Zero all rotations (T-Pose, Root stays Y=0)
    for b_name, m in models.items():
        m.SetVector(FBVector3d(0, 0, 0),
                    FBModelTransformationType.kModelRotation, False)

    FBSystem().Scene.Evaluate()
    FBMessageBox("Success",
        "Standard VMC skeleton generated!\n"
        "Bones: {} + VMC_Root\n"
        "Left arm at +X, facing +Z".format(len(STANDARD_POSITIONS)), "OK")

# ── Characterize HIK ──────────────────────────────────────────────────────────
def OnCharacterizeClick(control, event):
    root_model, bones = scan_vmc_bones()
    if not bones:
        FBMessageBox("Warning",
            "No VMC_ skeleton found!\nPlease generate or load a VMC skeleton first.", "OK")
        return

    char_name = "VMC_HIK_Character"
    char = None
    for c in FBSystem().Scene.Characters:
        if c.Name == char_name:
            char = c; break

    if not char:
        char = FBCharacter(char_name)

    char.SetCharacterizeOn(False)

    # Map bones to HIK slots
    for vmc_name, prop_name in HIK_MAPPING.items():
        if vmc_name not in bones: continue
        model = bones[vmc_name]
        prop  = char.PropertyList.Find(prop_name)
        if prop:
            prop.removeAll()
            try:    prop.append(model)
            except: prop.insert(model)
        else:
            base = prop_name.replace("Link","")
            for p in char.PropertyList:
                if p.Name.endswith("Link") and base in p.Name:
                    p.removeAll()
                    try:    p.append(model)
                    except: p.insert(model)
                    break

    # Handle Spine fallback
    if "Spine" not in bones and "Chest" in bones:
        prop = char.PropertyList.Find("SpineLink")
        if prop:
            prop.removeAll()
            try:    prop.append(bones["Chest"])
            except: prop.insert(bones["Chest"])

    # Force T-Pose: bones to zero, Root to Y=0 for HIK to characterize correctly.
    for b_name, m in bones.items():
        m.SetVector(FBVector3d(0,0,0),
                    FBModelTransformationType.kModelRotation, False)
    if root_model:
        root_model.SetVector(FBVector3d(0,0,0),
                             FBModelTransformationType.kModelRotation, False)

    # Map VMC_Root to HIK Reference node
    if root_model:
        ref_prop = char.PropertyList.Find("ReferenceLink")
        if ref_prop is None:
            # Fallback: scan all properties for any containing "Reference"
            for p in char.PropertyList:
                if "Reference" in p.Name:
                    ref_prop = p
                    print("Mobu2VMC: Found Reference property:", p.Name)
                    break
        if ref_prop is not None:
            ref_prop.removeAll()
            try:
                ref_prop.append(root_model)
                print("Mobu2VMC: ReferenceLink -> VMC_Root [OK]")
            except:
                try:
                    ref_prop.insert(root_model)
                    print("Mobu2VMC: ReferenceLink -> VMC_Root [OK via insert]")
                except Exception as e:
                    print("Mobu2VMC: ReferenceLink failed:", e)
        else:
            print("Mobu2VMC: ReferenceLink property NOT found")

    FBSystem().Scene.Evaluate()

    success = char.SetCharacterizeOn(True)

    # Restore Root to Y=180 after characterize ONLY for imported skeletons (VMC2Mobu)
    if root_model and g_sender.vmc2mobu_mode:
        root_model.SetVector(FBVector3d(0, 180, 0),
                             FBModelTransformationType.kModelRotation, False)
    FBSystem().Scene.Evaluate()

    if success:
        FBMessageBox("Success", "HIK Characterized Successfully!\nRoot restored to Y=180.", "OK")
    else:
        err = char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning",
            "Characterization failed.\nError: " + str(err) +
            "\nCheck Python Console.", "OK")

# ── FPS Selection ─────────────────────────────────────────────────────────────
def set_fps(fps):
    g_sender.fps_limit = fps
    g_ui["btn_fps24"].Caption = "[24]" if fps == 24 else " 24 "
    g_ui["btn_fps30"].Caption = "[30]" if fps == 30 else " 30 "
    g_ui["btn_fps60"].Caption = "[60]" if fps == 60 else " 60 "

def OnFPS24Click(c, e): set_fps(24)
def OnFPS30Click(c, e): set_fps(30)
def OnFPS60Click(c, e): set_fps(60)

# ── Send Loop ─────────────────────────────────────────────────────────────────
def OnSendUIIdle(control, event):
    if not g_sender.is_sending or not g_sender.sock:
        return

    # FPS throttle
    now = time.time()
    if now - g_sender.last_send_time < (1.0 / g_sender.fps_limit):
        return
    g_sender.last_send_time = now

    target = (g_sender.target_ip, g_sender.target_port)
    sent   = 0

    # ── Root: Send Global X and Z translation to Root ──────────
    root_px, root_pz = 0.0, 0.0
    if "Hips" in g_sender.bone_cache:
        hip_global = FBVector3d()
        g_sender.bone_cache["Hips"].GetVector(hip_global, FBModelTransformationType.kModelTranslation, True)
        root_px = -hip_global[0] / 100.0
        root_pz =  hip_global[2] / 100.0  # Fixed Z-axis inversion (Mobu +Z is Forward, Unity +Z is Forward)

    # Apply Global Scale to Root World Translation
    root_px *= g_sender.hip_scale_x
    root_pz *= g_sender.hip_scale_z

    g_sender.sock.sendto(
        encode_bone_msg("/VMC/Ext/Root/Pos","root",root_px,0.0,root_pz,0,0,0,1), target)
    sent += 1

    # ── Bones: local position + rotation (standard VMC format) ────────────────
    for bone_name, model in g_sender.bone_cache.items():
        try:
            px,py,pz,qx,qy,qz,qw = mb_to_vmc(model)
            if bone_name == "Hips":
                # Standard VMC: Root handles world X/Z. Hips handles local Y (height) and rotation.
                px = 0.0
                pz = 0.0
                # py remains unchanged to allow crouching/jumping
            else:
                # VMC Spec recommendation: Send 0 for non-root/hips bone positions to preserve receiver's model proportions
                px, py, pz = 0.0, 0.0, 0.0
                
            g_sender.sock.sendto(
                encode_bone_msg("/VMC/Ext/Bone/Pos",bone_name,px,py,pz,qx,qy,qz,qw), target)
            sent += 1
        except: pass

    g_sender.frame_count += 1
    if g_sender.frame_count % 60 == 0:
        try:
            g_ui["lbl_status"].Caption = "Sending: {} msgs @ {}fps -> {}:{}".format(
                sent, g_sender.fps_limit,
                g_sender.target_ip, g_sender.target_port)
        except:
            try: FBSystem().OnUIIdle.Remove(OnSendUIIdle)
            except: pass

# ── Hip Scale ────────────────────────────────────────────────────────────────
def OnHipScaleXChange(control, event):
    g_sender.hip_scale_x = control.Value

def OnHipScaleZChange(control, event):
    g_sender.hip_scale_z = control.Value

# ── Button Callbacks ──────────────────────────────────────────────────────────
def OnScanClick(control, event):
    root_model, bones = scan_vmc_bones()
    total = (1 if root_model else 0) + len(bones)
    if total == 0:
        FBMessageBox("Scan Result",
            "No VMC_ bones found in scene.\n\n"
            "Please click 'Generate Skeleton'\n"
            "to create a standard VMC skeleton.", "OK")
        return
    lines = ["Found {} VMC_ model(s):".format(total)]
    if root_model: lines.append("  [Root]  VMC_Root")
    for b in sorted(bones.keys()): lines.append("  [Bone]  VMC_" + b)
    FBMessageBox("Scan Result", "\n".join(lines), "OK")

def OnStartSendClick(control, event):
    if g_sender.is_sending: return
    root_model, bones = scan_vmc_bones()
    if not root_model and not bones:
        FBMessageBox("Warning",
            "No VMC_ skeleton found in scene!\n"
            "Please use 'Generate Skeleton' first.", "OK")
        return
    try:
        g_sender.target_ip   = g_ui["edit_ip"].Text
        g_sender.target_port = int(g_ui["edit_port"].Text.strip())
        g_sender.root_cache  = root_model
        g_sender.bone_cache  = bones
        g_sender.sock        = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        g_sender.is_sending  = True
        g_sender.frame_count = 0
        g_sender.last_send_time = 0.0

        g_ui["btn_start"].Caption = "Sending..."
        g_ui["btn_start"].Enabled = False
        g_ui["btn_stop"].Enabled  = True
        g_ui["lbl_status"].Caption = "Status: Sending to {}:{} ({} bones, {}fps)".format(
            g_sender.target_ip, g_sender.target_port,
            len(bones), g_sender.fps_limit)

        fb_sys = FBSystem()
        fb_sys.OnUIIdle.Remove(OnSendUIIdle)
        fb_sys.OnUIIdle.Add(OnSendUIIdle)
        import sys as python_sys
        python_sys.mobu2vmc_idle_func = OnSendUIIdle

        print("Mobu2VMC: Sending to {}:{} | bones:{} fps:{}".format(
            g_sender.target_ip, g_sender.target_port, len(bones), g_sender.fps_limit))
    except Exception as e:
        g_ui["lbl_status"].Caption = "Error: " + str(e)
        print("Mobu2VMC start error:", e)

def OnStopSendClick(control, event):
    g_sender.is_sending = False
    if g_sender.sock:
        try: g_sender.sock.close()
        except: pass
        g_sender.sock = None
    try: FBSystem().OnUIIdle.Remove(OnSendUIIdle)
    except: pass
    g_ui["btn_start"].Caption = "Start Sending"
    g_ui["btn_start"].Enabled = True
    g_ui["btn_stop"].Enabled  = False
    g_ui["lbl_status"].Caption = "Status: Stopped"
    print("Mobu2VMC: Stopped.")

# ── Delete Skeleton ───────────────────────────────────────────────────────────
def OnDeleteSkeletonClick(control, event):
    # Stop sending first
    if g_sender.is_sending:
        OnStopSendClick(None, None)

    # Delete HIK character first (bones are locked while characterized)
    char_name = "VMC_HIK_Character"
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name:
            try: c.SetCharacterizeOn(False)
            except: pass
            try: c.FBDelete()
            except: pass

    # Delete all VMC_ models from scene
    deleted = 0
    for comp in list(FBSystem().Scene.Components):
        try:
            if isinstance(comp, FBModel) and comp.Name and comp.Name.startswith("VMC_"):
                comp.FBDelete()
                deleted += 1
        except: pass

    g_sender.bone_cache.clear()
    g_sender.root_cache = None
    print("Mobu2VMC: Deleted {} VMC_ objects.".format(deleted))
    FBMessageBox("Done", "Deleted VMC skeleton ({} objects).".format(deleted), "OK")

# ── UI ────────────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 350
    tool.StartSizeY = 570

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main","main", x, y, w, h)

    g_ui["main_layout"] = FBVBoxLayout()
    tool.SetControl("main", g_ui["main_layout"])

    def hdr(text):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        return lbl

    def btn(caption, cb):
        b = FBButton(); b.Caption = caption; b.OnClick.Add(cb); return b

    # ── SKELETON buttons
    g_ui["btn_scan"]  = btn("Scan VMC_ Bones in Scene",     OnScanClick)
    g_ui["btn_gen"]   = btn("Generate Standard Skeleton",   OnGenerateSkeletonClick)
    g_ui["btn_char"]  = btn("Characterize HIK",             OnCharacterizeClick)
    g_ui["btn_del"]   = btn("Delete VMC Skeleton",          OnDeleteSkeletonClick)

    # ── Send Target
    g_ui["lyt_ip"]   = FBHBoxLayout()
    g_ui["lbl_ip"]   = FBLabel();     g_ui["lbl_ip"].Caption  = "Target IP:"
    g_ui["edit_ip"]  = FBEdit();      g_ui["edit_ip"].Text    = "127.0.0.1"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 80); g_ui["lyt_ip"].Add(g_ui["edit_ip"], 180)

    g_ui["lyt_port"]  = FBHBoxLayout()
    g_ui["lbl_port"]  = FBLabel();      g_ui["lbl_port"].Caption  = "UDP Port:"
    g_ui["edit_port"] = FBEdit(); g_ui["edit_port"].Text    = "39539"
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 80); g_ui["lyt_port"].Add(g_ui["edit_port"], 180)

    # ── FPS selector
    g_ui["lyt_fps"]   = FBHBoxLayout()
    g_ui["lbl_fps"]   = FBLabel(); g_ui["lbl_fps"].Caption = "Send FPS:"
    g_ui["btn_fps24"] = FBButton(); g_ui["btn_fps24"].Caption = " 24 "; g_ui["btn_fps24"].OnClick.Add(OnFPS24Click)
    g_ui["btn_fps30"] = FBButton(); g_ui["btn_fps30"].Caption = "[30]"; g_ui["btn_fps30"].OnClick.Add(OnFPS30Click)
    g_ui["btn_fps60"] = FBButton(); g_ui["btn_fps60"].Caption = " 60 "; g_ui["btn_fps60"].OnClick.Add(OnFPS60Click)
    g_ui["lyt_fps"].Add(g_ui["lbl_fps"],   80)
    g_ui["lyt_fps"].Add(g_ui["btn_fps24"], 55)
    g_ui["lyt_fps"].Add(g_ui["btn_fps30"], 55)
    g_ui["lyt_fps"].Add(g_ui["btn_fps60"], 55)

    # ── Global Position Weight X
    g_ui["lyt_hip_x"]     = FBHBoxLayout()
    g_ui["lbl_hip_x"]     = FBLabel(); g_ui["lbl_hip_x"].Caption = "Global Weight X:"
    g_ui["slider_hip_x"]  = FBEditNumber()
    g_ui["slider_hip_x"].Min = 0.0
    g_ui["slider_hip_x"].Max = 2.0
    g_ui["slider_hip_x"].Value = g_sender.hip_scale_x
    g_ui["slider_hip_x"].Precision = 2
    g_ui["slider_hip_x"].OnChange.Add(OnHipScaleXChange)
    g_ui["lyt_hip_x"].Add(g_ui["lbl_hip_x"],     100)
    g_ui["lyt_hip_x"].Add(g_ui["slider_hip_x"],  150)

    # ── Global Position Weight Z
    g_ui["lyt_hip_z"]     = FBHBoxLayout()
    g_ui["lbl_hip_z"]     = FBLabel(); g_ui["lbl_hip_z"].Caption = "Global Weight Z:"
    g_ui["slider_hip_z"]  = FBEditNumber()
    g_ui["slider_hip_z"].Min = 0.0
    g_ui["slider_hip_z"].Max = 2.0
    g_ui["slider_hip_z"].Value = g_sender.hip_scale_z
    g_ui["slider_hip_z"].Precision = 2
    g_ui["slider_hip_z"].OnChange.Add(OnHipScaleZChange)
    g_ui["lyt_hip_z"].Add(g_ui["lbl_hip_z"],     100)
    g_ui["lyt_hip_z"].Add(g_ui["slider_hip_z"],  150)

    # ── Start / Stop (side by side)
    g_ui["lyt_ctrl"]  = FBHBoxLayout()
    g_ui["btn_start"] = btn("Start Sending", OnStartSendClick)
    g_ui["btn_stop"]  = btn("Stop",          OnStopSendClick)
    g_ui["btn_stop"].Enabled = False
    g_ui["lyt_ctrl"].Add(g_ui["btn_start"], 200)
    g_ui["lyt_ctrl"].Add(g_ui["btn_stop"],   80)

    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Status: Stopped"

    # ── Layout
    lay = g_ui["main_layout"]
    lay.Add(hdr("SKELETON"),              25)
    lay.Add(g_ui["btn_scan"],             35)
    lay.Add(g_ui["btn_gen"],              35)
    lay.Add(g_ui["btn_char"],             35)
    lay.Add(g_ui["btn_del"],             35)
    lay.Add(hdr("SEND & CONTROL"),        25)
    lay.Add(g_ui["lyt_ip"],              30)
    lay.Add(g_ui["lyt_port"],            30)
    lay.Add(g_ui["lyt_fps"],             30)
    lay.Add(g_ui["lyt_hip_x"],           30)
    lay.Add(g_ui["lyt_hip_z"],           30)
    lay.Add(g_ui["lyt_ctrl"],            35)
    lay.Add(g_ui["lbl_status"],          30)

def CreateTool():
    tool_name = "Saint's Mobu2VMC Sender"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome",
            "本工具由小聖腦絲與Antigravity協作完成\n"
            "https://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating tool")

CreateTool()
