# MobuOpenPose_Toolkit 使用手冊

本工具專為 MotionBuilder 設計，用於生成符合 **OpenPose BODY_25** 規格的 3D 骨架，適用於 AI 辨識、動態捕捉數據對齊或 Retargeting。

## 主要功能

*   **自動生成 OpenPose 骨架**：一鍵生成包含 25 個關鍵點（0-24）的完整階層。
*   **標準 T-Pose**：生成的骨架預設為標準 T-Pose，並可根據角色高度自動縮放。
*   **自動 HIK 角色化**：自動將 OpenPose 節點映射至 HumanIK 槽位，方便進行角色間的動態傳遞。
*   **視覺化標記**：使用顏色標註（左藍、右紅、中黃），方便在視圖中辨識。

## 安裝與執行

1.  確保 `MobuOpenPose_Toolkit.py` 與 `Templates/OpenPose_BODY_25.json` 已放置於 MotionBuilder 的腳本目錄中。
2.  在 MotionBuilder 的 **Python Editor** 中打開 `MobuOpenPose_Toolkit.py`。
3.  點擊 **Execute** 執行腳本。

## 操作流程

### 1. 設定參數
*   **Namespace**: 為生成的骨架設定命名空間（例如 `Actor1`），避免與場景中現有模型衝突。
*   **Height (cm)**: 設定角色的實際身高，工具會自動縮放骨架比例。

### 2. 生成骨架
點擊 **Generate Skeleton**。
*   這將在 `(0,0,0)` 位置生成一個名為 `OP_Root` 的根節點。
*   下方會包含從 `OP_00_Nose` 到 `OP_24_RHeel` 的所有節點。

### 3. 角色化 (HIK)
點擊 **Characterize (HIK)**。
*   系統會自動創建一個名為 `OpenPose_Character` 的 FBCharacter 物件。
*   自動完成所有關鍵點的 HIK 鏈接。
*   完成後，該骨架即可作為 HIK 的 Source 或 Target 使用。

## OpenPose BODY_25 關鍵點對照表

| 索引 | 名稱 | HIK 映射 |
| :--- | :--- | :--- |
| 0 | Nose | Neck |
| 1 | Neck | Spine |
| 2-4 | Right Arm | Right Shoulder/Arm/ForeArm |
| 5-7 | Left Arm | Left Shoulder/Arm/ForeArm |
| 8 | MidHip | Hips |
| 9-11 | Right Leg | Right UpLeg/Leg/Foot |
| 12-14 | Left Leg | Left UpLeg/Leg/Foot |
| 15-18 | Face | (僅作為節點生成) |
| 19-24 | Feet | ToeBase (BigToe) |

---
*由 SaintMocap 與 Antigravity 協作開發*
