"""
MobuOptical_Toolkit.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Automate Optical Mocap setup for OptiTrack, Vicon, and custom systems via JSON.
Workflow:
  [Actor] Select Template -> Import Data -> Create RigidBodies -> Create & Fit Actor -> AutoMap
  [DataClean] Delete Unlabeled -> Set PostProcess (Done) -> Apply Filters (Peak Removal, Butterworth, Smooth)

Version: 2.0 (Template-Driven Architecture)
由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
print(">>> MobuOptical_Toolkit v2.0 Loading...")
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

def create_guide(name, pos, color=(1, 0, 0)):
    null_name = "Guide_" + name
    null = FBFindModelByLabelName(null_name)
    if not null:
        null = FBModelMarker(null_name)
    null.Show = True
    null.Size = 300.0
    null.Look = FBMarkerLook.kFBMarkerLookBone
    
    # Try to show name label
    p_show = null.PropertyList.Find("ShowName")
    if p_show: p_show.Data = True
    
    null.Color = FBColor(color[0], color[1], color[2])
    null.SetVector(FBVector3d(pos[0], pos[1], pos[2]))
    return null

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
        mlist = [m for m in optical.Children if m.Name.split(":")[-1].replace("_", " ").split()[-1] in m_names]
        if len(mlist) >= 3:
            if optical.CreateRigidBody(g_name, mlist): 
                created += 1
                        
    status("Created {} Rigid Bodies.".format(created))

def OnAnalyzeQualityClick(control, event):
    roots = get_optical_roots()
    if not roots: status("No Optical Data!"); return
    optical = roots[0]
    
    if len(optical.RigidBodies) == 0:
        status("No Rigid Bodies to analyze."); return
        
    print("\n--- Rigid Body Quality Analysis ---")
    worst_rb = None; worst_q = 0.0
    
    for rb in optical.RigidBodies:
        q_prop = rb.PropertyList.Find("Quality")
        q_val = q_prop.Data if q_prop else 0.0
        print("  - {}: {:.4f}".format(rb.Name, q_val))
        if q_val > worst_q:
            worst_q = q_val; worst_rb = rb.Name
            
    if worst_rb:
        status("Analysis complete. Worst: {} ({:.2f})".format(worst_rb, worst_q))
    else:
        status("Analysis complete.")

def get_marker_pos(optical, names, fuzzy=False):
    pts = []
    for m in optical.Children:
        clean_name = m.Name.split(":")[-1].replace("_", "")
        low_name = clean_name.lower()
        if clean_name in names or m.Name.split(":")[-1] in names:
            p = FBVector3d()
            m.GetVector(p)
            pts.append(p)
        elif fuzzy:
            for n in names:
                if n.lower() in low_name:
                    p = FBVector3d()
                    m.GetVector(p)
                    pts.append(p)
                    break
    if pts:
        return FBVector3d(sum(p[0] for p in pts)/len(pts), sum(p[1] for p in pts)/len(pts), sum(p[2] for p in pts)/len(pts))
    return None

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
    
    rules = template.get("FittingRules", {})
    f_markers = template.get("FittingMarkers", {})
    
    r_hips_offset = rules.get("HipsOffset", [0.0, -10.0, -4.0]) 
    r_head_offset = rules.get("HeadHeightOffset", 10.0) 
    
    def get_pm(key, defaults): return get_marker_pos(optical, f_markers.get(key, defaults), fuzzy=True)
    
    p_head = get_pm("HeadTop", ["HeadTop", "HeadFront"])
    p_waist = get_pm("Waist", ["WaistLFront", "WaistRFront", "WaistLBack", "WaistRBack", "LASI", "RASI"])
    p_l_ank = get_pm("LeftAnkle", ["LAnkleOut"])
    p_r_ank = get_pm("RightAnkle", ["RAnkleOut"])
    
    p_hips = p_waist 
    if not p_hips:
        print(">>> WARNING: No waist markers found! Actor position not updated.")
        status("Missing Waist Markers!")
        return
        
    actor.Selected = True
    
    # Base Floor height
    floor_y = 0.0
    if p_l_ank and p_r_ank: floor_y = min(p_l_ank[1], p_r_ank[1])
    elif p_l_ank: floor_y = p_l_ank[1]
    
    # 1. Slide the entire Actor to the point cloud
    if p_hips:
        p_hips_prop = actor.PropertyList.Find("HipsPosition")
        if p_hips_prop:
            try:
                hx = float(p_hips[0] + r_hips_offset[0])
                hy = float(p_hips[1] + r_hips_offset[1]) # Default: ~10cm below waist
                hz = float(p_hips[2] + r_hips_offset[2]) # Default: ~4cm backward
                p_hips_prop.Data = FBVector3d(hx, hy, hz)
            except:
                try: p_hips_prop.Data = [hx, hy, hz]
                except: pass
                        
    FBSystem().Scene.Evaluate()
    
    # 2. Scale the Actor based on Height Proportions
    if p_hips:
        hips_h = (p_hips[1] + r_hips_offset[1]) - floor_y
        set_prop(actor, "Hips Height", hips_h)
        set_prop(actor, "HipsHeight", hips_h)
        
    if p_head:
        # Calculate Total Height
        total_h = p_head[1] - floor_y + r_head_offset
        if total_h > 50.0: 
            set_prop(actor, "Height", total_h)
            
            # --- Simple Proportional Formula ---
            # Based on standard 170cm Actor relative pivot values
            S = total_h / 170.0
            
            def set_actor_pivot(prop_name, pos):
                prop = actor.PropertyList.Find(prop_name)
                if prop:
                    try: prop.Data = FBVector3d(pos[0], pos[1], pos[2])
                    except:
                        try: prop.Data = [pos[0], pos[1], pos[2]]
                        except: pass
            
            # Legs
            set_actor_pivot("LeftHipPosition", [9.60 * S, -3.60 * S, 7.30 * S])
            set_actor_pivot("RightHipPosition", [-9.60 * S, -3.60 * S, 7.30 * S])
            set_actor_pivot("LeftKneePosition", [0.0, -42.70 * S, -0.40 * S])
            set_actor_pivot("RightKneePosition", [0.0, -42.70 * S, -0.40 * S])
            set_actor_pivot("LeftAnklePosition", [0.0, -43.30 * S, -2.20 * S])
            set_actor_pivot("RightAnklePosition", [0.0, -43.30 * S, -2.20 * S])
            
            # Arms (Note: FBActor internal prop names differ from UI bone names)
            # UI: Left Shoulder -> Prop: LeftCollarPosition
            set_actor_pivot("LeftCollarPosition", [8.90 * S, 21.20 * S, 2.90 * S])
            set_actor_pivot("RightCollarPosition", [-8.90 * S, 21.20 * S, 2.90 * S])
            
            # UI: Left Arm -> Prop: LeftShoulderPosition
            set_actor_pivot("LeftShoulderPosition", [9.80 * S, 0.80 * S, 0.60 * S])
            set_actor_pivot("RightShoulderPosition", [-9.80 * S, 0.80 * S, 0.60 * S])
            
            # UI: Left Fore Arm -> Prop: LeftElbowPosition
            set_actor_pivot("LeftElbowPosition", [26.20 * S, 0.0, -1.70 * S])
            set_actor_pivot("RightElbowPosition", [-26.20 * S, 0.0, -1.70 * S])
            
            # UI: Left Hand -> Prop: LeftWristPosition
            set_actor_pivot("LeftWristPosition", [26.50 * S, 0.0, 0.40 * S])
            set_actor_pivot("RightWristPosition", [-26.50 * S, 0.0, 0.40 * S])
            
            # Spine / Head
            set_actor_pivot("WaistPosition", [0.0, 10.80 * S, 2.00 * S])
            set_actor_pivot("ChestPosition", [0.0, 14.00 * S, 1.30 * S])
            set_actor_pivot("NeckPosition", [0.0, 29.50 * S, 3.30 * S])
            set_actor_pivot("HeadPosition", [0.0, 9.30 * S, 2.70 * S])
            
    FBSystem().Scene.Evaluate()
    status("Actor Scaled (Proportional) & Positioned.")

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
                if marker.Name.split(":")[-1].replace("_", " ").split()[-1] in potential_names:
                    try: markerset.AddMarker(node_id, marker); match_count += 1
                    except: pass
    FBSystem().Scene.Evaluate(); status("Mapped {} markers.".format(match_count))

def OnActivateClick(control, event):
    actor = next((a for a in FBSystem().Scene.Actors if a.Name == "Optical_Actor"), None)
    if actor: set_prop(actor, "Active", True); status("Actor Activated.")

def OnDeleteUnlabeledClick(control, event):
    roots = get_optical_roots()
    if not roots: status("No Optical Data!"); return
    optical = roots[0]
    
    count = 0
    for m in list(optical.Children):
        name_lower = m.Name.lower()
        if "unlabel" in name_lower or "unname" in name_lower:
            m.FBDelete()
            count += 1
            
    status("Deleted {} unlabeled/unnamed markers.".format(count))

def ApplyFilterToSelectedMarkers(filter_name):
    selected_models = FBModelList()
    FBGetSelectedModels(selected_models)
    
    optical_markers = [m for m in selected_models if isinstance(m, FBModelMarker)]
    if not optical_markers:
        status("Please select optical markers first.")
        return
        
    filter_mgr = FBFilterManager()
    f = filter_mgr.CreateFilter(filter_name)
    if not f:
        status("{} filter not found!".format(filter_name))
        return
    try:
        applied_count = 0
        for m in optical_markers:
            anim_node = m.Translation.GetAnimationNode()
            if anim_node:
                f.Apply(anim_node, True)
                applied_count += 1
                
        status("{} applied to {} markers.".format(filter_name, applied_count))
    except Exception as e:
        print(">>> Filter Apply Error:", e)
        status("{} applied to {} markers (with warnings).".format(filter_name, len(optical_markers)))

def OnPeakRemovalClick(control, event):
    ApplyFilterToSelectedMarkers("Peak Removal")

def OnButterworthClick(control, event):
    ApplyFilterToSelectedMarkers("Butterworth")

def OnSetPostProcessClick(control, event):
    selected_models = FBModelList()
    FBGetSelectedModels(selected_models)
    
    optical_markers = [m for m in selected_models if isinstance(m, FBModelMarker)]
    if not optical_markers:
        status("Please select optical markers first.")
        return
        
    count = 0
    for m in optical_markers:
        done_prop = m.PropertyList.Find("Done")
        if done_prop:
            try: 
                done_prop.Data = True
                count += 1
            except: pass
            
    status("Set PostProcess (Done) on {} markers.".format(count))

def OnSmoothClick(control, event):
    ApplyFilterToSelectedMarkers("Smooth")

def OnResetClick(control, event):
    for a in list(FBSystem().Scene.Actors):
        if a.Name == "Optical_Actor": a.FBDelete()
    for ms in list(FBSystem().Scene.MarkerSets):
        if ms.Name == "Optical_MarkerSet": ms.FBDelete()
    
    # Safely delete Guide Nulls under RootModel
    for m in list(FBSystem().Scene.RootModel.Children):
        try:
            if m.Name.startswith("Guide_"): m.FBDelete()
        except:
            pass
    status("Scene Reset.")

# ── UI Construction ───────────────────────────────────────────────────────────

def PopulateTool(tool):
    tool.StartSizeX = 280; tool.StartSizeY = 550
    
    tabs = FBTabPanel()
    tabs.Items.append("Actor")
    tabs.Items.append("DataClean")
    
    lyt_actor = FBVBoxLayout()
    lyt_analysis = FBVBoxLayout()
    
    def on_tab_change(control, event):
        if control.ItemIndex == 0:
            tool.SetControl("main", lyt_actor)
        else:
            tool.SetControl("main", lyt_analysis)
    tabs.OnChange.Add(on_tab_change)
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(25, FBAttachType.kFBAttachNone, "")
    tool.AddRegion("tabs", "tabs", x, y, w, h)
    tool.SetControl("tabs", tabs)
    
    x = FBAddRegionParam(0, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "tabs")
    w = FBAddRegionParam(0, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(0, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("main", "main", x, y, w, h)
    tool.SetControl("main", lyt_actor)
    
    def btn(txt, fn): 
        b = FBButton(); b.Caption = txt; 
        b.Justify = FBTextJustify.kFBTextJustifyCenter
        b.OnClick.Add(fn); return b
        
    def lbl(txt): 
        l = FBLabel(); l.Caption = txt; 
        l.Justify = FBTextJustify.kFBTextJustifyCenter
        return l

    # --- ACTOR TAB ---
    lyt_actor.Add(lbl("1. Data & Template Selection"), 20)
    lyt_temp = FBHBoxLayout(); lyt_temp.Add(lbl("Template:"), 60)
    g_ui["list_template"] = FBList()
    
    load_templates()
    for tmpl in g_templates:
        g_ui["list_template"].Items.append(tmpl.get("TemplateName", "Unknown"))
    if g_templates:
        g_ui["list_template"].ItemIndex = 0
    else:
        g_ui["list_template"].Items.append("No templates found")
        
    lyt_temp.Add(g_ui["list_template"], 140); lyt_actor.Add(lyt_temp, 25)
    
    lyt_actor.Add(btn("Import Optical Data", OnImportClick), 35)
    
    lyt_actor.Add(lbl("2. Rigid Bodies & Actor"), 20)
    
    lyt_actor.Add(btn("Create Rigid Bodies", OnCreateRigidClick), 35)
    lyt_actor.Add(btn("Create & Fit Actor", OnCreateAndFitClick), 45)
    
    lyt_actor.Add(lbl("3. Auto-Mapping"), 20)
    lyt_actor.Add(btn("Auto-Map MarkerSet", OnAutoMapClick), 40)
    lyt_actor.Add(btn("Activate Mapping", OnActivateClick), 40)
    
    lyt_actor.Add(lbl("---"), 15)
    lyt_actor.Add(btn("Reset / Delete All", OnResetClick), 35)
    
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Status: Ready."; lyt_actor.Add(g_ui["lbl_status"], 30)

    # --- DATACLEAN TAB ---
    lyt_analysis.Add(lbl("1. Label"), 20)
    lyt_analysis.Add(lbl("(Manual Labeling in Viewer)"), 15)
    
    lyt_analysis.Add(lbl("2. Filter"), 20)
    lyt_analysis.Add(btn("Set PostProcess", OnSetPostProcessClick), 35)
    lyt_analysis.Add(lbl("轉換為動態曲線 (Set Done)"), 15)
    
    lyt_analysis.Add(btn("Peak Removal", OnPeakRemovalClick), 35)
    lyt_analysis.Add(lbl("消除突刺雜訊"), 15)
    
    lyt_analysis.Add(btn("Butterworth", OnButterworthClick), 35)
    lyt_analysis.Add(lbl("消除高頻，保留主要動態"), 15)
    
    lyt_analysis.Add(btn("Smooth", OnSmoothClick), 35)
    lyt_analysis.Add(lbl("平滑曲線，平順降低動態"), 15)

    lyt_analysis.Add(lbl("3. Data"), 20)
    lyt_analysis.Add(btn("Delete Unlabeled Markers", OnDeleteUnlabeledClick), 35)
def CreateTool():
    t = FBCreateUniqueTool("MobuOptical_Toolkit")
    if t: PopulateTool(t); ShowTool(t)
CreateTool()
