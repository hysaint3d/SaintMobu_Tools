# -*- coding: utf-8 -*-
"""
MobuFacial_CtrlRig.py (Slider Panel + Jaw 3D Ctrl + Bake + MasterCtrl)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Workflow:
  1. Select face mesh → [Connect to Selection]
  2. [Generate Master Ctrl] → creates Facial_MasterCtrl null with 20 custom props
  3. Use sliders → values written to MasterCtrl props AND mesh BS simultaneously
  4. [Key Frame] → keys ALL MasterCtrl props (visible in Timeline)
  5. [Sync Pose] → reads MasterCtrl props → updates slider UI (bidirectional)
  6. [Generate Jaw Ctrl] + [START Jaw Live] → 3D jaw control in viewport
  7. Bake functions → scrub timeline and write BS keys frame-by-frame
"""
from pyfbsdk import *
from pyfbsdk_additions import *
import time

# ── Slider Mapping ────────────────────────────────────────────────────────────
# (group_label, [(sub_label, pos_blendshape, neg_blendshape), ...])
GROUPS = [
    # ── Brow (Mirror view: R on left, Inner center, L on right) ─────────────
    ("Brow R", [
        ("Outer", "browOuterUpRight", "browDownRight"),
    ]),
    ("Brow", [
        ("Inner", "browInnerUp", ""),
    ]),
    ("Brow L", [
        ("Outer", "browOuterUpLeft", "browDownLeft"),
    ]),

    # ── Eye (Mirror view: R on left, unified Look center, L on right) ───────
    ("Eye R", [
        ("Open", "eyeWideRight", "eyeBlinkRight,eyeSquintRight"),
    ]),
    # Unified look — one slider drives BOTH eyes simultaneously
    # LR positive = look right  (eyeLookInLeft + eyeLookOutRight)
    # LR negative = look left   (eyeLookOutLeft + eyeLookInRight)
    # UD positive = look up     (eyeLookUpLeft  + eyeLookUpRight)
    # UD negative = look down   (eyeLookDownLeft + eyeLookDownRight)
    ("Look", [
        ("LR", "eyeLookInLeft,eyeLookOutRight",  "eyeLookOutLeft,eyeLookInRight"),
        ("UD", "eyeLookUpLeft,eyeLookUpRight",    "eyeLookDownLeft,eyeLookDownRight"),
    ]),
    ("Eye L", [
        ("Open", "eyeWideLeft", "eyeBlinkLeft,eyeSquintLeft"),
    ]),

    # ── Nose (R on left) ─────────────────────────────────────────────────────
    ("Nose", [
        ("R", "noseSneerRight", ""),
        ("L", "noseSneerLeft",  ""),
    ]),

    # ── Mouth (character Right corner on screen left = mirror view) ──────────
    ("Mouth", [
        ("Upper", "mouthShrugUpper", "mouthRollUpper"),
        ("Lower", "mouthShrugLower", "mouthLowerDownLeft,mouthLowerDownRight"),
        ("Rt",    "mouthSmileRight", "mouthFrownRight"),
        ("Lt",    "mouthSmileLeft",  "mouthFrownLeft"),
    ]),

    # ── Cheek (R on left) ────────────────────────────────────────────────────
    ("Cheek R", [
        ("Up",  "cheekSquintRight", ""),
        ("Puf", "cheekPuff",        ""),
    ]),
    ("Cheek L", [
        ("Up",  "cheekSquintLeft",  ""),
        ("Puf", "cheekPuff",        ""),
    ]),

    # ── Jaw (center) ─────────────────────────────────────────────────────────
    ("Jaw", [
        ("Open", "jawOpen",    ""),
        ("Fwd",  "jawForward", ""),
        ("Side", "jawLeft",    "jawRight"),
    ]),
]

JAW_REF_NAME      = "Ref_Jaw"              # parent null for repositioning
JAW_CTRL_NAME     = "Ctrl_Jaw"             # animated marker (child of Ref_Jaw)
JAW_ALL_BS        = ["jawOpen", "jawForward", "jawLeft", "jawRight"]
JAW_SCALE         = 10.0                   # ctrl range -10..+10 → BS 0..100
MASTER_CTRL_NAME  = "Facial_MasterCtrl"    # null holding all 23 slider props
RIG_ROOT_NAME     = "Facial_Rig_Root"
RELATION_NAME     = "Facial_Relation_Rig"

RIG_SCALE         = 5.0   # 5 units movement = 100% blendshape

SLIDER_W = 40
GRP_PAD  = 8
HDR_H    = 22
LBL_H    = 18

# ── Global ────────────────────────────────────────────────────────────────────
g_target           = None
g_master_ctrl      = None
g_mc_syncing       = False   # True while idle-sync writes to sliders (prevent loop)
g_sliders          = []      # [(FBSlider, prop_name, pos_bs, neg_bs, cached_prop), ...]
g_lbl_status       = None
g_jaw_live    = False
g_jaw_btn     = None  # button reference for caption toggle
g_viewport_rig_live = True   # Toggle connection state for the Viewport Rig
g_last_slider_time  = 0.0    # Timestamp of the last slider change for debouncing
g_btn_rig_live      = None   # Live toggle button reference

# ── Slider Property Naming ────────────────────────────────────────────────────

def _slider_prop_name(grp_label, sub_label):
    """Convert group/sub labels to a valid property name for the MasterCtrl.
    e.g. 'Brow L', 'Inner' → 'BrowL_Inner'
    """
    g = grp_label.replace(" ", "")
    s = sub_label.replace(" ", "")
    return "{}_{}".format(g, s)

# ── Master Ctrl Helpers ───────────────────────────────────────────────────────

def _get_master_ctrl():
    """Return existing Facial_MasterCtrl null, or None."""
    return FBFindModelByLabelName(MASTER_CTRL_NAME)

def _get_master_prop(prop_name):
    """Return an animated property from the MasterCtrl by name."""
    mc = _get_master_ctrl()
    if not mc: return None
    return mc.PropertyList.Find(prop_name)

def _set_master_prop(prop_name, value):
    """Write a value to a MasterCtrl custom property (clamped -100..100)."""
    prop = _get_master_prop(prop_name)
    if prop:
        prop.Data = max(-100.0, min(100.0, float(value)))

def _get_master_prop_val(prop_name):
    """Read a value from a MasterCtrl custom property."""
    prop = _get_master_prop(prop_name)
    return float(prop.Data) if prop else 0.0

# ── BS Helpers ────────────────────────────────────────────────────────────────

def _set_bs(name, value):
    """Safely set blendshape value on g_target."""
    try:
        if not name or not g_target or not g_target.LongName: return
        for bs in name.split(','):
            bs = bs.strip()
            if not bs: continue
            prop = g_target.PropertyList.Find(bs)
            if prop:
                prop.Data = max(0.0, min(100.0, float(value)))
    except:
        # Object might be destroyed
        pass

def _get_bs(name):
    """Safely get blendshape value from g_target."""
    try:
        if not g_target or not g_target.LongName: return 0.0
        prop = g_target.PropertyList.Find(name)
        return float(prop.Data) if prop else 0.0
    except:
        return 0.0

def _all_bs_names():
    names = list(JAW_ALL_BS)
    for _, subs in GROUPS:
        for _, pos, neg in subs:
            for b in (pos + ',' + neg).split(','):
                b = b.strip()
                if b: names.append(b)
    return list(set(names))

def _get_jaw_ctrl():
    return FBFindModelByLabelName(JAW_CTRL_NAME)

def _get_jaw_ref():
    return FBFindModelByLabelName(JAW_REF_NAME)

def _status(msg):
    if g_lbl_status: g_lbl_status.Caption = msg

def _create_ctrl_marker(name, parent, pos, color=FBColor(1,0.6,0), size=50.0, limits=None):
    """Helper to create a viewport marker directly under the parent Null group."""
    # Clean existing marker
    m = FBFindModelByLabelName(name)
    if m: m.FBDelete()
    zg = FBFindModelByLabelName(name + "_Zero")
    if zg: zg.FBDelete() # Clean up legacy zero groups if any
    
    # Create marker directly under parent Null group
    ctrl = FBModelMarker(name)
    ctrl.Parent = parent
    ctrl.Show = True
    ctrl.Size = size
    ctrl.Color = color
    ctrl.Look = FBMarkerLook.kFBMarkerLookHardCross
    ctrl.Translation = pos
    ctrl.Rotation = FBVector3d(0, 0, 0)
    ctrl.Scaling = FBVector3d(1, 1, 1)
    
    # Safely zero-out RotationPivot and ScalingPivot using PropertyList.Find()
    # This is the officially supported MoBu Python API — no C++ exception risk!
    zero = FBVector3d(0, 0, 0)
    rp = ctrl.PropertyList.Find("RotationPivot")
    if rp: rp.Data = zero
    sp = ctrl.PropertyList.Find("ScalingPivot")
    if sp: sp.Data = zero
    
    # Animation & Limits
    ctrl.Translation.SetAnimated(True)
    if limits:
        # limits: (minX, maxX, minY, maxY) relative to rest position
        ctrl.TranslationMinX = (limits[0] is not None)
        ctrl.TranslationMaxX = (limits[1] is not None)
        ctrl.TranslationMinY = (limits[2] is not None)
        ctrl.TranslationMaxY = (limits[3] is not None)
        
        min_x = pos[0] + limits[0] if limits[0] is not None else pos[0]
        max_x = pos[0] + limits[1] if limits[1] is not None else pos[0]
        min_y = pos[1] + limits[2] if limits[2] is not None else pos[1]
        max_y = pos[1] + limits[3] if limits[3] is not None else pos[1]
        
        ctrl.TranslationMin = FBVector3d(min_x, min_y, 0)
        ctrl.TranslationMax = FBVector3d(max_x, max_y, 0)
        
    return ctrl

# ── Relation Rig Helpers ──────────────────────────────────────────────────────

def _get_or_create_relation():
    """Create relation constraint directly using the FBConstraintRelation constructor."""
    rel = FBFindObjectByFullName("Constraint:" + RELATION_NAME)
    if rel: 
        rel.FBDelete()
        FBSystem().Scene.Evaluate()
        
    rel = FBConstraintRelation(RELATION_NAME)
    if rel:
        rel.Active = True
    return rel

def _find_node(parent_node, name):
    """Safely find a sub-node by name from a parent animation node, supporting both exact and suffix matches."""
    if not parent_node: return None
    try:
        found = parent_node.Nodes.Find(name)
        if found: return found
    except:
        pass
    for node in parent_node.Nodes:
        if node.Name == name:
            return node
    for node in parent_node.Nodes:
        if node.Name.endswith(" " + name) or node.Name.endswith("." + name):
            return node
    return None

def _find_node_recursive(node, name):
    """Recursively search for a child node by name under an animation node hierarchy, supporting suffix matches."""
    if not node: return None
    if node.Name == name or node.Name.endswith("." + name) or node.Name.endswith(" " + name):
        return node
    for child in node.Nodes:
        res = _find_node_recursive(child, name)
        if res: return res
    return None

def _find_node_hierarchical(root_node, path):
    """Find translation, rotation, or custom property nodes hierarchically with recursive fallback."""
    if not root_node: return None
    
    # Try exact match first
    found = _find_node(root_node, path)
    if found: return found
    
    if "Lcl Translation" in path or "Translation" in path:
        axis = path.split()[-1] # "X", "Y", "Z"
        parent = _find_node_recursive(root_node, "Lcl Translation")
        if not parent: parent = _find_node_recursive(root_node, "Translation")
        if parent:
            return _find_node(parent, axis)
    elif "Lcl Rotation" in path or "Rotation" in path:
        axis = path.split()[-1]
        parent = _find_node_recursive(root_node, "Lcl Rotation")
        if not parent: parent = _find_node_recursive(root_node, "Rotation")
        if parent:
            return _find_node(parent, axis)
    else:
        # Custom property recursive search
        return _find_node_recursive(root_node, path)
    return None

def _rel_connect(rel, sender_obj, sender_prop, receiver_obj, receiver_prop, scale=66.66, offset_node=None, is_neg=False):
    """
    Connect sender property to receiver property via relation.
    Uses standard SDK SetAsSource, ConstrainObject, and FBConnect.
    """
    # 1. Create or get sender box in relation
    s_box = rel.SetAsSource(sender_obj)
    # 2. Create or get receiver box in relation
    r_box = rel.ConstrainObject(receiver_obj)
    
    if not s_box or not r_box:
        print("Relation connect failed: could not create box for {} or {}".format(sender_obj.Name, receiver_obj.Name))
        return None

    try:
        s_out = _find_node_hierarchical(s_box.AnimationNodeOutGet(), sender_prop)
        r_in = _find_node_hierarchical(r_box.AnimationNodeInGet(), receiver_prop)
        
        # Diagnostic print helper
        def print_all_nodes(node, depth=0):
            if not node: return
            print("  " * depth + "- " + node.Name)
            for child in node.Nodes:
                print_all_nodes(child, depth + 1)

        if not s_out:
            print("Relation Warning: output node '{}' not found on sender '{}'".format(sender_prop, sender_obj.Name))
            print("Available sender output nodes:")
            print_all_nodes(s_box.AnimationNodeOutGet())
            return None
        if not r_in:
            print("Relation Warning: input node '{}' not found on receiver '{}'".format(receiver_prop, receiver_obj.Name))
            print("Available receiver input nodes:")
            print_all_nodes(r_box.AnimationNodeInGet())
            return None
            
        # Create Multiply box
        mult = rel.CreateFunctionBox("Number", "Multiply (Number)")
        if not mult:
            print("Relation Error: Could not create Multiply function box")
            return None
            
        mult_factor = _find_node(mult.AnimationNodeInGet(), "Factor")
        mult_mult = _find_node(mult.AnimationNodeInGet(), "Multiplier")
        mult_result = _find_node(mult.AnimationNodeOutGet(), "Result")
        
        if mult_mult:
            try: mult_mult.WriteData([float(-scale if is_neg else scale)])
            except:
                try: mult_mult.Data = float(-scale if is_neg else scale)
                except: pass
            
        val_out = s_out
        if offset_node:
            # Create Add box
            add = rel.CreateFunctionBox("Number", "Add (Number)")
            if add:
                add_1 = _find_node(add.AnimationNodeInGet(), "Number 1")
                add_2 = _find_node(add.AnimationNodeInGet(), "Number 2")
                add_res = _find_node(add.AnimationNodeOutGet(), "Result")
                
                # Get the translation out of offset_node
                offset_out = _find_node_hierarchical(offset_node.AnimationNodeOutGet(), sender_prop)
                
                if offset_out and add_1 and add_2 and add_res:
                    FBConnect(s_out, add_1)
                    FBConnect(offset_out, add_2)
                    val_out = add_res
            
        if val_out and mult_factor and mult_result:
            FBConnect(val_out, mult_factor)
            FBConnect(mult_result, r_in)
            print("Successfully connected: {}.{} -> {}.{} (scale={})".format(
                sender_obj.Name, sender_prop, receiver_obj.Name, receiver_prop, scale))
            
        return s_box
    except Exception as e:
        print("Relation Connect error: {}".format(e))
        return None

# ── Slider → BS + MasterCtrl (shared logic) ──────────────────────────────────

def _apply_slider(slider_val, prop_name, pos_bs, neg_bs):
    """Apply a slider value (−100..100) to both MasterCtrl prop and mesh BS."""
    # Write to MasterCtrl
    _set_master_prop(prop_name, slider_val)
    # Write to mesh BS
    if slider_val >= 0:
        _set_bs(pos_bs, slider_val)
        _set_bs(neg_bs, 0)
    else:
        _set_bs(pos_bs, 0)
        _set_bs(neg_bs, abs(slider_val))

# ── Sync helpers ──────────────────────────────────────────────────────────────

def _sync_sliders_from_master():
    """Read MasterCtrl props → update slider UI (no BS write)."""
    mc = _get_master_ctrl()
    if not mc: return
    for item in g_sliders:
        slider, prop_name = item[0], item[1]
        slider.Value = _get_master_prop_val(prop_name)

def _sync_sliders_from_bs():
    """Read mesh BS → update slider UI (fallback when no MasterCtrl)."""
    for item in g_sliders:
        slider, prop_name, pos_bs, neg_bs = item[0], item[1], item[2], item[3]
        val_pos = max((_get_bs(b) for b in pos_bs.split(',') if b.strip()), default=0)
        val_neg = max((_get_bs(b) for b in neg_bs.split(',') if b.strip()), default=0)
        slider.Value = val_pos - val_neg

def _sync_jaw_ctrl_from_bs():
    ctrl = _get_jaw_ctrl()
    if not ctrl: return
    # Ctrl right (+x) = character jaw left, ctrl left (-x) = character jaw right
    x = (_get_bs("jawLeft") - _get_bs("jawRight")) / JAW_SCALE
    y = (_get_bs("jawForward") - _get_bs("jawOpen")) / JAW_SCALE
    ctrl.Translation = FBVector3d(x, y, ctrl.Translation[2])

# ── Slider Callbacks ──────────────────────────────────────────────────────────

def on_slider_change(control, event):
    global g_mc_syncing, g_last_slider_time
    g_last_slider_time = time.time()
    if g_mc_syncing: return   # don't echo back when idle sync is writing
    if not g_target: return
    for item in g_sliders:
        slider, prop_name, pos_bs, neg_bs = item[0], item[1], item[2], item[3]
        if slider is control:
            _apply_slider(slider.Value, prop_name, pos_bs, neg_bs)
            FBSystem().Scene.Evaluate()
            return

# ── Jaw Live Callback ─────────────────────────────────────────────────────────

def _jaw_update(control, event):
    """Runs on OnUIIdle to drive jaw BS from Ctrl_Jaw position."""
    global g_jaw_live
    if not g_jaw_live: return
    try:
        # Validate g_target is still alive
        if not g_target or not g_target.LongName:
            raise RuntimeError("target gone")
        ctrl = _get_jaw_ctrl()
        if not ctrl: return
        t = ctrl.Translation
        x, y = float(t[0]), float(t[1])
        # Ctrl right (+x) = character jaw moves left, Ctrl left (-x) = jaw right
        _set_bs("jawLeft",    max(0,  x) * JAW_SCALE)
        _set_bs("jawRight",   max(0, -x) * JAW_SCALE)
        _set_bs("jawForward", max(0,  y) * JAW_SCALE)
        _set_bs("jawOpen",    max(0, -y) * JAW_SCALE)
    except Exception:
        # SDK object destroyed — turn off live mode gracefully
        g_jaw_live = False
        try: FBSystem().OnUIIdle.Remove(_jaw_update)
        except: pass
        _status("Jaw Live stopped (scene object destroyed).")

def _mc_idle_sync(control, event):
    """Bidirectional sync: reads MasterCtrl props → updates slider UI.
    Optimized with property caching to avoid string searches every tick."""
    global g_mc_syncing, g_sliders, g_last_slider_time
    if time.time() - g_last_slider_time < 0.3: return # Lockout slider feedback loop during active UI dragging
    if g_mc_syncing: return
    try:
        mc = _get_master_ctrl()
        if not mc: return
        g_mc_syncing = True
        
        for i in range(len(g_sliders)):
            item = g_sliders[i]
            slider, prop_name, pos_bs, neg_bs, prop = item
            
            # Cache the property if not already found
            if not prop:
                prop = mc.PropertyList.Find(prop_name)
                if prop:
                    # Update cache in the global list
                    g_sliders[i] = (slider, prop_name, pos_bs, neg_bs, prop)
            
            if prop:
                val = float(prop.Data)
                # Use a threshold to avoid constant tiny UI redraws
                if abs(val - slider.Value) > 0.1:
                    slider.Value = val
                    # Also drive BS directly — on_slider_change is blocked by
                    # g_mc_syncing flag, so we must write BS here manually
                    if g_target:
                        if val >= 0:
                            _set_bs(pos_bs, val)
                            _set_bs(neg_bs, 0)
                        else:
                            _set_bs(pos_bs, 0)
                            _set_bs(neg_bs, abs(val))

        g_mc_syncing = False
    except Exception:
        g_mc_syncing = False

def _live_rig_sync(control, event):
    """
    Script-based Real-time Viewport Rig Sync (Zero Relation Constraints!).
    Runs on OnUIIdle.
    - If a marker is selected (user dragging in viewport): we drive MasterCtrl & BlendShapes.
    - If a marker is NOT selected (user scrubbing timeline/changing sliders): we drive marker 3D position.
    """
    global g_mc_syncing, g_sliders, g_viewport_rig_live, g_last_slider_time
    if not g_viewport_rig_live: return
    if time.time() - g_last_slider_time < 0.2: return # Debounce cooldown during slider drags
    if g_mc_syncing: return
    
    # 1. Check if viewport rig root exists
    root = FBFindModelByLabelName(RIG_ROOT_NAME)
    if not root: return
    
    mc = _get_master_ctrl()
    if not mc: return
    
    try:
        # Cache of all marker objects in scene
        markers = {}
        for name in ["Ctrl_Brow_Master", "Ctrl_Brow_L", "Ctrl_Brow_Inner", "Ctrl_Brow_R",
                     "Ctrl_Look", "Ctrl_EyeOpen_R", "Ctrl_EyeOpen_L",
                     "Ctrl_Mouth_Master", "Ctrl_Mouth_R", "Ctrl_Mouth_C", "Ctrl_Mouth_L",
                     "Ctrl_Jaw"]:
            m = FBFindModelByLabelName(name)
            if m: markers[name] = m
            
        if not markers: return
        
        scale = 66.66
        
        # Check if any viewport marker is currently selected (means user is interacting with viewport)
        any_selected = any(m.Selected for m in markers.values())
        
        g_mc_syncing = True
        
        # ─── BROW GROUP ───
        # Brow_L/Inner/R are LOCAL to Brow_Sub_Grp (follows Master). Resting local Y = 0.
        if "Ctrl_Brow_Master" in markers and markers["Ctrl_Brow_Master"].Selected:
            y = markers["Ctrl_Brow_Master"].Translation[1] - 3.0  # resting local Y is 3 (relative to brow_g)
            for prop_name in ["BrowL_Outer", "Brow_Inner", "BrowR_Outer"]:
                _set_master_prop(prop_name, y * scale)
        else:
            for marker_name, prop_name in [("Ctrl_Brow_L", "BrowL_Outer"), 
                                           ("Ctrl_Brow_Inner", "Brow_Inner"), 
                                           ("Ctrl_Brow_R", "BrowR_Outer")]:
                if marker_name in markers:
                    m = markers[marker_name]
                    if m.Selected:
                        # Local Y relative to Brow_Sub_Grp (resting = 0) — no offset!
                        _set_master_prop(prop_name, m.Translation[1] * scale)
                    elif not any_selected:
                        val = _get_master_prop_val(prop_name)
                        m.Translation = FBVector3d(m.Translation[0], val / scale, m.Translation[2])

        # ─── EYE GROUP ───
        # Ctrl_Look drives Look_LR / Look_UD via its own XY local displacement (resting = 0).
        # EyeOpen_L/R are LOCAL to Eye_Sub_Grp (follows Look). Resting local Y = 0.
        if "Ctrl_Look" in markers:
            m = markers["Ctrl_Look"]
            if m.Selected:
                _set_master_prop("Look_LR", -m.Translation[0] * scale)
                _set_master_prop("Look_UD",  m.Translation[1] * scale)
            elif not any_selected:
                x = -_get_master_prop_val("Look_LR") / scale
                y =  _get_master_prop_val("Look_UD") / scale
                m.Translation = FBVector3d(x, y, m.Translation[2])
                
        for marker_name, prop_name in [("Ctrl_EyeOpen_R", "EyeR_Open"), 
                                       ("Ctrl_EyeOpen_L", "EyeL_Open")]:
            if marker_name in markers:
                m = markers[marker_name]
                if m.Selected:
                    # Local Y relative to Eye_Sub_Grp (resting = 0) — no offset!
                    _set_master_prop(prop_name, m.Translation[1] * scale)
                elif not any_selected:
                    val = _get_master_prop_val(prop_name)
                    m.Translation = FBVector3d(m.Translation[0], val / scale, m.Translation[2])

        # ─── MOUTH GROUP ───
        # Master drives all subs as a group offset. Sub R/C/L drive individual props.
        # Since R/C/L are parented under Master via Mouth_Sub_Grp, their .Translation is LOCAL to Master.
        if "Ctrl_Mouth_Master" in markers and markers["Ctrl_Mouth_Master"].Selected:
            y = markers["Ctrl_Mouth_Master"].Translation[1] + 3.0  # resting local Y is -3
            for prop_name in ["Mouth_Rt", "Mouth_Lt", "Mouth_Upper"]:
                _set_master_prop(prop_name, y * scale)
        else:
            for marker_name, prop_name in [("Ctrl_Mouth_R", "Mouth_Rt"), 
                                           ("Ctrl_Mouth_L", "Mouth_Lt"), 
                                           ("Ctrl_Mouth_C", "Mouth_Upper")]:
                if marker_name in markers:
                    m = markers[marker_name]
                    if m.Selected:
                        # Local Y relative to Master (resting = 0) — no offset needed!
                        _set_master_prop(prop_name, m.Translation[1] * scale)
                    elif not any_selected:
                        val = _get_master_prop_val(prop_name)
                        m.Translation = FBVector3d(m.Translation[0], val / scale, m.Translation[2])

        # ─── JAW GROUP ───
        if "Ctrl_Jaw" in markers:
            m = markers["Ctrl_Jaw"]
            if m.Selected:
                _set_master_prop("Jaw_Side", m.Translation[0] * scale)
                y = m.Translation[1]
                if y >= 0:
                    _set_master_prop("Jaw_Fwd", y * scale)
                    _set_master_prop("Jaw_Open", 0.0)
                else:
                    _set_master_prop("Jaw_Fwd", 0.0)
                    _set_master_prop("Jaw_Open", abs(y) * scale)
            elif not any_selected:
                x = _get_master_prop_val("Jaw_Side") / scale
                val_fwd = _get_master_prop_val("Jaw_Fwd")
                val_open = _get_master_prop_val("Jaw_Open")
                if val_fwd >= val_open:
                    y = val_fwd / scale
                else:
                    y = -val_open / scale
                m.Translation = FBVector3d(x, y, m.Translation[2])

        # ─── SYNC SLIDER UI AND BLENDSHAPES ───
        for item in g_sliders:
            slider, prop_name, pos_bs, neg_bs, prop = item
            if prop:
                val = float(prop.Data)
                if abs(val - slider.Value) > 0.1:
                    slider.Value = val
                if g_target:
                    if val >= 0:
                        _set_bs(pos_bs, val)
                        _set_bs(neg_bs, 0)
                    else:
                        _set_bs(pos_bs, 0)
                        _set_bs(neg_bs, abs(val))

        g_mc_syncing = False
    except Exception as e:
        g_mc_syncing = False

# ── Button Handlers ───────────────────────────────────────────────────────────

def OnConnectClick(control, event):
    """Find and connect to the selected mesh. Auto-generates MasterCtrl if needed."""
    global g_target
    try:
        g_target = None
        models = FBModelList()
        FBGetSelectedModels(models)

        if len(models) == 0:
            FBMessageBox("Connect Status", "No object selected in scene.\nPlease select your face mesh and try again.", "OK")
            return

        g_target = models[0]
        found = 0
        all_names = _all_bs_names()
        for bs in all_names:
            prop = g_target.PropertyList.Find(bs)
            if prop:
                prop.SetAnimated(True)
                found += 1

        # Auto-generate MasterCtrl if not already in scene
        mc = _get_master_ctrl()
        mc_created = False
        if not mc:
            _auto_generate_master_ctrl()
            mc_created = True

        msg = "Connected to: {}\n{} BlendShapes found.".format(g_target.Name, found)
        if found == 0:
            msg += "\n\nWarning: No matching ARKit BlendShapes found!\nMake sure this mesh has ARKit BS properties."
        if mc_created:
            msg += "\n\nFacial_MasterCtrl auto-generated."

        FBMessageBox("Connect Status", msg, "OK")
        _status("Connected: {} ({} BS) | MasterCtrl ready.".format(g_target.Name, found))

    except Exception as e:
        g_target = None
        _status("Connect failed.")
        FBMessageBox("Error", "Critical error:\n{}".format(str(e)), "OK")

def OnDisconnectClick(control, event):
    """Stop all live callbacks and clear target connection."""
    global g_target, g_jaw_live
    # Stop Jaw Live
    if g_jaw_live:
        g_jaw_live = False
        try: FBSystem().OnUIIdle.Remove(_jaw_update)
        except: pass
    # Reset slider UI to neutral
    for item in g_sliders:
        item[0].Value = 0.0
    g_target = None
    _status("Disconnected. You can now inspect the baked result freely.")

def _auto_generate_master_ctrl():
    """Internal: create Facial_MasterCtrl without popup. Called by OnConnectClick."""
    global g_master_ctrl
    existing = FBFindModelByLabelName(MASTER_CTRL_NAME)
    if existing: return  # Already exists, keep it

    mc = FBModelNull(MASTER_CTRL_NAME)
    mc.Show = True
    mc.Size = 15.0
    pos = FBVector3d(0, 20, 0)
    if g_target:
        mn, mx = FBVector3d(), FBVector3d()
        g_target.GetBoundingBox(mn, mx)
        pos = FBVector3d((mn[0]+mx[0])*0.5, mx[1] + 10, (mn[2]+mx[2])*0.5)
    mc.Translation = pos
    for grp_label, subs in GROUPS:
        for sub_label, pos_bs, neg_bs in subs:
            prop_name = _slider_prop_name(grp_label, sub_label)
            prop = mc.PropertyCreate(prop_name, FBPropertyType.kFBPT_float, "Number", True, True, None)
            if prop:
                prop.SetMin(-100.0); prop.SetMax(100.0)
                prop.Data = 0.0; prop.SetAnimated(True)
    g_master_ctrl = mc
    for i in range(len(g_sliders)):
        s = g_sliders[i]
        g_sliders[i] = (s[0], s[1], s[2], s[3], None)
    FBSystem().Scene.Evaluate()

def OnGenerateMasterClick(control, event):
    """Generate Facial_MasterCtrl null with one custom prop per slider."""
    global g_master_ctrl

    # Clean up existing
    existing = FBFindModelByLabelName(MASTER_CTRL_NAME)
    if existing:
        existing.FBDelete()

    # Create the null
    mc = FBModelNull(MASTER_CTRL_NAME)
    mc.Show = True
    mc.Size = 15.0

    # Position it near the face target (above it), or default
    pos = FBVector3d(0, 20, 0)
    if g_target:
        mn, mx = FBVector3d(), FBVector3d()
        g_target.GetBoundingBox(mn, mx)
        pos = FBVector3d((mn[0]+mx[0])*0.5, mx[1] + 10, (mn[2]+mx[2])*0.5)
    mc.Translation = pos

    # Add one animated custom float property per slider
    count = 0
    for grp_label, subs in GROUPS:
        for sub_label, pos_bs, neg_bs in subs:
            prop_name = _slider_prop_name(grp_label, sub_label)
            prop = mc.PropertyCreate(prop_name, FBPropertyType.kFBPT_float, "Number", True, True, None)
            if prop:
                prop.SetMin(-100.0)
                prop.SetMax(100.0)
                prop.Data = 0.0
                prop.SetAnimated(True)
                count += 1

    g_master_ctrl = mc
    
    # Reset property cache in g_sliders to force re-finding on next sync
    for i in range(len(g_sliders)):
        s = g_sliders[i]
        g_sliders[i] = (s[0], s[1], s[2], s[3], None)

    FBSystem().Scene.Evaluate()
def _create_rig_camera(root):
    """Create a dedicated vertical camera focused on the Viewport Rig panel."""
    cam_name = "Facial_Rig_Camera"
    cam = FBFindModelByLabelName(cam_name)
    if cam: cam.FBDelete()
    
    cam = FBCamera(cam_name)
    cam.Show = True
    
    # Parent to root so it follows the rig panel
    cam.Parent = root
    # Position camera at local T(0,0,75) and R(0,90,0) relative to FaceRig Root
    cam.Translation = FBVector3d(0, 0, 75)
    cam.Rotation = FBVector3d(0, 90, 0)
    
    # Adjust camera lens settings for optimal framing
    cam.FieldOfView = 20.0 # Telephoto lens to prevent perspective distortion
    
    FBSystem().Scene.Evaluate()
    return cam

def OnGenerateFullRigClick(control, event):
    """Generate a high-performance viewport rig driven by real-time Python callbacks (Zero Relation Constraints!)."""
    if not g_target:
        FBMessageBox("Error", "Connect to mesh first!", "OK"); return
        
    # 1. Root Setup
    root = FBFindModelByLabelName(RIG_ROOT_NAME)
    if root: root.FBDelete()
    
    root = FBModelNull(RIG_ROOT_NAME)
    root.Show = True
    root.Size = 100.0
    
    # Center root on face
    mn, mx = FBVector3d(), FBVector3d()
    g_target.GetBoundingBox(mn, mx)
    center_pos = FBVector3d((mn[0]+mx[0])*0.5, (mn[1]+mx[1])*0.5, mx[2] + 15)
    root.Translation = center_pos
    
    # Delete any leftover Relation Constraints to ensure scene stays clean and fast
    rel = FBFindObjectByFullName("Constraint:" + RELATION_NAME)
    if rel: rel.FBDelete()
    
    mc = _get_master_ctrl()
    if not mc: _auto_generate_master_ctrl(); mc = _get_master_ctrl()
    
    # Helper to create group roots for clean layout
    def _grp(name, pos):
        g = FBModelNull(name)
        g.Parent = root
        g.Translation = pos
        g.Show = False
        return g

    # Helper to create an invisible, non-selectable bridge Null that follows its parent marker
    def _sub_grp(name, parent_marker, offset_y):
        g = FBModelNull(name)
        g.Parent = parent_marker
        g.Translation = FBVector3d(0, offset_y, 0)
        g.Show = False       # hidden in viewport
        g.Pickable = False   # cannot be accidentally box-selected
        return g

    # ── BROW RIG ──  Master (yellow) is 1.5u ABOVE sub-controls (orange)
    # brow_g world Y = 6. Master local Y=3 → world Y=9. Sub_Grp local Y=-1.5 → world Y=7.5
    brow_g = _grp("Brow_Rig", FBVector3d(0, 6, 0))
    c_br_m = _create_ctrl_marker("Ctrl_Brow_Master", brow_g, FBVector3d(0, 3, 0), FBColor(1,1,0), 100.0, (-1.5,1.5, -1.5,1.5))
    brow_sub_g = _sub_grp("Brow_Sub_Grp", c_br_m, -1.5)
    c_br_l = _create_ctrl_marker("Ctrl_Brow_L",     brow_sub_g, FBVector3d( 5, 0, 0), FBColor(1,0.5,0), 100.0, (0,0, -1.5,1.5))
    c_br_i = _create_ctrl_marker("Ctrl_Brow_Inner", brow_sub_g, FBVector3d( 0, 0, 0), FBColor(1,0.8,0), 100.0, (0,0, -1.5,1.5))
    c_br_r = _create_ctrl_marker("Ctrl_Brow_R",     brow_sub_g, FBVector3d(-5, 0, 0), FBColor(1,0.5,0), 100.0, (0,0, -1.5,1.5))

    # ── EYE RIG ──  Look (cyan) is 1.5u ABOVE EyeOpen (blue)
    # eye_g world Y = 2. Look local Y=0 → world Y=2. Eye_Sub_Grp local Y=-1.5 → world Y=0.5
    eye_g = _grp("Eye_Rig", FBVector3d(0, 2, 0))
    c_look = _create_ctrl_marker("Ctrl_Look", eye_g, FBVector3d(0, 0, 0), FBColor(0,1,1), 100.0, (-1.5,1.5, -1.5,1.5))
    eye_sub_g = _sub_grp("Eye_Sub_Grp", c_look, -1.5)
    c_op_r = _create_ctrl_marker("Ctrl_EyeOpen_R", eye_sub_g, FBVector3d(-6, 0, 0), FBColor(0.2,0.8,1), 100.0, (0,0, -1.5,1.5))
    c_op_l = _create_ctrl_marker("Ctrl_EyeOpen_L", eye_sub_g, FBVector3d( 6, 0, 0), FBColor(0.2,0.8,1), 100.0, (0,0, -1.5,1.5))

    # ── MOUTH RIG ──  Master (yellow) is 1.5u ABOVE sub-controls (red)
    # mth_g world Y = -4. Master local Y=-3 → world Y=-7. Sub_Grp local Y=-1.5 → world Y=-8.5
    mth_g = _grp("Mouth_Rig", FBVector3d(0, -4, 0))
    c_m_m = _create_ctrl_marker("Ctrl_Mouth_Master", mth_g, FBVector3d(0, -3, 0), FBColor(1,1,0), 100.0, (-1.5,1.5, -1.5,1.5))
    mth_sub_g = _sub_grp("Mouth_Sub_Grp", c_m_m, -1.5)
    c_m_r = _create_ctrl_marker("Ctrl_Mouth_R", mth_sub_g, FBVector3d(-5, 0, 0), FBColor(1,0.4,0.4), 100.0, (-1.5,1.5, -1.5,1.5))
    c_m_c = _create_ctrl_marker("Ctrl_Mouth_C", mth_sub_g, FBVector3d( 0, 0, 0), FBColor(1,0.6,0.6), 100.0, (0,0, -1.5,1.5))
    c_m_l = _create_ctrl_marker("Ctrl_Mouth_L", mth_sub_g, FBVector3d( 5, 0, 0), FBColor(1,0.4,0.4), 100.0, (-1.5,1.5, -1.5,1.5))

    # ── JAW RIG ──  single 4-way marker
    jaw_g = _grp("Jaw_Rig", FBVector3d(0, -10, 0))
    c_jaw = _create_ctrl_marker("Ctrl_Jaw", jaw_g, FBVector3d(0, 0, 0), FBColor(1,0,0), 100.0, (-1.5,1.5, -1.5,1.5))

 
    # Force UI idle callback cleanup & registration to ensure only one instance is active
    try: FBSystem().OnUIIdle.Remove(_mc_idle_sync)
    except: pass
    try: FBSystem().OnUIIdle.Remove(_live_rig_sync)
    except: pass
    
    FBSystem().OnUIIdle.Add(_mc_idle_sync)
    FBSystem().OnUIIdle.Add(_live_rig_sync)
 
    # Create the Portrait Rig Camera
    _create_rig_camera(root)
 
    FBSystem().Scene.Evaluate()
    _status("Viewport Rig Generated. Script-based real-time drive active.")
    FBMessageBox("Rig Done", "Viewport Rig created and driven by high-performance real-time Python callback.\n\nNo buggy constraints! Move markers or scrub timeline to see bidirectional sync.", "OK")

def OnToggleViewportLiveClick(control, event):
    global g_viewport_rig_live
    g_viewport_rig_live = not g_viewport_rig_live
    if g_viewport_rig_live:
        control.Caption = "Viewport Live: ON"
        _status("Viewport Rig Live connection active.")
    else:
        control.Caption = "Viewport Live: OFF"
        _status("Viewport Rig disconnected. Sliders unlocked.")

def OnToggleJawLive(control, event):
    FBMessageBox("Deprecated", "Script-based Live mode is replaced by Relation Rig.\nPlease use 'Generate Full Rig'.", "OK")

def OnResetClick(control, event):
    for item in g_sliders:
        slider, prop_name = item[0], item[1]
        slider.Value = 0.0
        _set_master_prop(prop_name, 0.0)
    ctrl = _get_jaw_ctrl()
    if ctrl:
        ctrl.Translation = FBVector3d(0, 0, ctrl.Translation[2])
    if g_target:
        for bs in _all_bs_names():
            _set_bs(bs, 0)
        FBSystem().Scene.Evaluate()
    _status("Reset done.")

def OnSyncPoseClick(control, event):
    """Read current MasterCtrl props → update slider UI (if no MasterCtrl, fall back to BS)."""
    if not g_target:
        FBMessageBox("Error", "Not connected.", "OK"); return
    FBSystem().Scene.Evaluate()
    mc = _get_master_ctrl()
    if mc:
        _sync_sliders_from_master()
        _status("Synced pose from MasterCtrl at frame {}.".format(FBSystem().LocalTime.GetFrame()))
    else:
        _sync_sliders_from_bs()
        _sync_jaw_ctrl_from_bs()
        _status("Synced pose from BS at frame {}.".format(FBSystem().LocalTime.GetFrame()))

def OnKeyFrameClick(control, event):
    """Key ONLY the MasterCtrl custom props in the Timeline.
    Does NOT touch the face mesh BS — use [Bake Ctrl → BS] for that.
    """
    if not g_target:
        FBMessageBox("Error", "Not connected.", "OK"); return

    mc = _get_master_ctrl()
    if not mc:
        FBMessageBox("Error", "Facial_MasterCtrl not found.\nPlease Connect to Selection first.", "OK"); return

    keyed = 0
    for grp_label, subs in GROUPS:
        for sub_label, pos_bs, neg_bs in subs:
            prop_name = _slider_prop_name(grp_label, sub_label)
            prop = mc.PropertyList.Find(prop_name)
            if prop:
                try: prop.Key(); keyed += 1
                except: pass

    _status("Keyed MasterCtrl: {} props at frame {}  (Mesh BS untouched)".format(
        keyed, FBSystem().LocalTime.GetFrame()))

# ── Animation Layer Helper ────────────────────────────────────────────────────

def _get_or_create_adj_layer(layer_name="FacialAdj"):
    """Get or create a named animation layer in the current take.
    MoBu 2026 C++ signature: FBAnimationLayer(name: str, flags: int)
    Returns the layer index and switches the current edit layer to it."""
    take = FBSystem().CurrentTake

    # ── Step 1: Search for existing layer by name ──
    n = take.GetLayerCount()
    for i in range(n):
        lyr = take.GetLayer(i)
        if lyr and lyr.Name == layer_name:
            take.SetCurrentLayer(i)
            _status("Using existing layer '{}' (idx {})".format(layer_name, i))
            return i

    # ── Step 2: Create new layer — correct signature: (name, int) ──
    count_before = take.GetLayerCount()
    try:
        new_lyr = FBAnimationLayer(layer_name, 0)   # 0 = default flags
        FBSystem().Scene.Evaluate()
        count_after = take.GetLayerCount()

        # Search by name
        for i in range(count_after):
            lyr = take.GetLayer(i)
            if lyr and lyr.Name == layer_name:
                take.SetCurrentLayer(i)
                _status("Created layer '{}' (idx {})".format(layer_name, i))
                return i

        # Count increased but name not matched — use last
        if count_after > count_before:
            idx = count_after - 1
            lyr = take.GetLayer(idx)
            if lyr:
                try: lyr.Name = layer_name
                except: pass
            take.SetCurrentLayer(idx)
            _status("Created layer at idx {}".format(idx))
            return idx

    except Exception as e:
        _status("Layer creation failed: {}".format(e))

    # ── Step 3: Fallback to base layer 0 ──
    _status("Warning: Could not create FacialAdj layer. Baking on base layer.")
    take.SetCurrentLayer(0)
    return 0


# ── Bake: Ctrl → BS ──────────────────────────────────────────────────────────

def OnBakeCtrlToBSClick(control, event):
    if not g_target:
        FBMessageBox("Error", "Not connected.", "OK"); return

    take  = FBSystem().CurrentTake
    span  = take.LocalTimeSpan
    start = span.GetStart()
    end   = span.GetStop()
    step  = FBTime(0, 0, 0, 1)
    curr  = FBTime(start)
    total = max(1, int(end.GetFrame() - start.GetFrame()) + 1)
    done  = 0
    names = _all_bs_names()
    mc    = _get_master_ctrl()

    # ── Create / switch to FacialAdj animation layer ──
    orig_layer = take.GetCurrentLayer()
    adj_layer_idx = _get_or_create_adj_layer("FacialAdj")
    _status("Baking to layer: FacialAdj (idx {})...".format(adj_layer_idx))

    while curr <= end:
        FBPlayerControl().Goto(curr)
        FBSystem().Scene.Evaluate()

        # Drive jaw from Ctrl_Jaw at this frame
        ctrl = _get_jaw_ctrl()
        if ctrl:
            t = ctrl.Translation
            x, y = float(t[0]), float(t[1])
            _set_bs("jawRight",   max(0,  x) * JAW_SCALE)
            _set_bs("jawLeft",    max(0, -x) * JAW_SCALE)
            _set_bs("jawForward", max(0,  y) * JAW_SCALE)
            _set_bs("jawOpen",    max(0, -y) * JAW_SCALE)

        # Drive all slider-mapped BS from MasterCtrl props (if available)
        if mc:
            for item in g_sliders:
                prop_name, pos_bs, neg_bs = item[1], item[2], item[3]
                v = _get_master_prop_val(prop_name)
                if v >= 0:
                    _set_bs(pos_bs, v); _set_bs(neg_bs, 0)
                else:
                    _set_bs(pos_bs, 0); _set_bs(neg_bs, abs(v))
        else:
            for item in g_sliders:
                slider, pos_bs, neg_bs = item[0], item[2], item[3]
                v = slider.Value
                if v >= 0:
                    _set_bs(pos_bs, v); _set_bs(neg_bs, 0)
                else:
                    _set_bs(pos_bs, 0); _set_bs(neg_bs, abs(v))

        # Key all mesh BS onto the FacialAdj layer
        for bs in names:
            prop = g_target.PropertyList.Find(bs)
            if prop:
                try: prop.Key()
                except: pass
        curr += step
        done += 1

    # Restore original layer
    try: take.SetCurrentLayer(orig_layer)
    except: pass

    _status("Baked Controls → BS ({} frames) on layer [FacialAdj]".format(done))
    FBMessageBox("Done", "Baked Controls → BS\n{} frames\nLayer: [FacialAdj]".format(done), "OK")

# ── Bake: BS → Jaw Ctrl ───────────────────────────────────────────────────────

def OnBakeBSToCtrlClick(control, event):
    if not g_target:
        FBMessageBox("Error", "Not connected.", "OK"); return
    ctrl = _get_jaw_ctrl()
    if not ctrl:
        FBMessageBox("Error", "Generate Jaw Controller first!", "OK"); return

    take  = FBSystem().CurrentTake
    span  = take.LocalTimeSpan
    start = span.GetStart()
    end   = span.GetStop()
    step  = FBTime(0, 0, 0, 1)
    curr  = FBTime(start)
    total = max(1, int(end.GetFrame() - start.GetFrame()) + 1)
    done  = 0

    while curr <= end:
        FBPlayerControl().Goto(curr)
        FBSystem().Scene.Evaluate()
        open_  = _get_bs("jawOpen")
        fwd    = _get_bs("jawForward")
        left   = _get_bs("jawLeft")
        right  = _get_bs("jawRight")
        x = (right - left)  / JAW_SCALE
        y = (fwd   - open_) / JAW_SCALE
        ctrl.Translation = FBVector3d(x, y, ctrl.Translation[2])
        ctrl.Translation.Key()
        curr += step
        done += 1
    _status("Baked BS → Jaw Ctrl ({} frames)".format(done))
    FBMessageBox("Done", "Baked BS → Jaw Controller\n{} frames".format(done), "OK")


# ── Bake: BS → MasterCtrl (LiveLink → FacialSlider) ──────────────────────────

def OnBakeBSToMasterClick(control, event):
    """Read mesh BS frame-by-frame → write & key Facial_MasterCtrl props.

    Reverse-mapping logic per slider:
      slider_val = max(pos_bs values) - max(neg_bs values)
      Range clamped to -100..100.
    This lets LiveLink face capture data be 'absorbed' into the MasterCtrl
    so the animator can edit it via FacialSliders.
    """
    if not g_target:
        FBMessageBox("Error", "Not connected.", "OK"); return
    mc = _get_master_ctrl()
    if not mc:
        FBMessageBox("Error", "Generate Master Ctrl first!", "OK"); return

    take  = FBSystem().CurrentTake
    span  = take.LocalTimeSpan
    start = span.GetStart()
    end   = span.GetStop()
    step  = FBTime(0, 0, 0, 1)
    curr  = FBTime(start)
    total = max(1, int(end.GetFrame() - start.GetFrame()) + 1)
    done  = 0

    # Ensure all MasterCtrl props are animated
    for grp_label, subs in GROUPS:
        for sub_label, pos_bs, neg_bs in subs:
            prop_name = _slider_prop_name(grp_label, sub_label)
            prop = mc.PropertyList.Find(prop_name)
            if prop:
                prop.SetAnimated(True)

    while curr <= end:
        FBPlayerControl().Goto(curr)
        FBSystem().Scene.Evaluate()

        for item in g_sliders:
            slider, prop_name, pos_bs, neg_bs = item[0], item[1], item[2], item[3]
            # Read positive BS channel(s) — take the max if comma-separated
            val_pos = 0.0
            if pos_bs:
                vals = [_get_bs(b.strip()) for b in pos_bs.split(',') if b.strip()]
                if vals: val_pos = max(vals)

            # Read negative BS channel(s) — take the max
            val_neg = 0.0
            if neg_bs:
                vals = [_get_bs(b.strip()) for b in neg_bs.split(',') if b.strip()]
                if vals: val_neg = max(vals)

            # Combine: positive side wins if both > 0 (shouldn't happen normally)
            slider_val = val_pos - val_neg
            slider_val = max(-100.0, min(100.0, slider_val))

            # Write to MasterCtrl prop and key it
            prop = mc.PropertyList.Find(prop_name)
            if prop:
                prop.Data = slider_val
                try: prop.Key()
                except: pass

            # Also update slider UI live
            slider.Value = slider_val

        curr += step
        done += 1
    _status("Baked BS → MasterCtrl ({} frames). Select Facial_MasterCtrl to view keys.".format(done))
    FBMessageBox("Done", "Baked BS → MasterCtrl\n{} frames\n\nSelect [Facial_MasterCtrl] in scene\nto see all keys in Timeline.".format(done), "OK")

# ── UI ────────────────────────────────────────────────────────────────────────

def PopulateTool(tool):
    global g_sliders, g_lbl_status
    g_sliders = []

    tool.StartSizeX = 820
    tool.StartSizeY = 560

    outer = FBVBoxLayout()
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft,   "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop,    "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight,  "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    tool.SetControl("main", outer)

    # Title
    t = FBLabel(); t.Caption = "Facial CtrlRig — Slider Panel"
    t.Justify = FBTextJustify.kFBTextJustifyCenter
    outer.Add(t, 28)

    # Row 1: Connection, Generation, Reset & Live state (First level)
    row1 = FBHBoxLayout(); outer.Add(row1, 30)
    b = FBButton(); b.Caption = "Connect to Selection"; b.OnClick.Add(OnConnectClick);    row1.Add(b, 150)
    b = FBButton(); b.Caption = "Generate FULL Viewport Rig"; b.OnClick.Add(OnGenerateFullRigClick); row1.Add(b, 180)
    
    global g_btn_rig_live
    g_btn_rig_live = FBButton()
    g_btn_rig_live.Caption = "Viewport Live: ON" if g_viewport_rig_live else "Viewport Live: OFF"
    g_btn_rig_live.OnClick.Add(OnToggleViewportLiveClick)
    row1.Add(g_btn_rig_live, 130)
    
    b = FBButton(); b.Caption = "Regen Master Ctrl";   b.OnClick.Add(OnGenerateMasterClick); row1.Add(b, 130)
    b = FBButton(); b.Caption = "Reset All";            b.OnClick.Add(OnResetClick);       row1.Add(b, 90)
    b = FBButton(); b.Caption = "Disconnect";           b.OnClick.Add(OnDisconnectClick);  row1.Add(b, 90)

    # Row 2: Animation & Baking (Second level)
    row2 = FBHBoxLayout(); outer.Add(row2, 30)
    b = FBButton(); b.Caption = "Key Frame";         b.OnClick.Add(OnKeyFrameClick);       row2.Add(b, 100)
    b = FBButton(); b.Caption = "Sync Pose → UI";   b.OnClick.Add(OnSyncPoseClick);       row2.Add(b, 120)
    b = FBButton(); b.Caption = "Bake Ctrl → BS";   b.OnClick.Add(OnBakeCtrlToBSClick);    row2.Add(b, 120)
    b = FBButton(); b.Caption = "Bake BS → Ctrl";   b.OnClick.Add(OnBakeBSToCtrlClick);    row2.Add(b, 120)
    b = FBButton(); b.Caption = "Bake BS → Master"; b.OnClick.Add(OnBakeBSToMasterClick); row2.Add(b, 140)

    # Status
    g_lbl_status = FBLabel(); g_lbl_status.Caption = "Ready. Connect mesh → Generate Master Ctrl → Key Frame."
    outer.Add(g_lbl_status, 20)
    outer.Add(FBLabel(), 4)

    # Slider area
    slider_row = FBHBoxLayout()
    outer.AddRelative(slider_row, 1.0)

    # 3 Column layout: Left (Screen Left / Char Right), Center (Unified), Right (Screen Right / Char Left)
    left_col = FBVBoxLayout(); slider_row.Add(left_col, 250)
    center_col = FBVBoxLayout(); slider_row.Add(center_col, 280)
    right_col = FBVBoxLayout(); slider_row.Add(right_col, 250)

    col_mapping = {
        "Brow R": left_col,
        "Eye R": left_col,
        "Cheek R": left_col,
        
        "Brow": center_col,
        "Look": center_col,
        "Nose": center_col,
        "Mouth": center_col,
        "Jaw": center_col,
        
        "Brow L": right_col,
        "Eye L": right_col,
        "Cheek L": right_col
    }

    for grp_label, subs in GROUPS:
        col = col_mapping.get(grp_label, center_col)
        
        # Compact container for the group
        grp_box = FBVBoxLayout()
        col.Add(grp_box, len(subs) * 24 + 28)
        
        # Group Header Label
        lbl = FBLabel()
        lbl.Caption = "── {} ──".format(grp_label)
        lbl.Justify = FBTextJustify.kFBTextJustifyLeft
        grp_box.Add(lbl, 20)
        
        # Horizontal Sliders stacked vertically
        for sub_label, pos_bs, neg_bs in subs:
            prop_name = _slider_prop_name(grp_label, sub_label)
            
            # Row container for label + slider
            row = FBHBoxLayout()
            grp_box.Add(row, 20)
            
            # Label (Left side)
            sl = FBLabel()
            sl.Caption = "  " + sub_label
            sl.Justify = FBTextJustify.kFBTextJustifyLeft
            row.Add(sl, 60)
            
            # Slider (Right side, horizontal orientation)
            slider = FBSlider()
            slider.Orientation = FBOrientation.kFBHorizontal
            slider.Min   = -100.0
            slider.Max   =  100.0
            slider.Value =    0.0
            slider.OnChange.Add(on_slider_change)
            row.AddRelative(slider, 1.0)
            
            g_sliders.append((slider, prop_name, pos_bs, neg_bs, None))

# ── Entry ─────────────────────────────────────────────────────────────────────

def main():
    tool = FBCreateUniqueTool("Facial CtrlRig — Sliders")
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        
        # Safe callback registration: clean up old instances to prevent stacking on reload
        try: FBSystem().OnUIIdle.Remove(_mc_idle_sync)
        except: pass
        try: FBSystem().OnUIIdle.Remove(_live_rig_sync)
        except: pass
        
        FBSystem().OnUIIdle.Add(_mc_idle_sync)
        FBSystem().OnUIIdle.Add(_live_rig_sync)

main()
