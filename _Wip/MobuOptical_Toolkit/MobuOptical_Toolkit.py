"""
MobuOptical_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Automate Optical Mocap setup for OptiTrack Baseline 37 & Core 50.
Workflow:
  Import -> RigidBody -> Create & Fit -> AutoMap -> Active

Version: 1.24 (Clean Workflow)
由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
print(">>> MobuOptical_Toolkit v1.24 Loading...")
from pyfbsdk import *
from pyfbsdk_additions import *
import sys
import math

import os
import json

# ── Global Settings & Mapping ──────────────────────────────────────────────────
g_ui = {}
g_templates = []

ID_MAP = {
    "Reference": FBSkeletonNodeId.kFBSkeletonReferenceIndex,
    "Head": FBSkeletonNodeId.kFBSkeletonHeadIndex, "Chest": FBSkeletonNodeId.kFBSkeletonChestIndex,
    "Spine": FBSkeletonNodeId.kFBSkeletonWaistIndex, "Hips": FBSkeletonNodeId.kFBSkeletonHipsIndex,
    "LeftShoulder": FBSkeletonNodeId.kFBSkeletonLeftCollarIndex, "RightShoulder": FBSkeletonNodeId.kFBSkeletonRightCollarIndex,
    "LeftArm": FBSkeletonNodeId.kFBSkeletonLeftShoulderIndex, "RightArm": FBSkeletonNodeId.kFBSkeletonRightShoulderIndex,
    "LeftForeArm": FBSkeletonNodeId.kFBSkeletonLeftElbowIndex, "RightForeArm": FBSkeletonNodeId.kFBSkeletonRightElbowIndex,
    "LeftHand": FBSkeletonNodeId.kFBSkeletonLeftWristIndex, "RightHand": FBSkeletonNodeId.kFBSkeletonRightWristIndex,
    "LeftUpLeg": FBSkeletonNodeId.kFBSkeletonLeftHipIndex, "RightUpLeg": FBSkeletonNodeId.kFBSkeletonRightHipIndex,
    "LeftLeg": FBSkeletonNodeId.kFBSkeletonLeftKneeIndex, "RightLeg": FBSkeletonNodeId.kFBSkeletonRightKneeIndex,
    "LeftFoot": FBSkeletonNodeId.kFBSkeletonLeftAnkleIndex, "RightFoot": FBSkeletonNodeId.kFBSkeletonRightAnkleIndex,
    "LeftToe": FBSkeletonNodeId.kFBSkeletonLeftFootIndex, "RightToe": FBSkeletonNodeId.kFBSkeletonRightFootIndex,
}

# Dynamically add finger indices to ID_MAP
for side in ["Left", "Right"]:
    for finger in ["Thumb", "Index", "Middle", "Ring", "Pinky"]:
        for i in range(1, 5):
            key = "{side}Hand{finger}{i}".format(side=side, finger=finger, i=i)
            attr_name = "kFBSkeleton{}Index".format(key)
            if hasattr(FBSkeletonNodeId, attr_name):
                ID_MAP[key] = getattr(FBSkeletonNodeId, attr_name)

def load_templates():
    global g_templates
    g_templates = []
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
    except:
        script_dir = r"c:\Users\hysaint\Desktop\Antigravity\SaintMobu_Tools\_Wip\MobuOptical_Toolkit"
    
    template_dir = os.path.join(script_dir, "Templates")
    if not os.path.exists(template_dir):
        return
        
    for f in os.listdir(template_dir):
        if f.endswith(".json"):
            try:
                with open(os.path.join(template_dir, f), "r") as jf:
                    data = json.load(jf)
                    if "TemplateName" in data:
                        g_templates.append(data)
            except Exception as e:
                print("Error loading {}: {}".format(f, e))

g_ui = {}

def status(msg):
    try: g_ui["lbl_status"].Caption = "Status: " + msg
    except: pass

def set_prop(obj, name, val):
    p = obj.PropertyList.Find(name)
    if not p:
        for _p in obj.PropertyList:
            if name.lower() in _p.Name.lower():
                p = _p; break
    if p:
        try: p.Data = val; return True
        except: pass
    return False

def get_optical_roots():
    roots = []
    for comp in FBSystem().Scene.Components:
        if isinstance(comp, FBModelOptical):
            roots.append(comp)
    return roots

# ── Actions ────────────────────────────────────────────────────────────────────

def OnImportClick(control, event):
    file_popup = FBFilePopup()
    file_popup.Style = FBFilePopupStyle.kFBFilePopupOpen
    file_popup.Filter = "*.c3d;*.trc"; file_popup.Caption = "Select Optical Data"
    if file_popup.Execute():
        FBApplication().FileImport(file_popup.FullFilename)
        status("Imported: " + file_popup.FileName)

def OnCreateRigidClick(control, event):
    roots = get_optical_roots()
    if not roots: status("No Optical Data!"); return
    optical = roots[0]; created = 0
    
    idx = g_ui["list_template"].ItemIndex
    if idx < 0 or idx >= len(g_templates):
        status("Select a template first."); return
        
    template = g_templates[idx]
    rigid_groups = template.get("RigidBodies", {})
    
    for g_name, m_names in rigid_groups.items():
        mlist = [m for m in optical.Children if m.Name.split(":")[-1] in m_names]
        if len(mlist) >= 3:
            if optical.CreateRigidBody(g_name, mlist): created += 1
    status("Created {} Rigid Bodies.".format(created))

def OnCreateAndFitClick(control, event):
    # 1. Ensure Actor exists
    actor_name = "Optical_Actor"
    actor = next((a for a in FBSystem().Scene.Actors if a.Name == actor_name), None)
    if not actor: actor = FBActor(actor_name)
    
    roots = get_optical_roots()
    if not roots: status("No Optical Data!"); return
    optical = roots[0]
    
    idx = g_ui["list_template"].ItemIndex
    if idx < 0 or idx >= len(g_templates):
        status("Select a template first."); return
    template = g_templates[idx]
    
    # Get hip marker names from template
    hip_names = template.get("ActorMapping", {}).get("Hips", [])
    if not hip_names:
        hip_names = ["WaistLFront", "WaistRFront", "WaistLBack", "WaistRBack", "LASI", "RASI", "LPSI", "RPSI"]
    
    # 2. Analyze Optical Markers
    max_y = -100000.0; min_y = 100000.0
    waist_markers = []; pts = {}
    for marker in optical.Children:
        p = FBVector3d(); marker.GetVector(p)
        if p[1] > max_y: max_y = p[1]
        if p[1] < min_y: min_y = p[1]
        name = marker.Name.split(":")[-1]; pts[name] = p
        
        if name in hip_names or "Waist" in name or "ASI" in name or "PSI" in name:
            waist_markers.append(p)
    
    def dist(p1, p2):
        return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2 + (p1[2]-p2[2])**2) if p1 and p2 else None

    # 3. Positioning & Scaling (Fuzzy Search for properties)
    actor.Selected = True
    
    # Scale Height first (Optional, but good for base scale)
    height = max_y - min_y
    if height > 50.0: set_prop(actor, "Height", height)
    
    if waist_markers:
        avg_x = sum(p[0] for p in waist_markers) / len(waist_markers)
        avg_y = sum(p[1] for p in waist_markers) / len(waist_markers)
        avg_z = sum(p[2] for p in waist_markers) / len(waist_markers)
        
        # 暫時不移動 Actor 的位置，將其保持在原點 (0,0,0)
        # try:
        #     actor.SetActorTranslation(FBVector3d(avg_x, min_y, avg_z))
        # except:
        #     set_prop(actor, "Translation", FBVector3d(avg_x, min_y, avg_z))
        
        # Hips Height 暫時維持絕對高度計算，以便稍後找問題
        hips_h = avg_y
        
        found_hips = False
        for p in actor.PropertyList:
            p_low = p.Name.lower()
            if "hip" in p_low and "height" in p_low:
                p.Data = hips_h
                print(">>> Found & Set Hip Height: {} = {}".format(p.Name, hips_h))
                found_hips = True
        if not found_hips: 
            set_prop(actor, "Hips Height", hips_h)
            set_prop(actor, "HipsHeight", hips_h)

    # Scale Other Limbs
    l_sho = pts.get("LShoulderTop"); r_sho = pts.get("RShoulderTop")
    if l_sho: set_prop(actor, "ShoulderHeight", l_sho[1] - min_y)
    if l_sho and r_sho: set_prop(actor, "ShoulderWidth", abs(l_sho[0] - r_sho[0]))
    
    l_elb = pts.get("LElbowOut"); l_wri = pts.get("LWristOut")
    if l_sho and l_elb: set_prop(actor, "ArmLength", dist(l_sho, l_elb))
    if l_elb and l_wri: set_prop(actor, "ForearmLength", dist(l_elb, l_wri))
    
    l_kne = pts.get("LKneeOut"); l_ank = pts.get("LAnkleOut")
    l_waist_ref = pts.get("WaistLFront") or pts.get("LASI")
    if l_waist_ref and l_kne: set_prop(actor, "UpperLegLength", dist(l_waist_ref, l_kne))
    if l_kne and l_ank: set_prop(actor, "LowerLegLength", dist(l_kne, l_ank))

    FBSystem().Scene.Evaluate()
    status("Actor Created & Fitted.")

def OnAutoMapClick(control, event):
    actor = next((a for a in FBSystem().Scene.Actors if a.Name == "Optical_Actor"), None)
    roots = get_optical_roots()
    if not actor or not roots: status("Missing Actor/Data!"); return
    optical = roots[0]
    
    ms_name = "Optical_MarkerSet"
    markerset = next((ms for ms in FBSystem().Scene.MarkerSets if ms.Name == ms_name), None)
    if not markerset: markerset = FBMarkerSet(ms_name)
    actor.MarkerSet = markerset
    
    for p in markerset.PropertyList:
        if hasattr(p, "removeAll"): p.removeAll()
    
    template_idx = g_ui["list_template"].ItemIndex
    if template_idx < 0 or template_idx >= len(g_templates):
        status("Select a template first."); return
        
    mapping = g_templates[template_idx].get("ActorMapping", {})
    match_count = 0
    
    for slot_name, potential_names in mapping.items():
        if slot_name == "Reference":
            try:
                markerset.AddMarker(FBSkeletonNodeId.kFBSkeletonReferenceIndex, optical)
                print(">>> Assigned Reference via AddMarker")
            except Exception as e:
                print(">>> AddMarker Ref error:", e)
                
            for p in actor.PropertyList:
                if "reference" in p.Name.lower() or "ref" in p.Name.lower():
                    try: p.removeAll(); p.append(optical); print(">>> Mapped Actor prop:", p.Name)
                    except: pass
            for p in markerset.PropertyList:
                if "reference" in p.Name.lower() or "ref" in p.Name.lower():
                    try: p.removeAll(); p.append(optical); print(">>> Mapped MarkerSet prop:", p.Name)
                    except: pass
            continue
            
        node_id = ID_MAP.get(slot_name)
        if node_id is not None:
            for marker in optical.Children:
                if marker.Name.split(":")[-1] in potential_names:
                    try: markerset.AddMarker(node_id, marker); match_count += 1
                    except: pass
    FBSystem().Scene.Evaluate(); status("Mapped {} markers.".format(match_count))

def OnActivateClick(control, event):
    actor = next((a for a in FBSystem().Scene.Actors if a.Name == "Optical_Actor"), None)
    if actor: set_prop(actor, "Active", True); status("Actor Activated.")

def OnResetClick(control, event):
    for a in list(FBSystem().Scene.Actors):
        if a.Name == "Optical_Actor": a.FBDelete()
    for ms in list(FBSystem().Scene.MarkerSets):
        if ms.Name == "Optical_MarkerSet": ms.FBDelete()
    status("Scene Reset.")

# ── UI Construction ───────────────────────────────────────────────────────────

def PopulateTool(tool):
    tool.StartSizeX = 260; tool.StartSizeY = 500
    lyt = FBVBoxLayout()
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, ""); y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, ""); h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h); tool.SetControl("main", lyt)
    
    def btn(txt, fn): b = FBButton(); b.Caption = txt; b.OnClick.Add(fn); return b
    def lbl(txt): l = FBLabel(); l.Caption = txt; return l

    lyt.Add(lbl("1. Data & Template Selection"), 20)
    lyt_temp = FBHBoxLayout(); lyt_temp.Add(lbl("Template:"), 60)
    g_ui["list_template"] = FBList()
    
    load_templates()
    for tmpl in g_templates:
        g_ui["list_template"].Items.append(tmpl.get("TemplateName", "Unknown"))
    if g_templates:
        g_ui["list_template"].ItemIndex = 0
    else:
        g_ui["list_template"].Items.append("No templates found")
        
    lyt_temp.Add(g_ui["list_template"], 140); lyt.Add(lyt_temp, 25)
    
    lyt.Add(btn("Import Optical Data", OnImportClick), 35)
    
    lyt.Add(lbl("2. Rigid Bodies & Actor"), 20)
    lyt.Add(btn("Create Rigid Bodies", OnCreateRigidClick), 35)
    lyt.Add(btn("Create & Fit Actor", OnCreateAndFitClick), 45)
    
    lyt.Add(lbl("3. Auto-Mapping"), 20)
    lyt.Add(btn("Auto-Map MarkerSet", OnAutoMapClick), 40)
    lyt.Add(btn("Activate Mapping", OnActivateClick), 40)
    
    lyt.Add(lbl("---"), 15)
    lyt.Add(btn("Reset / Delete All", OnResetClick), 35)
    
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Status: Ready."; lyt.Add(g_ui["lbl_status"], 30)

def CreateTool():
    t = FBCreateUniqueTool("MobuOptical_Toolkit")
    if t: PopulateTool(t); ShowTool(t)
CreateTool()
