# -*- coding: utf-8 -*-
"""
MobuFacial_CtrlRig.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generate a 2D Face Board Control Rig for ARKit (52 Blendshapes).
Allows intuitive manual keying and baking raw capture data to editable controls.

Workflow:
  1. Click [Generate Face Board] to create UI Nulls in the viewport.
  2. Select target model (with ARKit BS) or LiveLink_Data node.
  3. Click [Connect Rig] to build Relation Constraints.
  4. (Optional) Click [Bake Animation to Rig] to transfer raw data for editing.

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pyfbsdk import *
from pyfbsdk_additions import *

# ── Mapping Configuration ─────────────────────────────────────────────────────

# Format: ControlName: { Axis: (Positive_BS, Negative_BS), ... }
# Position offsets are relative to the board center
FACE_BOARD_CONFIG = {
    "Mouth_Main": {
        "pos": (0, -20, 0),
        "color": FBColor(1, 0.5, 0),
        "mappings": {
            "Translation.Y": ("mouthSmileLeft,mouthSmileRight", "mouthFrownLeft,mouthFrownRight"),
            "Translation.X": ("mouthRight", "mouthLeft"),
            "Translation.Z": ("mouthPucker", "mouthFunnel"),
        }
    },
    "Mouth_Details": {
        "pos": (0, -35, 0),
        "color": FBColor(0.8, 0.4, 0),
        "mappings": {
            "Translation.X": ("mouthStretchRight", "mouthStretchLeft"),
            "Translation.Y": ("mouthDimpleRight", "mouthDimpleLeft"),
            "Translation.Z": ("mouthPressRight", "mouthPressLeft"),
        }
    },
    "Jaw": {
        "pos": (0, -50, 0),
        "color": FBColor(1, 1, 0),
        "mappings": {
            "Translation.Y": ("mouthClose", "jawOpen"),
            "Translation.X": ("jawRight", "jawLeft"),
            "Translation.Z": ("jawForward", ""),
        }
    },
    "Eye_L": {
        "pos": (25, 20, 0),
        "color": FBColor(0, 0.5, 1),
        "mappings": {
            "Translation.X": ("eyeLookOutLeft", "eyeLookInLeft"),
            "Translation.Y": ("eyeLookUpLeft", "eyeLookDownLeft"),
            "Translation.Z": ("eyeWideLeft", "eyeBlinkLeft"),
        }
    },
    "Eye_R": {
        "pos": (-25, 20, 0),
        "color": FBColor(0, 0.5, 1),
        "mappings": {
            "Translation.X": ("eyeLookInRight", "eyeLookOutRight"),
            "Translation.Y": ("eyeLookUpRight", "eyeLookDownRight"),
            "Translation.Z": ("eyeWideRight", "eyeBlinkRight"),
        }
    },
    "Brow_L": {
        "pos": (25, 45, 0),
        "color": FBColor(0, 0.8, 0.2),
        "mappings": {
            "Translation.Y": ("browOuterUpLeft", "browDownLeft"),
            "Translation.X": ("browInnerUp", ""),
        }
    },
    "Brow_R": {
        "pos": (-25, 45, 0),
        "color": FBColor(0, 0.8, 0.2),
        "mappings": {
            "Translation.Y": ("browOuterUpRight", "browDownRight"),
            "Translation.X": ("browInnerUp", ""),
        }
    },
    "Cheek_Nose": {
        "pos": (0, 0, 0),
        "color": FBColor(0.8, 0, 0.8),
        "mappings": {
            "Translation.X": ("cheekPuff", ""),
            "Translation.Y": ("noseSneerLeft,noseSneerRight", ""),
            "Translation.Z": ("cheekSquintLeft,cheekSquintRight", ""),
        }
    }
}

ARKIT_52 = [
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
    "noseSneerRight", "tongueOut"
]

# ── UI ────────────────────────────────────────────────────────────────────────
g_ui = {}

def get_board_root():
    for m in FBSystem().Scene.Components:
        try:
            if m.Name == "Facial_FaceBoard_Root":
                return m
        except (UnicodeDecodeError, AttributeError):
            continue
    return None

def OnGenerateBoardClick(control, event):
    root = get_board_root()
    if root:
        if FBMessageBox("Warning", "FaceBoard already exists. Re-generate?", "Yes", "No") == 2:
            return
        root.FBDelete()

    root = FBModelNull("Facial_FaceBoard_Root")
    root.Show = True
    root.Size = 100.0
    
    # Place root to the side of the scene
    root.Translation = FBVector3d(150, 150, 0)

    for ctrl_name, cfg in FACE_BOARD_CONFIG.items():
        ctrl = FBModelMarker("Ctrl_" + ctrl_name)
        ctrl.Parent = root
        ctrl.Show = True
        ctrl.Look = FBMarkerLook.kFBMarkerLookSphere
        ctrl.Size = 40.0
        ctrl.Color = cfg["color"]
        
        # Position relative to root
        off = cfg["pos"]
        ctrl.Translation = FBVector3d(off[0], off[1], off[2])
        
        # Set limits for translation to prevent rig from exploding
        ctrl.Translation.SetAnimated(True)
        
        # Enable limits in MB (Standard -15 to 15 range)
        ctrl.TranslationMinX = True
        ctrl.TranslationMinY = True
        ctrl.TranslationMinZ = True
        ctrl.TranslationMaxX = True
        ctrl.TranslationMaxY = True
        ctrl.TranslationMaxZ = True
        
        ctrl.TranslationMin = FBVector3d(-15, -15, -15)
        ctrl.TranslationMax = FBVector3d(15, 15, 15)

    FBSystem().Scene.Evaluate()
    g_ui["lbl_status"].Caption = "Generated FaceBoard Root and Controls."

def OnConnectRigClick(control, event):
    root = get_board_root()
    if not root:
        FBMessageBox("Error", "Please generate FaceBoard first!", "OK")
        return

    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Error", "Select a target model (with BS) or LiveLink_Data node!", "OK")
        return
    
    target_model = models[0]
    
    # Create or Find Relation Constraint
    rel_name = "Facial_CtrlRig_Link"
    relation = None
    for c in FBSystem().Scene.Constraints:
        try:
            if c.Name == rel_name:
                relation = c
                break
        except (UnicodeDecodeError, AttributeError):
            continue
    
    if relation:
        relation.Active = False
        relation.FBDelete()
    
    relation = FBConstraintRelation(rel_name)
    
    # Setup Boxes
    trgt_box = relation.ConstrainObject(target_model)
    relation.SetBoxPosition(trgt_box, 600, 100)
    
    # Map each control
    y_offset = 100
    for ctrl_name, cfg in FACE_BOARD_CONFIG.items():
        ctrl = None
        for child in root.Children:
            try:
                if child.Name == "Ctrl_" + ctrl_name:
                    ctrl = child; break
            except (UnicodeDecodeError, AttributeError):
                continue
        
        if not ctrl: continue
        
        src_box = relation.SetAsSource(ctrl)
        relation.SetBoxPosition(src_box, 100, y_offset)
        y_offset += 200
        
        src_out = src_box.AnimationNodeOutGet()
        trgt_in = trgt_box.AnimationNodeInGet()
        
        for axis_path, (pos_bs, neg_bs) in cfg["mappings"].items():
            # axis_path is e.g. "Translation.Y"
            axis_node = find_node(src_out, axis_path)
            if not axis_node: continue
            
            if pos_bs:
                for bs in pos_bs.split(','):
                    bs = bs.strip()
                    bs_node = find_node(trgt_in, bs)
                    if bs_node:
                        # Logic: If Axis > 0, then BS = Axis * Multiplier
                        # In MB Relation, we need a "Remap" or "Clamp"
                        # For simplicity, we use a Multiply operator if needed, 
                        # but standard ARKit is 0-1, MB is 0-100. 
                        # Our Ctrl range is -10 to 10. So multiplier is 10.
                        connect_with_math(relation, axis_node, bs_node, 10.0, True)
            
            if neg_bs:
                for bs in neg_bs.split(','):
                    bs = bs.strip()
                    bs_node = find_node(trgt_in, bs)
                    if bs_node:
                        # Logic: If Axis < 0, then BS = -Axis * Multiplier
                        connect_with_math(relation, axis_node, bs_node, 10.0, False)

    relation.Active = True
    FBSystem().Scene.Evaluate()
    g_ui["lbl_status"].Caption = "Rig connected to " + target_model.Name

def find_node(parent, name):
    parts = name.split('.')
    curr = parent
    for p in parts:
        found = False
        for node in curr.Nodes:
            try:
                if node.Name == p:
                    curr = node
                    found = True
                    break
            except (UnicodeDecodeError, AttributeError):
                continue
        if not found: return None
    return curr

def connect_with_math(relation, src_node, trgt_node, multiplier, is_positive):
    # Use "Mathematical" -> "Multiply" and "Number" -> "Clamp" or "Max"
    # Actually, MB has a "Remap" or we can just use "Condition"
    
    # Simple direct connection for now (assumes animator stays in positive/negative range)
    # A professional rig would use "Positive" and "Negative" filter boxes.
    # We will use "Math" -> "Product" and a "Constant"
    
    box_math = relation.CreateFunctionBox("Converters", "Inches to Centimeters") # Dummy to get a multiplier? No.
    # Better: "Number" -> "Scale"
    box_scale = relation.CreateFunctionBox("Number", "Multiply (Number)")
    
    # Set Constant for Multiplier
    const_box = relation.CreateFunctionBox("Number", "Number")
    const_box.PropertyList.Find("Value").Data = multiplier if is_positive else -multiplier
    
    relation.Connect(src_node, box_scale.AnimationNodeInGet().Nodes[0])
    relation.Connect(const_box.AnimationNodeOutGet().Nodes[0], box_scale.AnimationNodeInGet().Nodes[1])
    
    # Add a "Clamp" so it doesn't go negative on the Blendshape
    box_clamp = relation.CreateFunctionBox("Number", "Clamp")
    box_clamp.PropertyList.Find("Min").Data = 0.0
    box_clamp.PropertyList.Find("Max").Data = 100.0
    
    relation.Connect(box_scale.AnimationNodeOutGet().Nodes[0], box_clamp.AnimationNodeInGet().Nodes[0])
    relation.Connect(box_clamp.AnimationNodeOutGet().Nodes[0], trgt_node)

def OnBakeToRigClick(control, event):
    # Baking logic:
    # 1. Ensure a target model is connected.
    # 2. Iterate through takes or just current take.
    # 3. For each frame, look at BS values, calculate inverse translation for controls.
    # 4. Key the controls.
    
    root = get_board_root()
    if not root: return
    
    models = FBModelList()
    FBGetSelectedModels(models, None, True, True)
    if len(models) == 0:
        FBMessageBox("Error", "Select the target model/LiveLink node to bake FROM.", "OK")
        return
    
    src_model = models[0]
    take = FBSystem().CurrentTake
    span = take.LocalTimeSpan
    start = span.GetStart()
    end = span.GetEnd()
    
    step = FBTime(0, 0, 0, 1) # 1 frame
    curr = FBTime(start)
    
    progress = FBProgress()
    progress.Caption = "Baking Blendshapes to Rig..."
    
    # We need to temporarily disable the Relation Constraint to prevent feedback
    rel_name = "Facial_CtrlRig_Link"
    relation = None
    for c in FBSystem().Scene.Constraints:
        if c.Name == rel_name:
            relation = c; break
            
    if relation: relation.Active = False
    
    while curr <= end:
        FBSystem().LocalTime = curr
        # Calculate for each control
        for ctrl_name, cfg in FACE_BOARD_CONFIG.items():
            ctrl = None
            for child in root.Children:
                try:
                    if child.Name == "Ctrl_" + ctrl_name:
                        ctrl = child; break
                except (UnicodeDecodeError, AttributeError):
                    continue
            if not ctrl: continue
            
            tx, ty, tz = 0, 0, 0
            # Inverse mapping: Translation.Y = (PosBS - NegBS) / 10.0
            for axis_path, (pos_bs, neg_bs) in cfg["mappings"].items():
                val_pos = 0
                if pos_bs:
                    for bs in pos_bs.split(','):
                        p = src_model.PropertyList.Find(bs.strip())
                        if p: val_pos = max(val_pos, p.Data)
                
                val_neg = 0
                if neg_bs:
                    for bs in neg_bs.split(','):
                        p = src_model.PropertyList.Find(bs.strip())
                        if p: val_neg = max(val_neg, p.Data)
                
                final_val = (val_pos - val_neg) / 10.0
                if "X" in axis_path: tx = final_val
                elif "Y" in axis_path: ty = final_val
                elif "Z" in axis_path: tz = final_val
                
            # Set values relative to rig default
            off = cfg["pos"]
            ctrl.Translation = FBVector3d(off[0] + tx, off[1] + ty, off[2] + tz)
            ctrl.Translation.Key()
            
        curr += step
        
    if relation: relation.Active = True
    progress.Done()
    FBMessageBox("Success", "Baking Complete!", "OK")

def OnResetRigClick(control, event):
    root = get_board_root()
    if not root: return
    for ctrl_name, cfg in FACE_BOARD_CONFIG.items():
        for child in root.Children:
            try:
                if child.Name == "Ctrl_" + ctrl_name:
                    off = cfg["pos"]
                    child.Translation = FBVector3d(off[0], off[1], off[2])
                    break
            except (UnicodeDecodeError, AttributeError):
                continue
    FBSystem().Scene.Evaluate()

def PopulateTool(tool):
    tool.StartSizeX = 300
    tool.StartSizeY = 350
    
    ly = FBVBoxLayout()
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    tool.SetControl("main", ly)
    
    t = FBLabel(); t.Caption = "Facial Control Rig (Face Board)"; t.Justify = FBTextJustify.kFBTextJustifyCenter
    ly.Add(t, 40)
    
    b1 = FBButton(); b1.Caption = "1. Generate Face Board"; b1.OnClick.Add(OnGenerateBoardClick)
    ly.Add(b1, 40)
    
    b2 = FBButton(); b2.Caption = "2. Connect Rig to Selection"; b2.OnClick.Add(OnConnectRigClick)
    ly.Add(b2, 40)
    
    b3 = FBButton(); b3.Caption = "3. Bake Animation to Rig"; b3.OnClick.Add(OnBakeToRigClick)
    ly.Add(b3, 40)
    
    b4 = FBButton(); b4.Caption = "Reset Rig Positions"; b4.OnClick.Add(OnResetRigClick)
    ly.Add(b4, 40)
    
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Ready."
    ly.Add(g_ui["lbl_status"], 30)

def main():
    tool_name = "Facial CtrlRig Toolkit"
    tool = FBCreateUniqueTool(tool_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)

main()
