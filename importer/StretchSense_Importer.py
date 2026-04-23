from pyfbsdk import *
import csv

def show_intro_message():
    """ 顯示介紹對話框 """
    FBMessageBox("StretchSenseFingerImporter", "1. 本工具由小聖腦絲與ChatGPT協作完成\n\n     https://www.facebook.com/hysaint3d.mocap\n\n2. 僅將StretchSense錄製的CSV手指數據應用到場景中對應骨骼。", "OK")

def open_csv_file_dialog():
    """ 使用檔案選擇對話框選擇CSV檔案 """
    file_popup = FBFilePopup()
    file_popup.Style = FBFilePopupStyle.kFBFilePopupOpen
    file_popup.Filter = "*.csv"
    file_popup.Caption = "選擇StretchSense手指動畫的CSV檔案"
    return file_popup.FullFilename if file_popup.Execute() else None

def try_open_csv(file_path):
    """ 嘗試使用多種編碼打開CSV文件 """
    encodings = ['utf-8-sig', 'utf-8', 'latin1']
    for encoding in encodings:
        try:
            with open(file_path, 'r', encoding=encoding) as file:
                csv_reader = csv.DictReader(file)
                frames = list(csv_reader)
                return frames
        except Exception:
            continue
    FBMessageBox("錯誤", "無法打開CSV文件，請確認其編碼格式。", "OK")
    return None

def parse_bone_names(frames):
    """ 解析CSV中的骨骼名稱，忽略hand_x, hand_y, hand_z """
    bone_names = set()
    for key in frames[0].keys():
        if ("_x" in key or "_y" in key or "_z" in key) and not key.startswith("hand_"):
            bone_name = '_'.join(key.split('_')[:2]).strip()
            bone_names.add(bone_name)
    return bone_names

def find_bones_in_scene(bone_names, hand_side):
    """ 在場景中查找骨骼並匹配名稱，根據手的選擇過濾骨骼（左手或右手） """
    models = FBSystem().Scene.Components
    matched_bones = {}
    missing_bones = {name for name in bone_names if not name.startswith('hand_')}

    for model in models:
        try:
            model_name = '_'.join(model.Name.strip()[:-2].split('_')[:2]) if model.Name.strip()[-2:] in ['_l', '_r'] else '_'.join(model.Name.strip().split('_')[:2])
            if model_name in bone_names and model.Name.strip().endswith(hand_side):
                FBTrace(f"匹配骨骼: {model_name}\n")
                matched_bones[model_name] = model
                missing_bones.discard(model_name)
        except Exception as e:
            FBTrace(f"警告: 無法處理模型名稱，錯誤: {e}\n")

    return matched_bones, missing_bones

def apply_rotation_data(csv_file_path, hand_side):
    progress = FBProgress()
    progress.Caption = "應用旋轉數據"
    progress.Text = "正在加載數據..."
    """ 將CSV中的骨骼旋轉數據應用到場景中的骨骼上 """
    frames = try_open_csv(csv_file_path)
    if frames is None:
        return

    # 解析骨骼名稱
    bone_names = parse_bone_names(frames)

    # 查找場景中的骨骼
    matched_bones, missing_bones = find_bones_in_scene(bone_names, hand_side)

    if missing_bones:
        FBMessageBox("警告", f"以下骨骼未在場景中找到:{', '.join(missing_bones)}", "OK")

    # 遍歷每一幀的數據並應用到骨骼
    progress.Percent = 0
    progress.Text = "開始處理骨骼數據..."
    for frame_index, frame_data in enumerate(frames):
        progress.Percent = int((frame_index / len(frames)) * 100)
        progress.Text = f"處理第 {frame_index + 1}/{len(frames)} 幀..."
        time = FBTime(0, 0, 0, frame_index)  # 每行數據對應一幀

        for key, value in frame_data.items():
            if "_x" in key or "_y" in key or "_z" in key:
                bone_name = '_'.join(key.split('_')[:2]).strip()
                axis = key.split('_')[-1]

                if bone_name in matched_bones:
                    FBTrace(f"處理骨骼: {bone_name}, 軸: {axis}, 值: {value}\n")
                    bone = matched_bones[bone_name]

                    if not isinstance(bone.Rotation, FBPropertyAnimatable):
                        FBTrace(f"骨骼 {bone.Name} 的旋轉屬性不可動畫。\n")
                        continue

                    if not bone.Rotation.IsAnimated():
                        bone.Rotation.SetAnimated(True)

                    anim_node = bone.Rotation.GetAnimationNode()

                    if anim_node:
                        try:
                            value = float(value)
                            if axis == 'x':
                                anim_node.Nodes[0].FCurve.KeyAdd(time, value)
                                FBTrace(f"x 軸應用數值: {value} 在時間: {time.GetSecondDouble()}\n")
                            elif axis == 'y':
                                anim_node.Nodes[1].FCurve.KeyAdd(time, value)
                                FBTrace(f"y 軸應用數值: {value} 在時間: {time.GetSecondDouble()}\n")
                            elif axis == 'z':
                                anim_node.Nodes[2].FCurve.KeyAdd(time, value)
                                FBTrace(f"z 軸應用數值: {value} 在時間: {time.GetSecondDouble()}\n")
                        except ValueError:
                            FBTrace(f"警告: 無效數值，骨骼: {bone_name}, 軸: {axis}, 值: {value}\n")

    FBSystem().Scene.Evaluate()
    progress.Text = "完成應用數據！"
    progress.Percent = 100
    progress.FBDelete()
    FBMessageBox("成功", "骨骼旋轉數據已成功應用到場景骨骼！", "OK")

# 主流程
show_intro_message()
csv_file_path = open_csv_file_dialog()
if csv_file_path:
    hand_choice = FBMessageBox("選擇手的類型", "請選擇要作用的手:", "左手", "右手")
    if hand_choice == 1:
        hand_side = "_l"
    elif hand_choice == 2:
        hand_side = "_r"
    else:
        FBMessageBox("取消", "您未選擇任何選項，操作已取消。", "OK")
        exit()

    apply_rotation_data(csv_file_path, hand_side)
else:
    FBMessageBox("錯誤", "未選擇CSV檔案", "OK")
