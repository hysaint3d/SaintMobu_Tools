"""
MobuSkeleton_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generate a standard T-Pose skeleton (VMC or HIK naming), match its proportions
to any HIK characterized character, and characterize as HIK.

Workflow:
  1. Set namespace & height → Generate Skeleton
  2. (Optional) Select source HIK character → Match Proportions
  3. Characterize HIK

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pyfbsdk import *
from pyfbsdk_additions import *
import sys

# ── Bone tables ───────────────────────────────────────────────────────────────
BASE_H = 170.0

# Keyed by VMC name; (x, y, z) in cm for 170 cm character
BONE_POS = {
    "Hips":(0,96,0),"Spine":(0,104,0),"Chest":(0,116,0),"UpperChest":(0,126,0),
    "Neck":(0,140,0),"Head":(0,150,0),
    "LeftShoulder":(7,137,0),"LeftUpperArm":(18,137,0),"LeftLowerArm":(42,137,0),"LeftHand":(64,137,0),
    "RightShoulder":(-7,137,0),"RightUpperArm":(-18,137,0),"RightLowerArm":(-42,137,0),"RightHand":(-64,137,0),
    "LeftUpperLeg":(9,96,0),"LeftLowerLeg":(9,52,0),"LeftFoot":(9,8,0),"LeftToes":(9,0,8),
    "RightUpperLeg":(-9,96,0),"RightLowerLeg":(-9,52,0),"RightFoot":(-9,8,0),"RightToes":(-9,0,8),
    "LeftThumbProximal":(66,137,3),"LeftThumbIntermediate":(68,137,5),"LeftThumbDistal":(70,137,7),
    "LeftIndexProximal":(68,137,2),"LeftIndexIntermediate":(72,137,2),"LeftIndexDistal":(75,137,2),
    "LeftMiddleProximal":(68,137,0),"LeftMiddleIntermediate":(72,137,0),"LeftMiddleDistal":(75,137,0),
    "LeftRingProximal":(68,137,-2),"LeftRingIntermediate":(72,137,-2),"LeftRingDistal":(75,137,-2),
    "LeftLittleProximal":(67,137,-4),"LeftLittleIntermediate":(70,137,-4),"LeftLittleDistal":(73,137,-4),
    "RightThumbProximal":(-66,137,3),"RightThumbIntermediate":(-68,137,5),"RightThumbDistal":(-70,137,7),
    "RightIndexProximal":(-68,137,2),"RightIndexIntermediate":(-72,137,2),"RightIndexDistal":(-75,137,2),
    "RightMiddleProximal":(-68,137,0),"RightMiddleIntermediate":(-72,137,0),"RightMiddleDistal":(-75,137,0),
    "RightRingProximal":(-68,137,-2),"RightRingIntermediate":(-72,137,-2),"RightRingDistal":(-75,137,-2),
    "RightLittleProximal":(-67,137,-4),"RightLittleIntermediate":(-70,137,-4),"RightLittleDistal":(-73,137,-4),
}

HIERARCHY = {
    "Hips":None,"Spine":"Hips","Chest":"Spine","UpperChest":"Chest","Neck":"UpperChest","Head":"Neck",
    "LeftShoulder":"UpperChest","LeftUpperArm":"LeftShoulder","LeftLowerArm":"LeftUpperArm","LeftHand":"LeftLowerArm",
    "RightShoulder":"UpperChest","RightUpperArm":"RightShoulder","RightLowerArm":"RightUpperArm","RightHand":"RightLowerArm",
    "LeftUpperLeg":"Hips","LeftLowerLeg":"LeftUpperLeg","LeftFoot":"LeftLowerLeg","LeftToes":"LeftFoot",
    "RightUpperLeg":"Hips","RightLowerLeg":"RightUpperLeg","RightFoot":"RightLowerLeg","RightToes":"RightFoot",
    "LeftThumbProximal":"LeftHand","LeftThumbIntermediate":"LeftThumbProximal","LeftThumbDistal":"LeftThumbIntermediate",
    "LeftIndexProximal":"LeftHand","LeftIndexIntermediate":"LeftIndexProximal","LeftIndexDistal":"LeftIndexIntermediate",
    "LeftMiddleProximal":"LeftHand","LeftMiddleIntermediate":"LeftMiddleProximal","LeftMiddleDistal":"LeftMiddleIntermediate",
    "LeftRingProximal":"LeftHand","LeftRingIntermediate":"LeftRingProximal","LeftRingDistal":"LeftRingIntermediate",
    "LeftLittleProximal":"LeftHand","LeftLittleIntermediate":"LeftLittleProximal","LeftLittleDistal":"LeftLittleIntermediate",
    "RightThumbProximal":"RightHand","RightThumbIntermediate":"RightThumbProximal","RightThumbDistal":"RightThumbIntermediate",
    "RightIndexProximal":"RightHand","RightIndexIntermediate":"RightIndexProximal","RightIndexDistal":"RightIndexIntermediate",
    "RightMiddleProximal":"RightHand","RightMiddleIntermediate":"RightMiddleProximal","RightMiddleDistal":"RightMiddleIntermediate",
    "RightRingProximal":"RightHand","RightRingIntermediate":"RightRingProximal","RightRingDistal":"RightRingIntermediate",
    "RightLittleProximal":"RightHand","RightLittleIntermediate":"RightLittleProximal","RightLittleDistal":"RightLittleIntermediate",
}

# VMC bone name -> HIK link property name
HIK_LINK = {
    "Hips":"HipsLink","Spine":"SpineLink","Chest":"Spine1Link","UpperChest":"Spine2Link",
    "Neck":"NeckLink","Head":"HeadLink",
    "LeftShoulder":"LeftShoulderLink","LeftUpperArm":"LeftArmLink","LeftLowerArm":"LeftForeArmLink","LeftHand":"LeftHandLink",
    "RightShoulder":"RightShoulderLink","RightUpperArm":"RightArmLink","RightLowerArm":"RightForeArmLink","RightHand":"RightHandLink",
    "LeftUpperLeg":"LeftUpLegLink","LeftLowerLeg":"LeftLegLink","LeftFoot":"LeftFootLink","LeftToes":"LeftToeBaseLink",
    "RightUpperLeg":"RightUpLegLink","RightLowerLeg":"RightLegLink","RightFoot":"RightFootLink","RightToes":"RightToeBaseLink",
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

# VMC name -> Standard HIK bone name (link property minus "Link")
HIK_STD = {k: v.replace("Link","") for k, v in HIK_LINK.items()}
# HIK standard root name
HIK_ROOT = "Reference"

# VMC bone name -> UE Mannequin bone name
UE_NAME = {
    "Hips":"pelvis","Spine":"spine_01","Chest":"spine_02","UpperChest":"spine_03",
    "Neck":"neck_01","Head":"head",
    "LeftShoulder":"clavicle_l","LeftUpperArm":"upperarm_l","LeftLowerArm":"lowerarm_l","LeftHand":"hand_l",
    "RightShoulder":"clavicle_r","RightUpperArm":"upperarm_r","RightLowerArm":"lowerarm_r","RightHand":"hand_r",
    "LeftUpperLeg":"thigh_l","LeftLowerLeg":"calf_l","LeftFoot":"foot_l","LeftToes":"ball_l",
    "RightUpperLeg":"thigh_r","RightLowerLeg":"calf_r","RightFoot":"foot_r","RightToes":"ball_r",
    "LeftThumbProximal":"thumb_01_l","LeftThumbIntermediate":"thumb_02_l","LeftThumbDistal":"thumb_03_l",
    "LeftIndexProximal":"index_01_l","LeftIndexIntermediate":"index_02_l","LeftIndexDistal":"index_03_l",
    "LeftMiddleProximal":"middle_01_l","LeftMiddleIntermediate":"middle_02_l","LeftMiddleDistal":"middle_03_l",
    "LeftRingProximal":"ring_01_l","LeftRingIntermediate":"ring_02_l","LeftRingDistal":"ring_03_l",
    "LeftLittleProximal":"pinky_01_l","LeftLittleIntermediate":"pinky_02_l","LeftLittleDistal":"pinky_03_l",
    "RightThumbProximal":"thumb_01_r","RightThumbIntermediate":"thumb_02_r","RightThumbDistal":"thumb_03_r",
    "RightIndexProximal":"index_01_r","RightIndexIntermediate":"index_02_r","RightIndexDistal":"index_03_r",
    "RightMiddleProximal":"middle_01_r","RightMiddleIntermediate":"middle_02_r","RightMiddleDistal":"middle_03_r",
    "RightRingProximal":"ring_01_r","RightRingIntermediate":"ring_02_r","RightRingDistal":"ring_03_r",
    "RightLittleProximal":"pinky_01_r","RightLittleIntermediate":"pinky_02_r","RightLittleDistal":"pinky_03_r",
}

# VMC bone name -> VMC bone name (identity, for clarity)
VMC_NAME = {k: k for k in BONE_POS}

# ── State ─────────────────────────────────────────────────────────────────────
if not hasattr(sys, "mobu_skeleton_toolkit_state"):
    sys.mobu_skeleton_toolkit_state = {"bones":{}, "root":None, "mode":"vmc", "ns":""}
g_st  = sys.mobu_skeleton_toolkit_state
g_ui  = {}

# ── Helpers ───────────────────────────────────────────────────────────────────
def hdr(text):
    l = FBLabel(); l.Caption = "--- {} ---".format(text)
    l.Justify = FBTextJustify.kFBTextJustifyCenter; return l

def status(msg):
    try: g_ui["lbl_status"].Caption = msg
    except: pass

def get_ns():
    raw = g_ui["edit_ns"].Text.strip()
    if raw and not raw.endswith(":"): raw += ":"
    return raw

def get_mode():
    idx = g_ui["list_mode"].ItemIndex
    if idx == 0: return "hik"
    if idx == 1: return "vmc"
    return "ue"

def bone_scene_name(vmc_key, mode, ns):
    """Full scene name for a bone."""
    if mode == "vmc":
        return ns + "VMC_" + vmc_key
    elif mode == "ue":
        return ns + "UE_" + UE_NAME.get(vmc_key, vmc_key)
    else:
        return ns + HIK_STD[vmc_key]

def root_scene_name(mode, ns):
    if mode == "vmc": return ns + "VMC_Root"
    if mode == "ue":  return ns + "UE_root"
    return ns + HIK_ROOT

def get_characterized_chars():
    result = []
    for char in FBSystem().Scene.Characters:
        try:
            for prop in char.PropertyList:
                if prop.Name == "HipsLink":
                    result.append(char.Name); break
        except: pass
    return result

def get_link_model_map(char):
    m = {}
    for prop in char.PropertyList:
        if not prop.Name.endswith("Link"): continue
        try:
            for i in range(len(prop)):
                obj = prop[i]
                if obj and isinstance(obj, FBModel):
                    m[prop.Name] = obj; break
        except: pass
    return m

def delete_hik_char_for(mode, ns):
    if mode == "vmc":   char_name = ns + "VMC_HIK_Character"
    elif mode == "ue":  char_name = ns + "UE_HIK_Character"
    else:               char_name = ns + "HIK_Character"
    for char in list(FBSystem().Scene.Characters):
        n = getattr(char, "LongName", None) or char.Name
        if n == char_name:
            try: char.SetCharacterizeOn(False); char.FBDelete()
            except: pass

def delete_bones_with_prefix(prefix):
    """Delete all FBModel objects whose name starts with prefix."""
    deleted = 0
    for comp in list(FBSystem().Scene.Components):
        try:
            if not isinstance(comp, FBModel): continue
            n = getattr(comp, "LongName", None) or comp.Name
            if n and n.startswith(prefix):
                comp.FBDelete(); deleted += 1
        except: pass
    return deleted

# ── Core logic ────────────────────────────────────────────────────────────────
def do_generate():
    mode = get_mode()
    ns   = get_ns()

    try:
        scale = float(g_ui["edit_height"].Text.strip()) / BASE_H
    except:
        scale = 1.0

    root_name = root_scene_name(mode, ns)

    # Auto-delete existing skeleton with same prefix to allow re-generation
    prefix = ns + ("VMC_" if mode == "vmc" else HIK_STD["Hips"].replace("Hips",""))
    # Delete root + all bones sharing the namespace
    all_names = set([root_name] + [bone_scene_name(k, mode, ns) for k in BONE_POS])
    for comp in list(FBSystem().Scene.Components):
        try:
            if not isinstance(comp, FBModel): continue
            n = getattr(comp, "LongName", None) or comp.Name
            if n in all_names:
                comp.FBDelete()
        except: pass

    delete_hik_char_for(mode, ns)
    FBSystem().Scene.Evaluate()

    root = FBModelSkeleton(root_name)
    root.LongName = root_name
    root.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelTranslation, True)
    root.Show = True; root.Visibility = True

    models = {}
    for vmc_key in BONE_POS:
        fname = bone_scene_name(vmc_key, mode, ns)
        m = FBModelSkeleton(fname)
        m.LongName = fname; m.Show = True; m.Visibility = True
        x, y, z = BONE_POS[vmc_key]
        m.SetVector(FBVector3d(x*scale, y*scale, z*scale),
                    FBModelTransformationType.kModelTranslation, True)
        models[vmc_key] = m

    for vmc_key, parent_key in HIERARCHY.items():
        if vmc_key not in models: continue
        models[vmc_key].Parent = root if parent_key is None else models.get(parent_key, root)

    for m in models.values():
        m.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelRotation, False)

    # Hide parent link on Hips to avoid ugly line from Root through the crotch
    if "Hips" in models:
        prop = models["Hips"].PropertyList.Find("Show Parent Link")
        if prop: prop.Data = False

    FBSystem().Scene.Evaluate()

    g_st["bones"] = models
    g_st["root"]  = root
    g_st["mode"]  = mode
    g_st["ns"]    = ns

    # Auto-create HIK character definition (unlocked) so bones stay editable
    if mode == "vmc":  char_name = ns + "VMC_HIK_Character"
    elif mode == "ue": char_name = ns + "UE_HIK_Character"
    else:              char_name = ns + "HIK_Character"
    char = None
    for c in FBSystem().Scene.Characters:
        n = getattr(c, "LongName", None) or c.Name
        if n == char_name:
            char = c; break
    if not char:
        char = FBCharacter(char_name)
    char.SetCharacterizeOn(False)

    for vmc_key, prop_name in HIK_LINK.items():
        if vmc_key not in models: continue
        model = models[vmc_key]
        prop  = char.PropertyList.Find(prop_name)
        if prop:
            prop.removeAll()
            try:    prop.append(model)
            except: prop.insert(model)
        else:
            base = prop_name.replace("Link", "")
            for p in char.PropertyList:
                if p.Name.endswith("Link") and base in p.Name:
                    p.removeAll()
                    try:    p.append(model)
                    except: p.insert(model)
                    break

    if root:
        ref_prop = char.PropertyList.Find("ReferenceLink")
        if ref_prop is None:
            for p in char.PropertyList:
                if "Reference" in p.Name:
                    ref_prop = p; break
        if ref_prop is not None:
            ref_prop.removeAll()
            try: ref_prop.append(root)
            except:
                try: ref_prop.insert(root)
                except: pass

    FBSystem().Scene.Evaluate()

    # Characterize to commit bone assignments (definition will be visible in Navigator)
    ok = char.SetCharacterizeOn(True)
    FBSystem().Scene.Evaluate()

    label = "VMC" if mode == "vmc" else "HIK"
    h_val = g_ui["edit_height"].Text.strip() or "170"
    if ok:
        status("Generated & characterized {} skeleton ({}cm), ns='{}'. Use Match to adjust proportions.".format(label, h_val, ns))
    else:
        status("Generated {} skeleton ({}cm). Characterize manually if needed.".format(label, h_val, ns))

def do_match():
    # Fallback: if bones not in memory (e.g. script was reloaded), scan from scene
    if not g_st["bones"]:
        mode = get_mode(); ns = get_ns()
        root, bones = scan_bones_from_scene(mode, ns)
        if bones:
            g_st["bones"] = bones; g_st["root"] = root
            g_st["mode"] = mode;   g_st["ns"]   = ns
        else:
            FBMessageBox("Error", "No skeleton in memory or scene.\nPlease Generate first.", "OK"); return

    idx = g_ui["list_source"].ItemIndex
    if idx < 0 or idx >= len(g_ui["list_source"].Items):
        FBMessageBox("Error", "Please select a source HIK character.", "OK"); return

    char_name = g_ui["list_source"].Items[idx]
    src_char  = next((c for c in FBSystem().Scene.Characters if c.Name == char_name), None)
    if not src_char:
        FBMessageBox("Error", "Character '{}' not found.".format(char_name), "OK"); return

    link_map = get_link_model_map(src_char)
    if not link_map:
        FBMessageBox("Error", "Could not read bones from '{}'.".format(char_name), "OK"); return

    delete_hik_char_for(g_st["mode"], g_st["ns"])

    matched = 0
    for vmc_key, vmc_model in g_st["bones"].items():
        src = link_map.get(HIK_LINK.get(vmc_key))
        if not src: continue
        try:
            p = FBVector3d()
            src.GetVector(p, FBModelTransformationType.kModelTranslation, True)
            vmc_model.SetVector(p, FBModelTransformationType.kModelTranslation, True)
            matched += 1
        except: pass

    # Root stays at origin
    FBSystem().Scene.Evaluate()

    # Auto-characterize after matching
    do_characterize()

    status("Matched {}/{} bones from '{}' and re-characterized.".format(matched, len(g_st["bones"]), char_name))

def scan_bones_from_scene(mode, ns):
    """Re-scan bone models from scene based on current mode and namespace."""
    bones = {}
    root  = None
    root_name = root_scene_name(mode, ns)
    for comp in FBSystem().Scene.Components:
        try:
            if not isinstance(comp, FBModel): continue
            n = getattr(comp, "LongName", None) or comp.Name
            if n == root_name:
                root = comp
            else:
                for vmc_key in BONE_POS:
                    if n == bone_scene_name(vmc_key, mode, ns):
                        bones[vmc_key] = comp
                        break
        except: pass
    return root, bones

def do_characterize():
    mode = g_st["mode"]; ns = g_st["ns"]

    # Re-scan bones from scene in case g_st is stale
    root, bones = scan_bones_from_scene(mode, ns)
    if not bones:
        FBMessageBox("Error", "No skeleton found in scene.\nNamespace='{}', Mode='{}'.\nPlease Generate first.".format(ns, mode), "OK")
        return

    if mode == "vmc":  char_name = ns + "VMC_HIK_Character"
    elif mode == "ue": char_name = ns + "UE_HIK_Character"
    else:              char_name = ns + "HIK_Character"
    char = None
    for c in FBSystem().Scene.Characters:
        n = getattr(c, "LongName", None) or c.Name
        if n == char_name:
            char = c; break
    if not char:
        char = FBCharacter(char_name)

    char.SetCharacterizeOn(False)

    # Map bones to HIK slots (with fallback search)
    for vmc_key, prop_name in HIK_LINK.items():
        if vmc_key not in bones: continue
        model = bones[vmc_key]
        prop  = char.PropertyList.Find(prop_name)
        if prop:
            prop.removeAll()
            try:    prop.append(model)
            except: prop.insert(model)
        else:
            base = prop_name.replace("Link", "")
            for p in char.PropertyList:
                if p.Name.endswith("Link") and base in p.Name:
                    p.removeAll()
                    try:    p.append(model)
                    except: p.insert(model)
                    break

    # Spine fallback: if no Spine bone, use Chest
    if "Spine" not in bones and "Chest" in bones:
        prop = char.PropertyList.Find("SpineLink")
        if prop:
            prop.removeAll()
            try:    prop.append(bones["Chest"])
            except: prop.insert(bones["Chest"])

    # Force T-Pose rotations to zero
    for m in bones.values():
        m.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelRotation, False)
    if root:
        root.SetVector(FBVector3d(0,0,0), FBModelTransformationType.kModelRotation, False)

    # Map root to HIK Reference node (with fallback search)
    if root:
        ref_prop = char.PropertyList.Find("ReferenceLink")
        if ref_prop is None:
            for p in char.PropertyList:
                if "Reference" in p.Name:
                    ref_prop = p; break
        if ref_prop is not None:
            ref_prop.removeAll()
            try: ref_prop.append(root)
            except:
                try: ref_prop.insert(root)
                except: pass

    FBSystem().Scene.Evaluate()
    ok = char.SetCharacterizeOn(True)
    FBSystem().Scene.Evaluate()

    if ok:
        status("Characterized: {}".format(char_name))
        FBMessageBox("Success", "HIK Characterized!\n{}".format(char_name), "OK")
    else:
        err = char.GetCharacterizeError()
        print("[Skeleton_Generator] Characterize error:", err)
        FBMessageBox("Warning", "Characterization failed.\n{}\nCheck Python Console.".format(err), "OK")

def do_delete():
    mode = get_mode(); ns = get_ns()
    if not ns:
        FBMessageBox("Error", "Please enter a namespace to delete.", "OK"); return
    delete_hik_char_for(mode, ns)
    prefix = ns + ("VMC_" if mode == "vmc" else "")
    root_n = root_scene_name(mode, ns)

    deleted = 0
    targets = set([root_n] + [bone_scene_name(k, mode, ns) for k in BONE_POS])
    for comp in list(FBSystem().Scene.Components):
        try:
            if not isinstance(comp, FBModel): continue
            n = getattr(comp, "LongName", None) or comp.Name
            if n in targets:
                comp.FBDelete(); deleted += 1
        except: pass

    FBSystem().Scene.Evaluate()
    if deleted:
        g_st["bones"] = {}; g_st["root"] = None
    status("Deleted {} objects (ns='{}').".format(deleted, ns))

# ── Callbacks ─────────────────────────────────────────────────────────────────
def OnRefreshClick(c, e):
    g_ui["list_source"].Items.removeAll()
    chars = get_characterized_chars()
    for ch in chars: g_ui["list_source"].Items.append(ch)
    if chars: g_ui["list_source"].ItemIndex = 0
    status("Found {} HIK character(s).".format(len(chars)))

def OnGenerateClick(c, e):    do_generate()
def OnMatchClick(c, e):       do_match()
def OnCharClick(c, e):        do_characterize()
def OnDeleteClick(c, e):      do_delete()

# ── UI ────────────────────────────────────────────────────────────────────────
def PopulateTool(tool):
    tool.StartSizeX = 250
    tool.StartSizeY = 460

    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main","main", x, y, w, h)

    lay = FBVBoxLayout()
    tool.SetControl("main", lay)

    # ── 1. GENERATE ──────────────────────────────────────────────────────────
    lay.Add(hdr("GENERATE CHARACTER"), 25)

    # Skeleton naming mode dropdown
    lyt_mode = FBHBoxLayout()
    lbl_mode = FBLabel(); lbl_mode.Caption = "Skeleton:"
    g_ui["list_mode"] = FBList()
    g_ui["list_mode"].Items.append("MotionBuilder (HIK)")
    g_ui["list_mode"].Items.append("Humanoid (VMC)")
    g_ui["list_mode"].Items.append("Mannequins (UE)")
    g_ui["list_mode"].ItemIndex = 0  # HIK is default
    lyt_mode.Add(lbl_mode, 65)
    lyt_mode.Add(g_ui["list_mode"], 170)
    lay.Add(lyt_mode, 30)

    # Namespace input (manual)
    lyt_ns = FBHBoxLayout()
    lbl_ns = FBLabel(); lbl_ns.Caption = "Namespace:"
    g_ui["edit_ns"] = FBEdit(); g_ui["edit_ns"].Text = "A"
    lyt_ns.Add(lbl_ns,         75)
    lyt_ns.Add(g_ui["edit_ns"], 155)
    lay.Add(lyt_ns, 30)

    # Height
    lyt_h = FBHBoxLayout()
    lbl_h = FBLabel(); lbl_h.Caption = "Height (cm):"
    g_ui["edit_height"] = FBEdit(); g_ui["edit_height"].Text = "170"
    lyt_h.Add(lbl_h, 75)
    lyt_h.Add(g_ui["edit_height"], 70)
    lay.Add(lyt_h, 30)

    btn_gen = FBButton(); btn_gen.Caption = "Generate Character"; btn_gen.OnClick.Add(OnGenerateClick)
    lay.Add(btn_gen, 35)

    btn_del = FBButton(); btn_del.Caption = "Delete Character"; btn_del.OnClick.Add(OnDeleteClick)
    lay.Add(btn_del, 35)

    # ── 2. MATCH ─────────────────────────────────────────────────────────────
    lay.Add(hdr("MATCH PROPORTIONS"), 25)

    lyt_src = FBHBoxLayout()
    g_ui["list_source"] = FBList()
    btn_ref = FBButton(); btn_ref.Caption = "Refresh"; btn_ref.OnClick.Add(OnRefreshClick)
    lyt_src.Add(g_ui["list_source"], 160)
    lyt_src.Add(btn_ref, 65)
    lay.Add(lyt_src, 25)

    btn_match = FBButton(); btn_match.Caption = "Match & Characterize"; btn_match.OnClick.Add(OnMatchClick)
    lay.Add(btn_match, 35)

    # ── Status ────────────────────────────────────────────────────────────────
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Ready."
    lay.Add(g_ui["lbl_status"], 25)

    OnRefreshClick(None, None)

def CreateTool():
    tool = FBCreateUniqueTool("MobuSkeleton_Toolkit")
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        FBMessageBox("Welcome", "MobuSkeleton_Toolkit\n本工具由小聖腦絲與Antigravity協作完成\nhttps://www.facebook.com/hysaint3d.mocap", "OK")
    else:
        print("Error creating Skeleton Generator tool.")

CreateTool()
