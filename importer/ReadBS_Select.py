"""
ReadBS_Select.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Import facial blendshape animation from a JSON file (Warudo / custom capture
format) and bake keyframes onto a selected model at 30 FPS.
Strips the "Facial_Bs." prefix from shape names before matching.

Workflow:
  1. Select target model in scene
  2. Run script → browse for .json file
  3. Keyframes are applied from JSON BlendShapeDatas array

由小聖腦絲與 Antigravity 協作完成
https://www.facebook.com/hysaint3d.mocap
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import json
from pyfbsdk import *
from pyfbsdk_additions import *

# 通用版可套用在指定具有相同BS的模型,限定30FPS
# 使用檔案選擇器選擇 JSON 檔案
def open_json_file_dialog():
    file_popup = FBFilePopup()
    file_popup.Style = FBFilePopupStyle.kFBFilePopupOpen
    file_popup.Filter = "*.json"  # 限制選擇 JSON 檔案
    file_popup.Caption = "選擇 JSON 檔案"
    
    if file_popup.Execute():  # 如果使用者選擇檔案
        return file_popup.FullFilename
    return None

# 加載 JSON 文件
def load_json_file(file_path):
    try:
        with open(file_path, 'r', encoding="utf-8") as file:
            return json.load(file)  # 返回 JSON 數據
    except Exception as e:
        FBMessageBox("錯誤", f"讀取 JSON 文件失敗: {e}", "OK")
        return None

# 檢查模型是否包含指定的 BlendShape 屬性
def get_blendshape_property(target_model, shape_name):
    for prop in target_model.PropertyList:
        if shape_name == prop.Name and isinstance(prop, FBPropertyAnimatable):
            return prop
    return None

# 將 BlendShape 數據應用到模型
def apply_blendshape_data_to_model(target_model, blendshape_data):
    if not target_model:
        FBMessageBox("錯誤", "目標模型不存在！", "OK")
        return

    # 遍歷每一幀的 BlendShape 數據
    for frame_data in blendshape_data:
        frame = frame_data["Frame"]
        shapes = frame_data["BlendShapes"]

        # 遍歷每個 BlendShape
        for shape_name, weight in shapes.items():
            # 去除前綴 "Facial_Bs."
            clean_shape_name = shape_name.replace("Facial_Bs.", "")
            
            # 找到對應的 BlendShape 屬性
            blendshape_property = get_blendshape_property(target_model, clean_shape_name)
            if blendshape_property:
                # 啟用動畫
                blendshape_property.SetAnimated(True)
                
                # 獲取屬性的 AnimationNode
                anim_node = blendshape_property.GetAnimationNode()
                if anim_node and anim_node.FCurve:
                    # 創建關鍵幀
                    anim_node.FCurve.KeyAdd(FBTime(0, 0, 0, frame), weight * 100)
                else:
                    FBTrace(f"警告：'{clean_shape_name}' 屬性沒有有效的 AnimationNode 或 FCurve。\n")
            else:
                FBTrace(f"警告：Blend Shape '{clean_shape_name}' 不存在於模型中！\n")

# 獲取當前選取的模型
def get_selected_model():
    selected_models = FBModelList()
    FBGetSelectedModels(selected_models)
    if len(selected_models) > 0:
        return selected_models[0]  # 假設只處理第一個選取的模型
    else:
        FBMessageBox("錯誤", "未選取任何模型！", "OK")
        return None

# 主程序
def main():
    # 呼叫檔案選擇對話框
    json_file_path = open_json_file_dialog()
    if not json_file_path:
        FBMessageBox("取消", "未選擇任何檔案。", "OK")
        return

    # 加載 JSON 檔案
    json_data = load_json_file(json_file_path)
    if not json_data:
        return

    # 獲取目前選取的模型
    target_model = get_selected_model()
    if not target_model:
        return

    # 列出屬性，方便調試
    print(f"選取的模型 '{target_model.Name}' 的屬性清單：")
    for prop in target_model.PropertyList:
        print(f"- 屬性名稱: {prop.Name}")

    # 提取 BlendShape 資料並應用到模型
    blendshape_data = json_data.get("BlendShapeDatas", [])
    apply_blendshape_data_to_model(target_model, blendshape_data)
    FBMessageBox("成功", "BlendShape 數據成功應用！", "OK")

# 執行主程序
main()
