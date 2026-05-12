# MotionBuilder to Warudo: RigidBody (Props) 同步流程

本文件記錄如何將 MotionBuilder 中的道具（Props）或追蹤器（Trackers）透過 VMC 協議同步至 Warudo。

---

## 1. MotionBuilder 端設定 (Mobu2VMC_MultiActor)

### 步驟：
1. **選取道具**：在 Mobu 場景中選取你想要同步的 `FBModel` (可以是 Null, Cube 或任何 Mesh)。
2. **設定端口 (Port)**：
   - 如果該道具是跟隨 Actor 的（例如手持道具），可以使用 Actor 的端口。
   - 如果是獨立道具（例如場景中的相機或移動平台），建議給予獨立的端口。
3. **加入清單**：在 `Mobu2VMC_MultiActor` 的 **Props** 頁籤點擊 `Add/Update`。
4. **資料特性**：
   - 程式會自動將這些道具的「世界座標」封裝成 VMC 的 **Root** 封包 (`/VMC/Ext/Root/Pos`)。
   - **優點**：Warudo 接收端不需要處理複雜的骨架層級，直接抓取 Root 即可獲得位移與旋轉。

---

## 2. Warudo 端設定 (Blueprint)

在 Warudo 中，你需要建立一個簡單的藍圖來接收並套用位移。

### 藍圖連線邏輯：
1. **接收資料**：建立一個 `VMC Receiver` 節點。
   - 將 **Port** 設定為與 Mobu 輸出的端口一致。
2. **獲取座標**：建立一個 `Get VMC Root Transform` 節點。
   - 將來源連向上述的 `VMC Receiver`。
3. **套用座標**：建立一個 `Set Asset Transform` 節點。
   - **Target Asset**：選擇你在 Warudo 場景中的道具模型。
   - **Transform**：連向 `Get VMC Root Transform` 的輸出。
4. **更新頻率**：確保上述連線是由 `On Update` 或是 `On VMC Data Received` 觸發。

---

## 3. 常見問答 (Q&A)

**Q: 為什麼要在 Mobu 把道具設成 Root？**  
A: 因為 VMC 協議主要是為人體骨架設計的。如果把道具設成某個骨頭名稱，Warudo 會試圖尋找骨架對應。設為 Root 是最乾淨的傳輸方式，Warudo 會直接把它當作一個具備 6DOF 的空間節點。

**Q: 我可以同時送多個道具嗎？**  
A: 可以。你可以讓多個道具共用同一個端口（在 Mobu 中加入清單），或分開端口。若共用端口，Warudo 的 VMC Receiver 會接收到所有資料，但通常建議分開端口以便在藍圖中區分不同的 Receiver。

---
**由 小聖腦絲 × Antigravity 協作記錄**  
[小聖腦絲的粉專](https://www.facebook.com/hysaint3d.mocap)
