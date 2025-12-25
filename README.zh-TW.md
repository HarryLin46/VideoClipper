# VideoClipper

VideoClipper 是一款輕量級工具，讓你在使用 PotPlayer 觀看影片時標記片段，並透過簡單的 GUI 介面檢視與匯出所有片段。  
它能在不中斷播放、也不需要手動記錄時間戳的情況下，讓你順暢完成「觀看 → 標記 → 匯出精彩片段」的工作流程。

---

## Overview

VideoClipper 由兩個部分組成：

### 1. background_marker.exe

當 PotPlayer 為前景視窗時，按下滑鼠中鍵會記錄目前的播放時間點。  
這些時間戳會被儲存成一個 `.marks` 檔案，位置如下：

```
VideoClipper/VideoMarks/
```

`.marks` 檔案的內容格式如下：

```
03:13, start
04:22, end
32:13, start
35:49, end
01:02:17, start
01:06:06, end
...
```

每一組 `start` 與 `end` 都代表一個剪輯片段。

### 2. gui_app.exe

完成觀看後，你可以開啟 GUI 介面，載入影片檔與其對應的 `.marks` 檔案。  
GUI 會顯示所有偵測到的片段，並提供滑桿與按鈕進行細部調整，最後可一次匯出所有片段。

如果 **找不到** 對應的 `.marks` 檔案，GUI 會切換為手動模式，允許以手動方式建立與調整剪輯片段。

---

## 安裝

VideoClipper 提供已編譯完成的 Windows 可執行檔。  
使用者不需要安裝 Python 或任何開發相關的套件。

只需下載最新的 Release 套件，並參照下方說明即可。

### 專案資料夾結構

下載最新 **Release** 後，請將檔案整理成以下結構：

```
VideoClipper/
└─ dist/
   ├─ background_marker.exe
   └─ gui_app.exe
```

---

## 使用流程

VideoClipper 的使用流程從標記影片到匯出剪輯片段相當直觀。

### Step 0. 安裝並設定 PotPlayer

- 若尚未安裝 PotPlayer，請先完成安裝。

- 在 PotPlayer 中設定滑鼠中鍵：
  1. 開啟 PotPlayer，按下 `F5`或滑鼠右鍵 進入 **偏好設定**。
  2. 前往 **一般 → 鍵盤**。
  3. 點擊 **新增** 以建立新的熱鍵。
  4. 點擊快捷鍵輸入欄位，然後按下 **滑鼠中鍵** 進行熱鍵指派。
  5. 在選單中選擇：**其他 → 複製目前播放時間到剪貼簿**。

---

### Step 1. 啟動 background marker（選擇性）

執行：

```
VideoClipper/dist/background_marker.exe
```

並讓它在背景中持續執行。

---

### Step 2. 在 PotPlayer 中觀看並標記（選擇性）

- 使用 PotPlayer 播放影片。

- 當你想標記某個片段時：

  - 在片段開始前按下滑鼠中鍵 → 記錄一個 `start`
  - 在片段結束後按下滑鼠中鍵 → 記錄一個 `end`

系統會自動在以下位置建立 `.marks` 檔案：

```
VideoClipper/VideoMarks/
```

範例：

```
VideoClipper/VideoMarks/MyVideo.marks
```

內容如下：

```
03:13, start
04:22, end
32:13, start
35:49, end
...
```

---

### Step 3. 在 GUI 中載入並匯出剪輯片段

在開啟 GUI 前，請確認影片檔案與其 `.marks` 檔案（若有）位於同一資料夾內。

執行：

```
VideoClipper/dist/gui_app.exe
```

在 GUI 中：

![VideoClipper GUI](./README_img/gui_v2.png)

- 點擊 **「選擇影片檔案...」** 並選擇目標影片。
- 處理完成後，所有片段會在左側清單中顯示為：
  - `Clip #1`, `Clip #2`, …

- 你可以使用以下方式微調每個片段：

  - **「目前正在調整: 開始點 / 結束點」** 以選擇要調整的時間點
  - 綠色滑桿：開始位置  
  - 紅色滑桿：結束位置  
  - ±1s 與 ±0.1s 按鈕：精準調整

- 你也可以使用以下播放控制來檢視每個片段：

  #### 播放按鈕行為

  - 若為 **「目前正在調整: 開始點」**，按下 **「播放/暫停」** 會從 **片段開始位置** 播放。
  - 若為 **「目前正在調整: 結束點」**，按下 **「播放/暫停」** 會從 **片段結束前約 3 秒** 開始播放。

  #### 接續播放

  - 按下 **「接續播放/暫停」** 會從 **目前播放位置** 接續播放影片內容。

  #### 藍色滑桿

  - 開始播放後，你可以使用藍色滑桿調整 **目前播放位置**。

若不需要調整，你也可以直接匯出片段。

---

## 手動模式

若 **找不到** 對應的 `.marks` 檔案，GUI 會自動進入手動模式。

在手動模式中，你可以：

- 自由調整開始與結束邊界
- 以與標記模式相同的方式匯出剪輯片段

不論是否存在 `.marks` 檔案，你都可以隨時 **手動新增剪輯片段**。

### 新增剪輯片段

若要手動新增剪輯片段：

- 點擊 GUI 右下角的 **開始輸出所有clips** 按鈕
- GUI 會提示你確認新增剪輯片段
- 新增後的剪輯片段可與其他片段相同的方式進行調整

---

## 輸出位置

所有匯出的剪輯片段會輸出到原始影片檔案所在的資料夾，例如：

```
~/MyVideo.mp4
~/clip_001.mp4
~/clip_002.mp4
...
```

---

## For 開發者

若要手動建置.exe檔：

### 準備 ffmpeg

從 ffmpeg 官方網站下載：

```
https://ffmpeg.org/download.html
```

下載 **ffmpeg-git-full.7z** 並解壓縮。

解壓縮後，請將 `ffmpeg.exe` 複製到 repository 中以下路徑：

```
bin/ffmpeg.exe
```

---

### 建置.exe檔

1. 建置 background marker：

```
pyinstaller --onefile .\run_background_marker.py
```

2. 建置 GUI：

```
pyinstaller --onefile --clean --add-binary "bin\ffmpeg.exe;bin" .\run_gui_app.py
```

建置完成後會得到：

```
run_background_marker.exe
run_gui_app.exe
```

---

## 注意事項

- background marker 只會在 PotPlayer 為前景視窗時記錄時間戳至.mark檔。
- 標記時間點沒有Undo機制；每次按下滑鼠中鍵都會被永久記錄。
- 不支援巢狀片段（一個片段包含另一個片段）。
- 有時UI會當掉沒有回應，目前尚未查明原因，遇到此情況建議關掉重開
