# MobuCharacter Toolkit 使用說明

本工具是 MotionBuilder 中的角色管理與骨架標準化利器，旨在簡化角色化（Characterize）流程並統一骨架命名規範。

---

## 1. 核心功能模組

### 🦴 生成標準骨架 (Generate Skeleton)
- **功能**：根據預設的比例（170cm 基比）生成一組完美的 T-Pose 骨架。
- **命名規範**：支援 **VMC**、**HIK** (MotionBuilder 內建) 以及 **UE** (Unreal Engine) 三種命名方式。
- **應用場景**：當你需要一個乾淨的控制骨架，或是要為新角色建立 Target 骨架時。

### 🤖 自動角色化 (Auto Characterize)
- **Smart Detect (智慧偵測)**：自動掃描場景中的骨架，利用模糊匹配算法將骨頭填入 HIK 槽位。
- **Templates (模板)**：支援從 `Templates` 資料夾讀取 JSON 檔案，精確對應特定格式的角色（如 Mixamo, VRoid）。
- **一鍵完成**：填入名稱後直接點擊 `Characterize`，系統會自動建立 Character 資源並鎖定。

### 🛠 骨架工具 (Skeleton Tools)
- **Rename to Standard**：根據 HIK 定義，將選取的骨頭重新命名為標準格式。
- **Fuzzy / Aggressive Matching**：針對命名極度混亂的骨架，開啟進階匹配模式。

---

## 2. 操作流程建議

### 流程 A：處理全新導入的模型
1. 選擇模型所有骨頭。
2. 點擊 **Smart Detect**，檢查槽位是否正確。
3. 點擊 **Characterize** 完成。

### 流程 B：建立全新的同步骨架 (用於 VMC)
1. 在 `Mode` 選擇 `VMC`。
2. 點擊 **Generate Skeleton**。
3. 使用該骨架作為你的資料傳輸目標。

---

## 3. 模板系統 (Templates)

工具會自動讀取腳本路徑下 `Templates/*.json`。
- 你可以自行增加 JSON 檔案，格式為：`{"HIK_Slot_Name": "Bone_Name_In_Scene"}`。
- 這樣對於特定工作室的內部規範模型，可以達到 100% 的自動對接。

---

## 4. 常見問答 (Q&A)

**Q: 為什麼智慧偵測抓不到我的手部骨頭？**  
A: 請確保你的手部骨架包含明顯的關鍵字（如 `Hand`, `Wrist`, `Palm`）。如果命名太過簡略，建議先使用 **Tools** 進行手動映射或修改 JSON 模板。

**Q: 產生的骨架比例不對怎麼辦？**  
A: 生成後，你可以直接縮放 Root 節點。工具內建的 `BASE_H` (170cm) 是為了確保 retargeting 時的預設比例最接近標準。

---
**由 小聖腦絲 × Antigravity 協作記錄**  
[小聖腦絲的粉專](https://www.facebook.com/hysaint3d.mocap)
