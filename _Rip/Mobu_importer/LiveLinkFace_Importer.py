"""
LiveLinkFace_Importer.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Import ARKit blendshape animation from an Unreal Engine LiveLink Face CSV
export and bake it as keyframes onto the selected model's blendshape properties.

Workflow:
  1. Select target model in scene
  2. Run script → browse for .csv file
  3. Keyframes are baked at 30 FPS onto matching ARKit blendshape channels

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from pyfbsdk import *
import csv

def show_intro_message():
    """ 顯示介紹對話框 """
    FBMessageBox("UE LivelinkFace Importer", "1. 本工具由小聖腦絲與ChatGPT協作完成\n\n     https://www.facebook.com/hysaint3d.mocap\n\n2. 目標模型的BS名稱需正確遵循ARKit規範", "OK")

def open_csv_file_dialog():
    """ 使用MotionBuilder內建檔案選擇器讓使用者選擇CSV檔案 """
    file_popup = FBFilePopup()
    file_popup.Style = FBFilePopupStyle.kFBFilePopupOpen
    file_popup.Filter = "*.csv"
    file_popup.Caption = "選擇UE LivelinkFace的CSV檔案"
    return file_popup.FullFilename if file_popup.Execute() else None

def get_selected_model():
    """ 取得目前選取的模型 """
    model = FBModelList()
    FBGetSelectedModels(model)
    return model[0] if model else None

def import_blendshape_data(model, csv_file_path):
    """ 將CSV中的blendshape數據導入到模型中 """
    try:
        with open(csv_file_path, 'r') as file:
            frames = list(csv.DictReader(file))
    except Exception as e:
        FBMessageBox("錯誤", f"無法讀取CSV檔案: {e}", "OK")
        return

    for frame_index, frame_data in enumerate(frames):
        time = FBTime(0, 0, 0, frame_index)

        for blendshape, value in frame_data.items():
            if blendshape in ["Timecode", "BlendshapeCount"]:
                continue

            channel = model.PropertyList.Find(blendshape)
            if channel and isinstance(channel, FBPropertyAnimatable):
                channel.SetAnimated(True)
                channel.GetAnimationNode().KeyAdd(time, float(value) * 100)
            else:
                FBTrace(f"警告: 無法找到blendshape '{blendshape}'\n")
    
    FBSystem().Scene.Evaluate()
    FBMessageBox("成功", "ARKit表情動畫導入完成！", "OK")

# 主流程
show_intro_message()
model = get_selected_model()
if not model:
    FBMessageBox("錯誤", "請選擇一個模型！", "OK")
else:
    csv_file_path = open_csv_file_dialog()
    if csv_file_path:
        import_blendshape_data(model, csv_file_path)
    else:
        FBMessageBox("錯誤", "未選擇CSV檔案", "OK")
