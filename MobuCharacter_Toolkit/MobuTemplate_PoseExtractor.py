# -*- coding: utf-8 -*-
"""
MobuTemplate_PoseExtractor.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Standalone tool to extract the current pose (Global Rotations) of a 
characterized HIK character and save it back into a JSON template.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pyfbsdk import *
from pyfbsdk_additions import *
import os
import json
from collections import OrderedDict

print(">>> PoseExtractor: Initializing...")

def get_script_dir():
    if "__file__" in globals():
        return os.path.dirname(__file__)
    # Fallback to current working directory or metadata path
    return r"c:\Users\hysaint\Desktop\Antigravity\SaintMobu_Tools\MobuCharacter_Toolkit"

def get_template_dir():
    return os.path.join(get_script_dir(), "Templates")

# Global UI handles
g_ui = {}
g_template_map = {}

def RefreshCharacters():
    print(">>> PoseExtractor: Refreshing characters...")
    g_ui["list_chars"].Items.removeAll()
    for char in FBSystem().Scene.Characters:
        g_ui["list_chars"].Items.append(char.Name)
    if g_ui["list_chars"].Items:
        g_ui["list_chars"].ItemIndex = 0

def RefreshTemplates():
    print(">>> PoseExtractor: Refreshing templates...")
    g_ui["list_tmpls"].Items.removeAll()
    global g_template_map
    g_template_map = {}
    t_dir = get_template_dir()
    print(">>> PoseExtractor: Template Dir: {}".format(t_dir))
    if os.path.exists(t_dir):
        files = [f for f in os.listdir(t_dir) if f.endswith(".json")]
        print(">>> PoseExtractor: Found {} json files".format(len(files)))
        for f in files:
            display_name = f.replace(".json", "")
            try:
                with open(os.path.join(t_dir, f), 'r', encoding='utf-8') as j:
                    d = json.load(j)
                    if "DisplayName" in d: display_name = d["DisplayName"]
            except: pass
            g_ui["list_tmpls"].Items.append(display_name)
            g_template_map[display_name] = f
    else:
        print(">>> PoseExtractor: Template directory NOT FOUND!")
    if g_ui["list_tmpls"].Items:
        g_ui["list_tmpls"].ItemIndex = 0

def OnExtractClick(control, event):
    c_idx = g_ui["list_chars"].ItemIndex
    t_idx = g_ui["list_tmpls"].ItemIndex
    
    if c_idx < 0 or t_idx < 0:
        FBMessageBox("Pose Extractor", "Please select a character and a template.", "OK")
        return
        
    char_name = g_ui["list_chars"].Items[c_idx]
    tmpl_disp = g_ui["list_tmpls"].Items[t_idx]
    tmpl_file = g_template_map.get(tmpl_disp)
    
    char = next((c for c in FBSystem().Scene.Characters if c.Name == char_name), None)
    if not char: return
    
    t_path = os.path.join(get_template_dir(), tmpl_file)
    try:
        with open(t_path, 'r', encoding='utf-8') as f:
            data = json.load(f, object_pairs_hook=OrderedDict)
    except Exception as e:
        FBMessageBox("Error", "Failed to load template: " + str(e), "OK")
        return

    res = FBMessageBox("Confirm", "Update '{}' with current global rotations?".format(tmpl_disp), "Yes", "No")
    if res == 2: return

    updated_count = 0
    for prop_name, val in data.items():
        if not prop_name.endswith("Link"): continue
        if prop_name in ["DisplayName", "Description", "ReferenceLink"]: continue
        
        prop = char.PropertyList.Find(prop_name)
        if prop and len(prop) > 0:
            model = prop[0]
            if model:
                rot = FBVector3d()
                # Get Global Rotation (Essential for capturing the final World-Space pose)
                model.GetVector(rot, FBModelTransformationType.kModelRotation, False)
                
                bone_name = ""
                if isinstance(val, dict): bone_name = val.get("Name", "")
                else: bone_name = val
                
                data[prop_name] = OrderedDict([
                    ("Name", bone_name),
                    ("Rotation", [round(rot[0], 3), round(rot[1], 3), round(rot[2], 3)]),
                    ("RotationOrder", int(model.RotationOrder))
                ])
                updated_count += 1
    
    try:
        with open(t_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)
        g_ui["lbl_status"].Caption = "Success: Updated {} bones.".format(updated_count)
        FBMessageBox("Success", "Updated {} bones.\nTemplate: {}".format(updated_count, tmpl_file), "OK")
    except Exception as e:
        FBMessageBox("Error", "Failed to save template: " + str(e), "OK")

def PopulateTool(tool):
    print(">>> PoseExtractor: Populating tool UI...")
    tool.StartSizeX = 350
    tool.StartSizeY = 400
    
    x = FBAddRegionParam(10, FBAttachType.kFBAttachLeft, "")
    y = FBAddRegionParam(10, FBAttachType.kFBAttachTop, "")
    w = FBAddRegionParam(-10, FBAttachType.kFBAttachRight, "")
    h = FBAddRegionParam(-10, FBAttachType.kFBAttachBottom, "")
    tool.AddRegion("MainRegion", "MainRegion", x, y, w, h)
    
    vbox = FBVBoxLayout()
    tool.SetControl("MainRegion", vbox)
    
    lbl1 = FBLabel(); lbl1.Caption = "1. Select Character (must be characterized):"
    vbox.Add(lbl1, 20)
    g_ui["list_chars"] = FBList()
    vbox.Add(g_ui["list_chars"], 30)
    
    btn1 = FBButton(); btn1.Caption = "Refresh Character List"
    btn1.OnClick.Add(lambda c,e: RefreshCharacters())
    vbox.Add(btn1, 25)
    
    lbl2 = FBLabel(); lbl2.Caption = "2. Select Target Template to Update:"
    vbox.Add(lbl2, 20)
    g_ui["list_tmpls"] = FBList()
    vbox.Add(g_ui["list_tmpls"], 30)
    
    btn2 = FBButton(); btn2.Caption = "Refresh Template List"
    btn2.OnClick.Add(lambda c,e: RefreshTemplates())
    vbox.Add(btn2, 25)
    
    vbox.Add(FBLabel(), 15) # Spacer
    
    btn_ex = FBButton(); btn_ex.Caption = "EXTRACT POSE TO TEMPLATE"
    btn_ex.OnClick.Add(OnExtractClick)
    vbox.Add(btn_ex, 45)
    
    g_ui["lbl_status"] = FBLabel(); g_ui["lbl_status"].Caption = "Ready."
    g_ui["lbl_status"].Justify = FBTextJustify.kFBTextJustifyCenter
    vbox.Add(g_ui["lbl_status"], 30)

    RefreshCharacters()
    RefreshTemplates()

def CreatePoseExtractorTool():
    t_name = "Pose Extractor Tool v1.1"
    print(">>> PoseExtractor: Creating unique tool '{}'".format(t_name))
    tool = FBCreateUniqueTool(t_name)
    if tool:
        PopulateTool(tool)
        ShowTool(tool)
        print(">>> PoseExtractor: Tool shown successfully.")
    else:
        print(">>> PoseExtractor: Tool creation returned None. Checking existing tools...")
        for t in FBSystem().Scene.Tools:
            if t.Name == t_name:
                ShowTool(t)
                print(">>> PoseExtractor: Existing tool found and shown.")
                break

print(">>> PoseExtractor: Starting main execution...")
CreatePoseExtractorTool()
print(">>> PoseExtractor: Execution finished.")
