# MobuVCam NDI Toolkit 使用說明

本工具結合了虛擬攝影機控制、多種追蹤源導入以及 **NDI 6 即時影像輸出**。

---

## 1. 核心功能分頁 (Tabs)

### [VCam] 攝影機主控
- **Attach**：將攝影機連結至指定的目標物（Rigid Body）。
- **Offset**：調整位置與旋轉偏移量。
- **Zoom/FOV**：即時控制焦距。
- **Record/Snapshot**：控制 Mobu 錄製與抓取畫面。

### [OSC Source] 手機追蹤 (ARKit)
- 透過 OSC 協議接收手機（如 ZIG SIM）的位移與旋轉資料。
- 預設端口：可自定義（通常建議 5000-9000）。

### [OpenVR] SteamVR 追蹤
- 直接讀取 Vive Tracker 或手把的 6DOF 資料。
- 適合精確度要求較高的棚內拍攝。

### [NDI Out] 影像輸出
- **功能**：將目前選擇的攝影機視口（Viewport）以 NDI 格式推送至區域網路。
- **應用**：在 OBS、vMix 或 NDI Monitor 中直接接收 Mobu 的即時畫面。
- **需求**：系統必須安裝 **NDI 6 Tools** 或 **NDI Runtime**。

---

## 2. Gamepad (Xbox 手把) 操作說明

本工具支援使用 Xbox 藍牙手把進行無線操作，讓你像拿著實體攝影機一樣拍攝。

### 攝影機控制 (Camera)
- **LT / RT / 左類比(LS) Y軸**：變焦 (Zoom In/Out)。
- **右類比 (RS)**：平移與傾斜 (Pan / Tilt) 微調。
- **Start 鍵**：重設 FOV 與偏移量。

### 錄製與擷取 (Capture)
- **A 鍵**：切換錄製狀態 (Record Toggle)。
- **B 鍵**：擷取目前的 Viewport 快照。

### Take 管理 (Takes)
- **X / Y 鍵**：切換上一個 / 下一個 Take。
- **LB / RB 鍵**：快速前往時間軸的 起點 / 終點。

### 時間軸控制 (Timeline)
- **方向鍵 (D-Pad) 上/下**：正向播放 / 逆向播放。
- **方向鍵 (D-Pad) 左/右**：逐格前進 / 逐格後退。

---

## 3. 設定與疑難排解

- **NDI 無法輸出？**
  - 請確認 NDI Runtime 已安裝。
  - 檢查防火牆是否允許 MotionBuilder 的網路連線。
- **手把沒反應？**
  - 本工具使用 XInput 協議，請確保手把已連線至 Windows 且被識別為控制器 1。
- **追蹤抖動？**
  - 在 [VCam] 頁面可以調整 Low-pass Filter (平滑濾波) 數值。

---
**由 小聖腦絲 × Antigravity 協作記錄**  
[小聖腦絲的粉專](https://www.facebook.com/hysaint3d.mocap)
