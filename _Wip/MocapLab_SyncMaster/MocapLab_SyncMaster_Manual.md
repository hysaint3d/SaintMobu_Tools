# MocapLab SyncMaster — 遠端同步錄製系統設定手冊

本工具旨在透過一個中央控制台，同時觸發多個動捕相關軟體的錄製開關，確保 Take Name 與時間軸的一致性。

## 系統需求
- **Python 3.x**
- 依賴套件（程式會自動安裝）: `flask`, `obsws-python`, `requests`, `websocket-client`

---

## 軟體對接設定指南

### 1. Optitrack Motive (2.x / 3.x)
- **協議**：Dual-Trigger (XML + NatNet Command)
- **預設端口**：1512 (XML) / 1510 (NatNet)
- **設定**：
  - 前往 `Advanced Streaming Settings`。
  - 勾選 **Remote Trigger**。
  - 確保 Command Port 設定正確（通常為 1510）。

### 2. OBS Studio (28.0+)
- **協議**：obs-websocket v5
- **預設端口**：4455
- **功能特色**：
  - **檔名同步**：錄影時會自動將 OBS 檔名格式設為當前的 `Take Name`。
  - **場景切換**：可透過 GUI 下方的 `Video Ctrl` 區塊手動刷新場景清單並切換。
- **設定**：
  - `工具` -> `WebSocket 伺服器設定` -> `啟用 WebSocket 伺服器`。
  - 建議設定密碼並填入 SyncMaster GUI。

### 3. Warudo
- **協議**：WebSocket (JSON Action)
- **預設端口**：19190
- **藍圖設定 (Blueprint)**：
  - **開始錄製**：新增 `On WebSocket Action` 節點，Action Name 填入 `RecordStart`。連向 `Invoke Asset Trigger` (Asset: Motion Recorder, Path: StartRecording)。
  - **停止錄製**：新增 `On WebSocket Action` 節點，Action Name 填入 `RecordStop`。連向 `Invoke Asset Trigger` (Asset: Motion Recorder, Path: StopRecording)。

### 4. MotionBuilder
- **協議**：OSC (UDP)
- **預設端口**：9000
- **說明**：搭配 `MocapLab_SyncRecorder.py` 腳本使用，可接收 `/TakeName` 與 `/RecordStart` 指令。

### 5. Unreal Engine 5
- **協議**：Web Remote Control (HTTP)
- **預設端口**：30010
- **設定**：
  - 啟用 `Web Remote Control` 插件。
  - 確保專案設定中允許遠端調用 Take Recorder。

---

## 介面燈號說明
- 🟢 **綠燈**：目標在線且通訊正常。
- 🔴 **紅燈**：目標離線、IP 錯誤或密碼錯誤。
- 🟡 **黃燈**：正在嘗試連線/偵測中。
- 🔘 **灰燈**：該項目未勾選啟用。

---
**由 小聖腦絲 × Antigravity 協作開發**  
[小聖腦絲的粉專](https://www.facebook.com/hysaint3d.mocap)
