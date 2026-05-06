"""
Mobu2VMC_MultiActor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stream bone rotations and root position from MotionBuilder to VMC-compatible
receivers (e.g. Warudo) via OSC/UDP. Supports up to 3 simultaneous actors,
each with an independent namespace (VMC1/VMC2/VMC3) and target port.

Workflow:
  1. Select Actor → Generate Standard Skeleton (or load existing VMC_ bones)
  2. (Optional) Match Proportions to a source HIK character → Characterize HIK
  3. Set Target IP & Port → Start Sending

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys
import socket
import struct
import math
import time
import json
from pyfbsdk import *
from pyfbsdk_additions import *

# ── Global State ──────────────────────────────────────────────────────────────
class Mobu2VMCState:
    def __init__(self, actor_id):
        self.actor_id       = actor_id
        self.sock           = None
        self.is_connected   = False  # renamed from is_sending for consistency
        self.target_ip      = "127.0.0.1"
        self.target_port    = 39539 + (actor_id - 1)
        self.bone_cache     = {}
        self.root_cache     = None
        self.prop_data      = []     # New: List of {"model": FBModel, "port": int}
        self.frame_count    = 0
        self.fps_limit      = 30
        self.last_send_time = 0.0
        self.hip_scale_x    = 1.0   # Scale factor for Hips local X (sideways)
        self.hip_scale_z    = 1.0   # Scale factor for Hips local Z (forward)
        self.vmc2mobu_mode  = False  # True = VMC2Mobu skeleton (Root Y~180)

if hasattr(sys, "mobu2vmc_multiactor_states") and sys.mobu2vmc_multiactor_states is not None:
    try: FBSystem().OnUIIdle.Remove(sys.mobu2vmc_multiactor_idle_func)
    except: pass
    for state in sys.mobu2vmc_multiactor_states.values():
        if state.sock:
            try: state.sock.close()
            except: pass

sys.mobu2vmc_multiactor_states = {1: Mobu2VMCState(1), 2: Mobu2VMCState(2), 3: Mobu2VMCState(3), 4: Mobu2VMCState(4)}
g_sender_states = sys.mobu2vmc_multiactor_states
g_ui = {}

def current_actor():
    try: return g_ui["list_actor"].ItemIndex + 1
    except: return 1

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

def mb_to_vmc(model, state):
    pos = FBVector3d()
    rot = FBVector3d()
    model.GetVector(pos, FBModelTransformationType.kModelTranslation, False)
    model.GetVector(rot, FBModelTransformationType.kModelRotation,    False)
    vmc_px = -pos[0] / 100.0
    vmc_py =  pos[1] / 100.0
    vmc_pz =  pos[2] / 100.0  # Fixed Z-axis inversion
    qx, qy, qz, qw = euler_to_quat(rot[0], rot[1], rot[2])
    if state.vmc2mobu_mode:
        # VMC2Mobu skeleton (Root Y~180): exact inverse of vmc_to_mb
        return vmc_px, vmc_py, vmc_pz, -qx, -qy, qz, qw
    else:
        # New skeleton (Root Y=0): standard MB→VMC handedness flip
        return vmc_px, vmc_py, vmc_pz, -qx, qy, qz, -qw

# ── VRM Proportions Matching (pure-Python GLB parser, no third-party deps) ────
VRM_TO_VMC = {
    'hips':'Hips','spine':'Spine','chest':'Chest','upperChest':'UpperChest',
    'neck':'Neck','head':'Head','leftEye':'LeftEye','rightEye':'RightEye','jaw':'Jaw',
    'leftShoulder':'LeftShoulder','leftUpperArm':'LeftUpperArm',
    'leftLowerArm':'LeftLowerArm','leftHand':'LeftHand',
    'rightShoulder':'RightShoulder','rightUpperArm':'RightUpperArm',
    'rightLowerArm':'RightLowerArm','rightHand':'RightHand',
    'leftUpperLeg':'LeftUpperLeg','leftLowerLeg':'LeftLowerLeg',
    'leftFoot':'LeftFoot','leftToes':'LeftToes',
    'rightUpperLeg':'RightUpperLeg','rightLowerLeg':'RightLowerLeg',
    'rightFoot':'RightFoot','rightToes':'RightToes',
    'leftThumbProximal':'LeftThumbProximal','leftThumbIntermediate':'LeftThumbIntermediate','leftThumbDistal':'LeftThumbDistal',
    'leftIndexProximal':'LeftIndexProximal','leftIndexIntermediate':'LeftIndexIntermediate','leftIndexDistal':'LeftIndexDistal',
    'leftMiddleProximal':'LeftMiddleProximal','leftMiddleIntermediate':'LeftMiddleIntermediate','leftMiddleDistal':'LeftMiddleDistal',
    'leftRingProximal':'LeftRingProximal','leftRingIntermediate':'LeftRingIntermediate','leftRingDistal':'LeftRingDistal',
    'leftLittleProximal':'LeftLittleProximal','leftLittleIntermediate':'LeftLittleIntermediate','leftLittleDistal':'LeftLittleDistal',
    'rightThumbProximal':'RightThumbProximal','rightThumbIntermediate':'RightThumbIntermediate','rightThumbDistal':'RightThumbDistal',
    'rightIndexProximal':'RightIndexProximal','rightIndexIntermediate':'RightIndexIntermediate','rightIndexDistal':'RightIndexDistal',
    'rightMiddleProximal':'RightMiddleProximal','rightMiddleIntermediate':'RightMiddleIntermediate','rightMiddleDistal':'RightMiddleDistal',
    'rightRingProximal':'RightRingProximal','rightRingIntermediate':'RightRingIntermediate','rightRingDistal':'RightRingDistal',
    'rightLittleProximal':'RightLittleProximal','rightLittleIntermediate':'RightLittleIntermediate','rightLittleDistal':'RightLittleDistal',
}

def _vrm_parse_glb(filepath):
    """Parse a GLB/VRM file and return (gltf_json, bin_bytes)."""
    with open(filepath, 'rb') as f:
        if f.read(4) != b'glTF':
            raise ValueError("Not a valid GLB/VRM file")
        f.read(4)  # version
        total = struct.unpack('<I', f.read(4))[0]
        gltf_json = bin_data = None
        pos = 12
        while pos < total:
            f.seek(pos)
            clen  = struct.unpack('<I', f.read(4))[0]
            ctype = struct.unpack('<I', f.read(4))[0]
            cbytes = f.read(clen)
            if ctype == 0x4E4F534A:  # JSON chunk
                gltf_json = json.loads(cbytes.rstrip(b'\x00').decode('utf-8'))
            elif ctype == 0x004E4942:  # BIN chunk
                bin_data = cbytes
            pos += 8 + clen
    return gltf_json, bin_data

def _vrm_get_humanoid(gltf):
    """Return ({VMC_bone_name: node_index}, version_str) for VRM 0.x and 1.0."""
    ext = gltf.get('extensions', {})
    if 'VRM' in ext:  # VRM 0.x
        out = {}
        for e in ext['VRM'].get('humanoid', {}).get('humanBones', []):
            vmc = VRM_TO_VMC.get(e.get('bone', ''))
            if vmc and e.get('node', -1) >= 0:
                out[vmc] = e['node']
        return out, '0.x'
    if 'VRMC_vrm' in ext:  # VRM 1.0
        out = {}
        for vrm_name, data in ext['VRMC_vrm'].get('humanoid', {}).get('humanBones', {}).items():
            vmc = VRM_TO_VMC.get(vrm_name)
            if vmc and data.get('node', -1) >= 0:
                out[vmc] = data['node']
        return out, '1.0'
    return {}, 'unknown'

def _vrm_quat_rotate(q, v):
    """Rotate vector v=(x,y,z) by quaternion q=(x,y,z,w)."""
    qx, qy, qz, qw = q
    vx, vy, vz = v
    tx = 2.0*(qy*vz - qz*vy)
    ty = 2.0*(qz*vx - qx*vz)
    tz = 2.0*(qx*vy - qy*vx)
    return (vx+qw*tx+qy*tz-qz*ty, vy+qw*ty+qz*tx-qx*tz, vz+qw*tz+qx*ty-qy*tx)

def _vrm_quat_mul(a, b):
    ax,ay,az,aw = a; bx,by,bz,bw = b
    return (aw*bx+ax*bw+ay*bz-az*by, aw*by-ax*bz+ay*bw+az*bx,
            aw*bz+ax*by-ay*bx+az*bw, aw*bw-ax*bx-ay*by-az*bz)

def _vrm_world_positions(nodes):
    """Compute world (x,y,z) in metres for each glTF node via quaternion chain."""
    n = len(nodes)
    wp = [(0.,0.,0.)]*n
    wr = [(0.,0.,0.,1.)]*n
    par = [-1]*n
    done = [False]*n
    for i, nd in enumerate(nodes):
        for c in nd.get('children', []):
            if c < n: par[c] = i
    def _proc(i):
        if done[i]: return
        p = par[i]
        if p >= 0 and not done[p]: _proc(p)
        nd = nodes[i]
        if 'matrix' in nd:
            m = nd['matrix']
            lt, lr = (m[12],m[13],m[14]), (0.,0.,0.,1.)
        else:
            t = nd.get('translation',[0,0,0])
            r = nd.get('rotation',[0,0,0,1])  # x,y,z,w
            lt, lr = tuple(t[:3]), tuple(r[:4])
        if p < 0:
            wp[i], wr[i] = lt, lr
        else:
            rt = _vrm_quat_rotate(wr[p], lt)
            wp[i] = (wp[p][0]+rt[0], wp[p][1]+rt[1], wp[p][2]+rt[2])
            wr[i] = _vrm_quat_mul(wr[p], lr)
        done[i] = True
    for i in range(n): _proc(i)
    return wp

# ── Scene Scan ────────────────────────────────────────────────────────────────
def scan_vmc_bones(act_id):
    """Scan VMC bones for a specific actor using Namespace."""
    state = g_sender_states[act_id]
    root_model = None
    bones = {}
    prefix = "VMC{}:VMC_".format(act_id)
    try:
        for comp in FBSystem().Scene.Components:
            try:
                if not isinstance(comp, FBModel): continue
                name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
                
                if name == "VMC{}:VMC_Root".format(act_id):
                    root_model = comp
                elif name.startswith(prefix):
                    bn = name[len(prefix):]
                    if bn in VMC_BONE_NAMES:
                        bones[bn] = comp
            except: continue
    except: pass
    
    if root_model:
        src_prop = root_model.PropertyList.Find("VMC_Source")
        is_new = (src_prop is not None and src_prop.Data == "Mobu2VMC")
        state.vmc2mobu_mode = not is_new
    else:
        state.vmc2mobu_mode = False
    return root_model, bones

# ── Generate Standard Skeleton ────────────────────────────────────────────────
def OnGenerateSkeletonClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    prefix = "VMC{}:".format(act_id)
    
    # Check if VMC_Root already exists
    for comp in FBSystem().Scene.Components:
        try:
            name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
            if isinstance(comp, FBModel) and name == "{}VMC_Root".format(prefix):
                FBMessageBox("Warning",
                    "{}VMC_Root already exists in scene!\n".format(prefix) +
                    "Please delete it first before generating.", "OK")
                return
        except: continue

    models = {}

    # Create Root null (Y=0 — stable T-pose for HIK characterization)
    root = FBModelNull("{}VMC_Root".format(prefix))
    root.Show = True; root.Size = 50.0
    # Stamp a property so Mobu2VMC can identify this as a self-generated skeleton
    try:
        p = root.PropertyCreate("VMC_Source", FBPropertyType.kFBPT_charptr, "String", False, True, None)
        if p: p.Data = "Mobu2VMC"
    except: pass
    models["Root"] = root

    # Create bones and set positions BEFORE parenting.
    for b_name, pos in STANDARD_POSITIONS.items():
        m = FBModelSkeleton("{}VMC_".format(prefix) + b_name)
        m.Show = True; m.Size = 50.0
        m.SetVector(FBVector3d(pos[0], pos[1], pos[2]),
                    FBModelTransformationType.kModelTranslation, False)
        models[b_name] = m

    # Establish hierarchy
    for b_name, parent_name in UNITY_HIERARCHY.items():
        if b_name not in models: continue
        if parent_name is None:
            models[b_name].Parent = root
        elif parent_name in models:
            models[b_name].Parent = models[parent_name]

    # Zero all rotations
    for b_name, m in models.items():
        m.SetVector(FBVector3d(0, 0, 0),
                    FBModelTransformationType.kModelRotation, False)

    FBSystem().Scene.Evaluate()
    FBMessageBox("Success",
        "Standard VMC skeleton generated for Actor {}!\n".format(act_id) +
        "Bones: {} + VMC_Root\n".format(len(STANDARD_POSITIONS)) +
        "Left arm at +X, facing +Z", "OK")

# ── Characterize HIK ──────────────────────────────────────────────────────────
def OnCharacterizeClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    root_model, bones = scan_vmc_bones(act_id)
    if not bones:
        FBMessageBox("Warning",
            "No VMC_ skeleton found for Actor {}!\nPlease generate or load a VMC skeleton first.".format(act_id), "OK")
        return

    char_name = "VMC{}:VMC_HIK_Character".format(act_id)
    char = None
    for c in FBSystem().Scene.Characters:
        if c.Name == char_name or (hasattr(c, "LongName") and c.LongName == char_name):
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
            for p in char.PropertyList:
                if "Reference" in p.Name:
                    ref_prop = p
                    break
        if ref_prop is not None:
            ref_prop.removeAll()
            try: ref_prop.append(root_model)
            except:
                try: ref_prop.insert(root_model)
                except: pass

    FBSystem().Scene.Evaluate()

    success = char.SetCharacterizeOn(True)

    if root_model and state.vmc2mobu_mode:
        root_model.SetVector(FBVector3d(0, 180, 0),
                             FBModelTransformationType.kModelRotation, False)
    FBSystem().Scene.Evaluate()

    if success:
        FBMessageBox("Success", "Actor {} HIK Characterized Successfully!".format(act_id), "OK")
    else:
        err = char.GetCharacterizeError()
        print("CHARACTERIZE ERROR:", err)
        FBMessageBox("Warning",
            "Characterization failed.\nError: " + str(err) +
            "\nCheck Python Console.", "OK")

# ── FPS Selection ─────────────────────────────────────────────────────────────
def set_fps(fps):
    act_id = current_actor()
    state = g_sender_states[act_id]
    state.fps_limit = fps
    g_ui["btn_fps24"].Caption = "[24]" if fps == 24 else " 24 "
    g_ui["btn_fps30"].Caption = "[30]" if fps == 30 else " 30 "
    g_ui["btn_fps60"].Caption = "[60]" if fps == 60 else " 60 "

def OnFPS24Click(c, e): set_fps(24)
def OnFPS30Click(c, e): set_fps(30)
def OnFPS60Click(c, e): set_fps(60)

# ── Send Loop ─────────────────────────────────────────────────────────────────
def OnSendUIIdle(control, event):
    now = time.time()
    for act_id, state in g_sender_states.items():
        if not state.is_connected or not state.sock:
            continue

        if now - state.last_send_time < (1.0 / state.fps_limit):
            continue
        state.last_send_time = now

        target = (state.target_ip, state.target_port)
        sent   = 0

        if act_id == 4:
            for item in state.prop_data:
                try:
                    model = item["model"]
                    port  = item["port"]
                    p_target = (state.target_ip, port)
                    
                    pos = FBVector3d()
                    rot = FBVector3d()
                    model.GetVector(pos, FBModelTransformationType.kModelTranslation, True)
                    model.GetVector(rot, FBModelTransformationType.kModelRotation,    True)
                    
                    px = -pos[0] / 100.0
                    py =  pos[1] / 100.0
                    pz =  pos[2] / 100.0
                    qx, qy, qz, qw = euler_to_quat(rot[0], rot[1], rot[2])
                    # X inversion
                    qx = -qx; qw = -qw
                    
                    # Standard Tracker msg
                    state.sock.sendto(
                        encode_bone_msg("/VMC/Ext/Tra/Pos", model.Name, px, py, pz, qx, qy, qz, qw), p_target)
                    
                    # Root Hack: Send as Root for easy Warudo mapping
                    state.sock.sendto(
                        encode_bone_msg("/VMC/Ext/Root/Pos", "root", px, py, pz, qx, qy, qz, qw), p_target)
                        
                    sent += 1
                except: pass
        else:
            root_px, root_pz = 0.0, 0.0
            if "Hips" in state.bone_cache:
                hip_global = FBVector3d()
                state.bone_cache["Hips"].GetVector(hip_global, FBModelTransformationType.kModelTranslation, True)
                root_px = -hip_global[0] / 100.0
                root_pz =  hip_global[2] / 100.0
                
            root_px *= state.hip_scale_x
            root_pz *= state.hip_scale_z

            state.sock.sendto(
                encode_bone_msg("/VMC/Ext/Root/Pos","root",root_px,0.0,root_pz,0,0,0,1), target)
            sent += 1

            for bone_name, model in state.bone_cache.items():
                try:
                    px,py,pz,qx,qy,qz,qw = mb_to_vmc(model, state)
                    if bone_name == "Hips":
                        px = 0.0
                        pz = 0.0
                    else:
                        px, py, pz = 0.0, 0.0, 0.0
                        
                    state.sock.sendto(
                        encode_bone_msg("/VMC/Ext/Bone/Pos",bone_name,px,py,pz,qx,qy,qz,qw), target)
                    sent += 1
                except: pass

        state.frame_count += 1
        
    act_id = current_actor()
    act_state = g_sender_states[act_id]
    if act_state.is_connected:
        if act_id == 4:
            g_ui["lbl_status"].Caption = "Props Sending: {} items @ {}fps -> {}".format(
                len(act_state.prop_data), act_state.fps_limit, act_state.target_ip)
        else:
            g_ui["lbl_status"].Caption = "Actor {} Sending: {} msgs @ {}fps -> {}:{}".format(
                act_id, len(act_state.bone_cache) + 1, act_state.fps_limit,
                act_state.target_ip, act_state.target_port)
    else:
        g_ui["lbl_status"].Caption = "Actor {} Status: Stopped".format(act_id)

# ── Hip Scale ────────────────────────────────────────────────────────────────
def OnHipScaleXChange(control, event):
    act_id = current_actor()
    g_sender_states[act_id].hip_scale_x = control.Value

def OnHipScaleZChange(control, event):
    act_id = current_actor()
    g_sender_states[act_id].hip_scale_z = control.Value

# ── Button Callbacks ──────────────────────────────────────────────────────────
def OnScanClick(control, event):
    act_id = current_actor()
    root_model, bones = scan_vmc_bones(act_id)
    total = (1 if root_model else 0) + len(bones)
    if total == 0:
        FBMessageBox("Scan Result",
            "No VMC_ bones found for Actor {}.\n\n".format(act_id) +
            "Please click 'Generate Skeleton'.", "OK")
        return
    lines = ["Found {} VMC_ model(s) for Actor {}:".format(total, act_id)]
    if root_model: lines.append("  [Root]  VMC{}:VMC_Root".format(act_id))
    for b in sorted(bones.keys()): lines.append("  [Bone]  VMC{}:VMC_".format(act_id) + b)
    FBMessageBox("Scan Result", "\n".join(lines), "OK")

# ── Props Manager ─────────────────────────────────────────────────────────────
def _refresh_props_list():
    if "list_props" not in g_ui: return
    g_ui["list_props"].Items.removeAll()
    state = g_sender_states[4]
    for item in state.prop_data:
        try:
            m = item["model"]
            p = item["port"]
            g_ui["list_props"].Items.append("[{}] {}".format(p, m.Name))
        except: pass

def OnAddPropsClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    if act_id != 4: return
    
    try:
        target_port = int(g_ui["edit_prop_port"].Text.strip())
    except:
        FBMessageBox("Error", "Invalid Port number.", "OK")
        return
        
    selected = [m for m in FBSystem().Scene.Components if isinstance(m, FBModel) and m.Selected]
    added = 0
    for m in selected:
        # Check if already exists (can update port if needed)
        exists = False
        for item in state.prop_data:
            if item["model"] == m:
                item["port"] = target_port
                exists = True
                break
        if not exists:
            state.prop_data.append({"model": m, "port": target_port})
            added += 1
            
    _refresh_props_list()
    if added > 0: FBMessageBox("Props Added", "Added {} new prop(s) at port {}.".format(added, target_port), "OK")
    else: FBMessageBox("Props Updated", "Updated port for selected props to {}.".format(target_port), "OK")

def OnClearPropsClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    if act_id != 4: return
    state.prop_data = []
    _refresh_props_list()

def OnActorChange(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    g_ui["edit_ip"].Text = state.target_ip
    g_ui["edit_port"].Value = state.target_port
    g_ui["slider_hip_x"].Value = state.hip_scale_x
    g_ui["slider_hip_z"].Value = state.hip_scale_z
    set_fps(state.fps_limit)
    
    is_props = (act_id == 4)
    _rebuild_layout()

    if is_props:
        _refresh_props_list()

    if state.is_connected:
        g_ui["btn_stream"].Caption = "Stop Sending"
        g_ui["lbl_status"].Caption = "Actor {} Sending to {}:{}".format(
            act_id, state.target_ip, state.target_port)
    else:
        g_ui["btn_stream"].Caption = "Start Sending"
        g_ui["lbl_status"].Caption = "Actor {} Status: Stopped".format(act_id)

def OnToggleSendClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    
    if not state.is_connected:
        # Start Sending logic
        if act_id == 4:
            if not state.prop_data:
                FBMessageBox("Warning", "No props added to send. Add some models first.", "OK")
                return
            state.bone_cache = {}
            state.root_cache = None
        else:
            root_model, bones = scan_vmc_bones(act_id)
            if not root_model and not bones:
                FBMessageBox("Warning",
                    "No VMC_ skeleton found for Actor {}!\n".format(act_id) +
                    "Please use 'Generate Skeleton' first.", "OK")
                return
            state.root_cache  = root_model
            state.bone_cache  = bones
            
        try:
            state.target_ip   = g_ui["edit_ip"].Text
            state.target_port = int(g_ui["edit_port"].Text.strip())
            state.sock        = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            state.is_connected = True
            state.frame_count = 0
            state.last_send_time = 0.0

            g_ui["btn_stream"].Caption = "Stop Sending"
            if act_id == 4:
                g_ui["lbl_status"].Caption = "Props Sending to {} (Multiple Ports)".format(state.target_ip)
            else:
                g_ui["lbl_status"].Caption = "Actor {} Sending to {}:{} ({} bones)".format(
                    act_id, state.target_ip, state.target_port, len(state.bone_cache))

            fb_sys = FBSystem()
            fb_sys.OnUIIdle.Remove(OnSendUIIdle)
            fb_sys.OnUIIdle.Add(OnSendUIIdle)
            import sys as python_sys
            python_sys.mobu2vmc_multiactor_idle_func = OnSendUIIdle
        except Exception as e:
            g_ui["lbl_status"].Caption = "Error: " + str(e)
    else:
        # Stop Sending logic
        state.is_connected = False
        if state.sock:
            try: state.sock.close()
            except: pass
            state.sock = None
            
        g_ui["btn_stream"].Caption = "Start Sending"
        g_ui["lbl_status"].Caption = "Actor {} Status: Stopped".format(act_id)

# ── Match Proportions ───────────────────────────────────────────────────────────
def OnRefreshCharListClick(control, event):
    """Scan scene for HIK characters and populate the dropdown."""
    if "list_char_source" not in g_ui: return
    g_ui["list_char_source"].Items.removeAll()
    for char in FBSystem().Scene.Characters:
        try:
            # Check if HipsLink property exists AND has an actual bone assigned
            for prop in char.PropertyList:
                if prop.Name == "HipsLink":
                    if len(prop) > 0:  # Must have a bone actually assigned
                        g_ui["list_char_source"].Items.append(char.Name)
                    break
        except: pass
    if len(g_ui["list_char_source"].Items) > 0:
        g_ui["list_char_source"].ItemIndex = 0

def OnMatchProportionsClick(control, event):
    """Read Global bone positions from selected HIK character and apply to VMC skeleton."""
    # Auto-refresh character list before matching
    OnRefreshCharListClick(None, None)

    act_id = current_actor()
    prefix = "VMC{}:".format(act_id)
    
    # Get selected character name from list
    if "list_char_source" not in g_ui: return
    idx = g_ui["list_char_source"].ItemIndex
    if idx < 0 or idx >= len(g_ui["list_char_source"].Items):
        FBMessageBox("Error", "Please select a source character first.", "OK")
        return
    char_name = g_ui["list_char_source"].Items[idx]
    
    # Find the FBCharacter object
    source_char = None
    for char in FBSystem().Scene.Characters:
        if char.Name == char_name:
            source_char = char
            break
    if source_char is None:
        FBMessageBox("Error", "Character '{}' not found in scene.".format(char_name), "OK")
        return
    
    # Build a map of Link property name -> model from source character
    link_to_model = {}
    for prop in source_char.PropertyList:
        if not prop.Name.endswith("Link"): continue
        try:
            # FBPropertyListObject supports index access
            for i in range(len(prop)):
                obj = prop[i]
                if obj and isinstance(obj, FBModel):
                    link_to_model[prop.Name] = obj
                    break
        except: pass
    
    if not link_to_model:
        FBMessageBox("Error", "Could not read bone data from character '{}'.\nMake sure the character is fully characterized with bones assigned.".format(char_name), "OK")
        return
    
    # Build VMC bone lookup dict
    vmc_bones = {}
    for comp in FBSystem().Scene.Components:
        try:
            if not isinstance(comp, FBModel): continue
            name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
            if name.startswith(prefix + "VMC_"):
                bn = name[len(prefix + "VMC_"):]
                vmc_bones[bn] = comp
        except: pass
    
    # Also handle VMC_Root
    root_bone = None
    for comp in FBSystem().Scene.Components:
        try:
            if not isinstance(comp, FBModel): continue
            name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
            if name == "{}VMC_Root".format(prefix):
                root_bone = comp
                break
        except: pass
    
    if not vmc_bones and not root_bone:
        FBMessageBox("Error",
            "No VMC skeleton found for Actor {}.\nPlease Generate Skeleton first.".format(act_id), "OK")
        return
    
    # Step 1: Delete VMC HIK Character if it exists (so bones are free to move)
    vmc_char_name = "{}VMC_HIK_Character".format(prefix)
    for char in list(FBSystem().Scene.Characters):
        if char.Name == vmc_char_name or (hasattr(char, "LongName") and char.LongName == vmc_char_name):
            try:
                char.SetCharacterizeOn(False)
                char.FBDelete()
            except: pass
    FBSystem().Scene.Evaluate()
    
    # Step 2: Move VMC bones to match source character Global positions (no HIK lock now)
    matched = 0
    for vmc_bone_name, vmc_model in vmc_bones.items():
        hik_link_name = HIK_MAPPING.get(vmc_bone_name)
        if not hik_link_name: continue
        src_model = link_to_model.get(hik_link_name)
        if not src_model: continue
        try:
            src_pos = FBVector3d()
            src_model.GetVector(src_pos, FBModelTransformationType.kModelTranslation, True)
            vmc_model.SetVector(src_pos, FBModelTransformationType.kModelTranslation, True)
            matched += 1
        except: pass
    
    # Root stays at origin, do not move it
    FBSystem().Scene.Evaluate()
    
    FBMessageBox("Done",
        "Matched {} bones to '{}'.\nNow click 'Characterize HIK' to lock the new proportions.".format(matched, char_name), "OK")

# ── Delete Skeleton ───────────────────────────────────────────────────────────
def OnDeleteSkeletonClick(control, event):
    act_id = current_actor()
    state = g_sender_states[act_id]
    
    if state.is_connected:
        OnStopSendClick(None, None)

    prefix = "VMC{}:".format(act_id)
    char_name = prefix + "VMC_HIK_Character"
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name or (hasattr(c, "LongName") and c.LongName == char_name):
            try: c.SetCharacterizeOn(False)
            except: pass
            try: c.FBDelete()
            except: pass

    deleted = 0
    for comp in list(FBSystem().Scene.Components):
        try:
            name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
            if isinstance(comp, FBModel) and name and name.startswith(prefix):
                comp.FBDelete()
                deleted += 1
        except: pass

    state.bone_cache.clear()
    state.root_cache = None
    FBMessageBox("Done", "Deleted Actor {} VMC skeleton ({} objects).".format(act_id, deleted), "OK")

# ── Match Proportions from VRM File ───────────────────────────────────────────
def OnBrowseVRMClick(control, event):
    """Open file browser to select a .vrm file."""
    popup = FBFilePopup()
    popup.Caption = "Select VRM File"
    popup.Filter  = "*.vrm"
    popup.Style   = FBFilePopupStyle.kFBFilePopupOpen
    if popup.Execute():
        if "edit_vrm_path" in g_ui:
            g_ui["edit_vrm_path"].Text = popup.FullFilename

def OnMatchFromVRMClick(control, event):
    """Parse a VRM file and match VMC_ skeleton proportions to it."""
    act_id   = current_actor()
    vrm_path = g_ui["edit_vrm_path"].Text.strip() if "edit_vrm_path" in g_ui else ""
    if not vrm_path:
        FBMessageBox("Error", "Please browse and select a VRM file first.", "OK")
        return
    try:
        gltf, _ = _vrm_parse_glb(vrm_path)
    except Exception as e:
        FBMessageBox("Error", "Failed to parse VRM file:\n" + str(e), "OK")
        return

    humanoid, version = _vrm_get_humanoid(gltf)
    if not humanoid:
        FBMessageBox("Error",
            "No humanoid bone data found.\nDetected version: " + version, "OK")
        return

    nodes    = gltf.get('nodes', [])
    world_pos = _vrm_world_positions(nodes)

    # Build target positions in MB space (VRM metres * 100 = MB centimetres)
    # VRM 0.x: character faces -Z → LEFT is at -X, FRONT is -Z
    #   Equivalent to Y-axis 180° rotation vs VMC skeleton (facing +Z, LEFT at +X)
    #   Fix: negate both X and Z
    # VRM 1.0: character faces +Z, LEFT at +X → matches VMC, no flip needed
    axis_sign = -1.0 if version == '0.x' else 1.0
    vrm_positions = {}
    for vmc_name, node_idx in humanoid.items():
        if node_idx < len(world_pos):
            wx, wy, wz = world_pos[node_idx]
            vrm_positions[vmc_name] = FBVector3d(
                wx * axis_sign * 100.0,
                wy * 100.0,
                wz * axis_sign * 100.0)

    if not vrm_positions:
        FBMessageBox("Error", "Could not compute bone positions from VRM.", "OK")
        return

    prefix = "VMC{}:".format(act_id)

    # Build VMC bone lookup dict from scene
    vmc_bones = {}
    for comp in FBSystem().Scene.Components:
        try:
            if not isinstance(comp, FBModel): continue
            name = comp.LongName if hasattr(comp, "LongName") and comp.LongName else comp.Name
            if name.startswith(prefix + "VMC_"):
                bn = name[len(prefix + "VMC_"):]
                vmc_bones[bn] = comp
        except: continue

    if not vmc_bones:
        FBMessageBox("Error",
            "No VMC_ skeleton found for Actor {}.\nPlease Generate Skeleton first.".format(act_id), "OK")
        return

    # Step 1: Delete HIK character so bones are free to move
    vmc_char_name = "{}VMC_HIK_Character".format(prefix)
    for char in list(FBSystem().Scene.Characters):
        cname = char.LongName if hasattr(char, "LongName") and char.LongName else char.Name
        if cname == vmc_char_name:
            try:
                char.SetCharacterizeOn(False)
                char.FBDelete()
            except: pass
    FBSystem().Scene.Evaluate()

    # Step 2: Move VMC bones to VRM world positions
    matched = 0
    for vmc_name, new_pos in vrm_positions.items():
        if vmc_name in vmc_bones:
            try:
                vmc_bones[vmc_name].SetVector(
                    new_pos, FBModelTransformationType.kModelTranslation, True)
                matched += 1
            except: pass
    FBSystem().Scene.Evaluate()

    FBMessageBox("Done",
        "VRM {} — Matched {} bones for Actor {}.\n"
        "Click 'Characterize HIK' to lock the new proportions.".format(
            version, matched, act_id), "OK")

# ── UI ────────────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 240
    tool.StartSizeY = 880

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main","main", x, y, w, h)

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
    g_ui["list_actor"].Items.append("Props / Trackers")
    g_ui["list_actor"].ItemIndex = 0
    g_ui["list_actor"].OnChange.Add(OnActorChange)
    g_ui["lyt_actor"].Add(g_ui["lbl_actor"], 75)
    g_ui["lyt_actor"].Add(g_ui["list_actor"], 140)

    def hdr(text, key=None):
        lbl = FBLabel()
        lbl.Caption = "--- " + text + " ---"
        lbl.Justify = FBTextJustify.kFBTextJustifyCenter
        if key: g_ui[key] = lbl
        return lbl

    def btn(caption, cb):
        b = FBButton(); b.Caption = caption; b.OnClick.Add(cb); return b

    g_ui["hdr_skel"] = hdr("SKELETON")
    g_ui["btn_scan"]  = btn("Scan VMC Bones",           OnScanClick)
    g_ui["btn_gen"]   = btn("Generate Skeleton",        OnGenerateSkeletonClick)
    
    g_ui["hdr_char"] = hdr("CHARACTERIZE")
    g_ui["btn_char"]  = btn("Characterize HIK",         OnCharacterizeClick)
    
    g_ui["hdr_del"] = hdr("CLEAN SKELETON")
    g_ui["btn_del"]   = btn("Delete Skeleton",          OnDeleteSkeletonClick)
    
    g_ui["hdr_match"] = hdr("MATCH PROPORTIONS")
    g_ui["btn_match"] = btn("Match Proportions",        OnMatchProportionsClick)
    
    # HIK Character source list
    g_ui["lyt_char_src"] = FBHBoxLayout()
    g_ui["list_char_source"] = FBList()
    g_ui["btn_refresh_chars"] = btn("Refresh", OnRefreshCharListClick)
    g_ui["lyt_char_src"].Add(g_ui["list_char_source"], 150)
    g_ui["lyt_char_src"].Add(g_ui["btn_refresh_chars"], 65)

    g_ui["hdr_vrm"] = hdr("MATCH FROM VRM FILE")
    # VRM File browse
    g_ui["lyt_vrm_path"] = FBHBoxLayout()
    g_ui["edit_vrm_path"] = FBEdit(); g_ui["edit_vrm_path"].Text = ""
    g_ui["btn_browse_vrm"] = btn("Browse...", OnBrowseVRMClick)
    g_ui["lyt_vrm_path"].Add(g_ui["edit_vrm_path"],   145)
    g_ui["lyt_vrm_path"].Add(g_ui["btn_browse_vrm"],   70)
    g_ui["btn_match_vrm"] = btn("Match from VRM File", OnMatchFromVRMClick)

    g_ui["hdr_props"] = hdr("TRACKERS / PROPS")
    # ── PROPS Manager UI
    g_ui["lyt_props_port"] = FBHBoxLayout()
    g_ui["lbl_prop_port"] = FBLabel(); g_ui["lbl_prop_port"].Caption = "Initial Port:"
    g_ui["edit_prop_port"] = FBEdit(); g_ui["edit_prop_port"].Text = "39542"
    g_ui["lyt_props_port"].Add(g_ui["lbl_prop_port"], 75)
    g_ui["lyt_props_port"].Add(g_ui["edit_prop_port"], 140)

    g_ui["lyt_props_btns"] = FBHBoxLayout()
    g_ui["btn_add_props"] = btn("Add / Update Selected", OnAddPropsClick)
    g_ui["btn_clear_props"] = btn("Clear Props", OnClearPropsClick)
    g_ui["lyt_props_btns"].Add(g_ui["btn_add_props"], 130)
    g_ui["lyt_props_btns"].Add(g_ui["btn_clear_props"], 90)
    g_ui["list_props"] = FBList()

    # ── Send Target
    g_ui["lyt_ip"]   = FBHBoxLayout()
    g_ui["lbl_ip"]   = FBLabel();     g_ui["lbl_ip"].Caption  = "Target IP:"
    g_ui["edit_ip"]  = FBEdit();      g_ui["edit_ip"].Text    = "127.0.0.1"
    g_ui["lyt_ip"].Add(g_ui["lbl_ip"], 75); g_ui["lyt_ip"].Add(g_ui["edit_ip"], 140)

    g_ui["lyt_port"]  = FBHBoxLayout()
    g_ui["lbl_port"]  = FBLabel();      g_ui["lbl_port"].Caption  = "UDP Port:"
    g_ui["edit_port"] = FBEdit(); g_ui["edit_port"].Text    = "39539"
    g_ui["lyt_port"].Add(g_ui["lbl_port"], 75); g_ui["lyt_port"].Add(g_ui["edit_port"], 140)

    # ── FPS selector
    g_ui["lyt_fps"]   = FBHBoxLayout()
    g_ui["lbl_fps"]   = FBLabel(); g_ui["lbl_fps"].Caption = "Send FPS:"
    g_ui["btn_fps24"] = FBButton(); g_ui["btn_fps24"].Caption = " 24 "; g_ui["btn_fps24"].OnClick.Add(OnFPS24Click)
    g_ui["btn_fps30"] = FBButton(); g_ui["btn_fps30"].Caption = "[30]"; g_ui["btn_fps30"].OnClick.Add(OnFPS30Click)
    g_ui["btn_fps60"] = FBButton(); g_ui["btn_fps60"].Caption = " 60 "; g_ui["btn_fps60"].OnClick.Add(OnFPS60Click)
    g_ui["lyt_fps"].Add(g_ui["lbl_fps"],   75)
    g_ui["lyt_fps"].Add(g_ui["btn_fps24"], 45)
    g_ui["lyt_fps"].Add(g_ui["btn_fps30"], 45)
    g_ui["lyt_fps"].Add(g_ui["btn_fps60"], 45)

    # ── Global Position Weight X
    g_ui["lyt_hip_x"]     = FBHBoxLayout()
    g_ui["lbl_hip_x"]     = FBLabel(); g_ui["lbl_hip_x"].Caption = "Global Weight X:"
    g_ui["slider_hip_x"]  = FBEditNumber()
    g_ui["slider_hip_x"].Min = 0.0
    g_ui["slider_hip_x"].Max = 2.0
    g_ui["slider_hip_x"].Value = g_sender_states[1].hip_scale_x
    g_ui["slider_hip_x"].Precision = 2
    g_ui["slider_hip_x"].OnChange.Add(OnHipScaleXChange)
    g_ui["lyt_hip_x"].Add(g_ui["lbl_hip_x"],     100)
    g_ui["lyt_hip_x"].Add(g_ui["slider_hip_x"],  115)

    # ── Global Position Weight Z
    g_ui["lyt_hip_z"]     = FBHBoxLayout()
    g_ui["lbl_hip_z"]     = FBLabel(); g_ui["lbl_hip_z"].Caption = "Global Weight Z:"
    g_ui["slider_hip_z"]  = FBEditNumber()
    g_ui["slider_hip_z"].Min = 0.0
    g_ui["slider_hip_z"].Max = 2.0
    g_ui["slider_hip_z"].Value = g_sender_states[1].hip_scale_z
    g_ui["slider_hip_z"].Precision = 2
    g_ui["slider_hip_z"].OnChange.Add(OnHipScaleZChange)
    g_ui["lyt_hip_z"].Add(g_ui["lbl_hip_z"],     100)
    g_ui["lyt_hip_z"].Add(g_ui["slider_hip_z"],  115)

    # ── Start / Stop (single button)
    g_ui["btn_stream"] = btn("Start Sending", OnToggleSendClick)

    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Status: Stopped"

    g_ui["hdr_send"] = hdr("SEND & CONTROL")
    
    # Store tool reference for dynamic rebuilds
    g_ui["tool"] = tool
    
    # Auto-populate character list on open
    OnRefreshCharListClick(None, None)
    
    # Initial layout build
    _rebuild_layout()

def _rebuild_layout():
    act_id = current_actor()
    is_props = (act_id == 4)
    
    lay = FBVBoxLayout()
    g_ui["main_layout"] = lay
    g_ui["tool"].SetControl("main", lay)
    
    lay.Add(g_ui["lyt_actor"], 30)
    
    if is_props:
        lay.Add(g_ui["hdr_props"], 25)
        lay.Add(g_ui["lyt_props_port"], 30)
        lay.Add(g_ui["lyt_props_btns"], 30)
        lay.Add(g_ui["list_props"], 60) # Reduced height
    else:
        lay.Add(g_ui["hdr_skel"], 25)
        lay.Add(g_ui["btn_scan"], 35)
        lay.Add(g_ui["btn_gen"], 35)
        lay.Add(g_ui["hdr_match"], 25)
        lay.Add(g_ui["lyt_char_src"], 30)
        lay.Add(g_ui["btn_match"], 35)
        lay.Add(g_ui["hdr_vrm"], 25)
        lay.Add(g_ui["lyt_vrm_path"], 30)
        lay.Add(g_ui["btn_match_vrm"], 35)
        lay.Add(g_ui["hdr_char"], 25)
        lay.Add(g_ui["btn_char"], 35)
        lay.Add(g_ui["hdr_del"], 25)
        lay.Add(g_ui["btn_del"], 35)
        
    lay.Add(g_ui["hdr_send"], 25)
    lay.Add(g_ui["lyt_ip"], 30)
    
    if not is_props:
        lay.Add(g_ui["lyt_port"], 30) # Hide global UDP port in props mode
        
    lay.Add(g_ui["lyt_fps"], 30)
    
    if not is_props:
        lay.Add(g_ui["lyt_hip_x"], 30)
        lay.Add(g_ui["lyt_hip_z"], 30)
        
    lay.Add(g_ui["btn_stream"], 35)
    lay.Add(g_ui["lbl_status"], 30)

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
