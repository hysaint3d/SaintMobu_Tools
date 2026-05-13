# -*- coding: utf-8 -*-
"""
MobuOpenPose_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Generate an OpenPose BODY_25 compliant skeleton for AI identification.
Supports auto-characterization for HIK retargeting.

By SaintMocap & Antigravity
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pyfbsdk import *
from pyfbsdk_additions import *
import os
import json

# ── OpenPose BODY_25 Data ───────────────────────────────────────────────────

# Index to Name mapping
OP_KEYPOINTS = {
    0: "Nose", 1: "Neck", 2: "RShoulder", 3: "RElbow", 4: "RWrist",
    5: "LShoulder", 6: "LElbow", 7: "LWrist", 8: "MidHip", 9: "RHip",
    10: "RKnee", 11: "RAnkle", 12: "LHip", 13: "LKnee", 14: "LAnkle",
    15: "REye", 16: "LEye", 17: "REar", 18: "LEar", 19: "LBigToe",
    20: "LSmallToe", 21: "LHeel", 22: "RBigToe", 23: "RSmallToe", 24: "RHeel"
}

# Hierarchy (Child: Parent)
# Root is MidHip (8)
OP_HIERARCHY = {
    8: None,        # Root
    1: 8,           # Neck attached to MidHip
    0: 1,           # Nose attached to Neck
    15: 0, 16: 0,   # Eyes attached to Nose
    17: 15, 18: 16, # Ears attached to Eyes
    2: 1, 3: 2, 4: 3,   # Right Arm
    5: 1, 6: 5, 7: 6,   # Left Arm
    9: 8, 10: 9, 11: 10, # Right Leg
    24: 11, 22: 11, 23: 11, # Right Foot (Heel, BigToe, SmallToe)
    12: 8, 13: 12, 14: 13, # Left Leg
    21: 14, 19: 14, 20: 14  # Left Foot
}

# Default Positions (X, Y, Z) for 170cm T-Pose
# Based on MobuCharacter_Toolkit standard proportions
OP_POS = {
    8: (0, 96, 0),     # MidHip
    1: (0, 140, 0),    # Neck
    0: (0, 155, 5),    # Nose
    15: (-3, 158, 8),  # REye
    16: (3, 158, 8),   # LEye
    17: (-7, 158, 2),  # REar
    18: (7, 158, 2),   # LEar
    2: (-18, 140, 0),  # RShoulder
    3: (-42, 140, 0),  # RElbow
    4: (-64, 140, 0),  # RWrist
    5: (18, 140, 0),   # LShoulder
    6: (42, 140, 0),   # LElbow
    7: (64, 140, 0),   # LWrist
    9: (-9, 96, 0),    # RHip
    10: (-9, 52, 0),   # RKnee
    11: (-9, 8, 0),    # RAnkle
    24: (-9, 0, -2),   # RHeel
    22: (-11, 0, 8),   # RBigToe
    23: (-7, 0, 8),    # RSmallToe
    12: (9, 96, 0),    # LHip
    13: (9, 52, 0),    # LKnee
    14: (9, 8, 0),     # LAnkle
    21: (9, 0, -2),    # LHeel
    19: (11, 0, 8),    # LBigToe
    20: (7, 0, 8)      # LSmallToe
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_script_dir():
    if "__file__" in globals():
        return os.path.dirname(__file__)
    return r"c:\Users\hysaint\Desktop\Antigravity\SaintMobu_Tools\MobuCharacter_Toolkit"

def get_bone_name(index):
    name = OP_KEYPOINTS.get(index, "Unknown")
    return "OP_{:02d}_{}".format(index, name)

def get_side_color(index):
    name = OP_KEYPOINTS[index]
    if name.startswith("R"): return FBColor(0.8, 0.2, 0.2) # Red
    if name.startswith("L"): return FBColor(0.2, 0.4, 0.8) # Blue
    return FBColor(0.8, 0.8, 0.2) # Yellow/Gold for Center

# ── Core Logic ────────────────────────────────────────────────────────────────

def generate_skeleton(height=170.0, namespace=""):
    scale = height / 170.0
    ns = namespace + ":" if namespace and not namespace.endswith(":") else namespace
    
    # Root Reference
    root_name = ns + "OP_Root"
    root = FBModelSkeleton(root_name)
    root.Show = True
    root.SetVector(FBVector3d(0, 0, 0), FBModelTransformationType.kModelTranslation, True)
    
    models = {}
    
    # Create all bones
    for idx, name in OP_KEYPOINTS.items():
        fname = ns + get_bone_name(idx)
        m = FBModelSkeleton(fname)
        m.Show = True
        
        # Set Position
        x, y, z = OP_POS[idx]
        m.SetVector(FBVector3d(x*scale, y*scale, z*scale), FBModelTransformationType.kModelTranslation, True)
        
        # Set Color
        m.Color = get_side_color(idx)
        
        models[idx] = m
        
    # Parent bones
    for child_idx, parent_idx in OP_HIERARCHY.items():
        child_m = models.get(child_idx)
        if not child_m: continue
        
        if parent_idx is None:
            child_m.Parent = root
        else:
            parent_m = models.get(parent_idx)
            if parent_m:
                child_m.Parent = parent_m
                
    FBSystem().Scene.Evaluate()
    return root, models

def characterize_skeleton(namespace=""):
    ns = namespace + ":" if namespace and not namespace.endswith(":") else namespace
    char_name = ns + "OpenPose_Character"
    
    # Delete existing character if any
    for c in list(FBSystem().Scene.Characters):
        if c.Name == char_name:
            c.FBDelete()
            
    char = FBCharacter(char_name)
    char.SetCharacterizeOn(False)
    
    # Load Template
    template_path = os.path.join(get_script_dir(), "Templates", "OpenPose_BODY_25.json")
    if not os.path.exists(template_path):
        print("Template not found: " + template_path)
        return False
        
    with open(template_path, 'r') as f:
        tdata = json.load(f)
        
    # Map bones
    matched = 0
    for prop_name, bone_name in tdata.items():
        if not prop_name.endswith("Link"): continue
        
        full_bone_name = ns + bone_name
        model = None
        for m in FBSystem().Scene.Components:
            if isinstance(m, FBModel) and (m.LongName == full_bone_name or m.Name == bone_name):
                model = m; break
        
        if model:
            prop = char.PropertyList.Find(prop_name)
            if prop:
                prop.removeAll()
                prop.append(model)
                matched += 1
                
    FBSystem().Scene.Evaluate()
    ok = char.SetCharacterizeOn(True)
    return ok, char_name, matched

# ── UI ────────────────────────────────────────────────────────────────────────

class OpenPoseToolkit(FBTool):
    def build_gui(self):
        x = FBAddRegionParam(10, FBAttachType.kFBAttachLeft, "")
        y = FBAddRegionParam(10, FBAttachType.kFBAttachTop, "")
        w = FBAddRegionParam(-10, FBAttachType.kFBAttachRight, "")
        h = FBAddRegionParam(30, FBAttachType.kFBAttachNone, "")
        
        self.AddRegion("title", "title", 0, FBAttachType.kFBAttachLeft, "", 1.0, 0, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 30, FBAttachType.kFBAttachNone, "", 1.0)
        self.lbl_title = FBLabel()
        self.lbl_title.Caption = "OpenPose Skeleton Generator"
        self.lbl_title.Justify = FBTextJustify.kFBTextJustifyCenter
        self.SetControl("title", self.lbl_title)
        
        curr_y = 40
        # Namespace
        self.AddRegion("reg_ns_lbl", "reg_ns_lbl", 10, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, 100, FBAttachType.kFBAttachNone, "", 1.0, 25, FBAttachType.kFBAttachNone, "", 1.0)
        l = FBLabel(); l.Caption = "Namespace:"; self.SetControl("reg_ns_lbl", l)
        self.AddRegion("reg_ns", "reg_ns", 110, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 25, FBAttachType.kFBAttachNone, "", 1.0)
        self.edit_ns = FBEdit(); self.edit_ns.Text = "OpenPose"; self.SetControl("reg_ns", self.edit_ns)
        
        curr_y += 30
        # Height
        self.AddRegion("reg_h_lbl", "reg_h_lbl", 10, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, 100, FBAttachType.kFBAttachNone, "", 1.0, 25, FBAttachType.kFBAttachNone, "", 1.0)
        l = FBLabel(); l.Caption = "Height (cm):"; self.SetControl("reg_h_lbl", l)
        self.AddRegion("reg_h", "reg_h", 110, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 25, FBAttachType.kFBAttachNone, "", 1.0)
        self.edit_height = FBEditNumber(); self.edit_height.Value = 170.0; self.SetControl("reg_h", self.edit_height)
        
        curr_y += 40
        # Buttons
        self.AddRegion("reg_btn_gen", "reg_btn_gen", 10, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 40, FBAttachType.kFBAttachNone, "", 1.0)
        self.btn_gen = FBButton(); self.btn_gen.Caption = "Generate Skeleton"; self.btn_gen.OnClick.Add(self.on_gen_click); self.SetControl("reg_btn_gen", self.btn_gen)
        
        curr_y += 50
        self.AddRegion("reg_btn_char", "reg_btn_char", 10, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 40, FBAttachType.kFBAttachNone, "", 1.0)
        self.btn_char = FBButton(); self.btn_char.Caption = "Characterize (HIK)"; self.btn_char.OnClick.Add(self.on_char_click); self.SetControl("reg_btn_char", self.btn_char)
        
        curr_y += 60
        self.AddRegion("reg_status", "reg_status", 10, FBAttachType.kFBAttachLeft, "", 1.0, curr_y, FBAttachType.kFBAttachTop, "", 1.0, -10, FBAttachType.kFBAttachRight, "", 1.0, 30, FBAttachType.kFBAttachNone, "", 1.0)
        self.lbl_status = FBLabel(); self.lbl_status.Caption = "Ready."; self.SetControl("reg_status", self.lbl_status)

    def __init__(self, name):
        FBTool.__init__(self, name)
        self.build_gui()
        self.StartSizeX = 300
        self.StartSizeY = 350

    def on_gen_click(self, control, event):
        ns = self.edit_ns.Text
        h = self.edit_height.Value
        root, models = generate_skeleton(h, ns)
        self.lbl_status.Caption = "Skeleton generated with namespace: " + ns

    def on_char_click(self, control, event):
        ns = self.edit_ns.Text
        ok, char_name, matched = characterize_skeleton(ns)
        if ok:
            self.lbl_status.Caption = "Characterized: " + char_name + " (" + str(matched) + " bones)"
        else:
            self.lbl_status.Caption = "Characterization failed! Check console."

def main():
    tool_name = "OpenPose Skeleton Toolkit"
    # Close existing tool
    for t in FBSystem().Scene.Tools:
        if t.Name == tool_name:
            t.FBDelete()
    
    tool = OpenPoseToolkit(tool_name)
    if tool:
        ShowTool(tool)

if __name__ in ("__main__", "__builtin__"):
    main()
