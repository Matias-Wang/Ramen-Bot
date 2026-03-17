# Ramen Bot (拉麵推薦機器人)

## 簡介（Tagline）
透過 LINE 對話，用 AI 幫你快速找出符合口味的拉麵並生成「食慾推薦文」。


##  專案說明
### 專案在解決什麼問題
使用者常常不知道該吃哪家拉麵，或是花很多時間在搜尋評論、比對店家。
本專案讓使用者只要在 LINE 輸入需求，就能直接得到符合條件的店家推薦，並附上 AI 生成的推薦描述。

### 使用架構/技術
- 透過 Gemini（Google AI）解析使用者輸入的自然語言（地區、口味、回覆形式）
- 以本地資料庫(ramen_data.json) 做快速店家篩選
- 再由 Gemini 依據店家內容生成一句吸引人的推薦文
- 透過 LINE Flex Message Carousel 呈現推薦結果

### 目標使用者是誰
- 拉麵愛好者、在地上班族、觀光客
- 想用聊天方式快速取得拉麵推薦的人

---
## 功能特色（Features）

- ✅ LINE 對話式拉麵推薦（自然語言輸入即可查詢）
- ✅ Gemini 解析意圖（地區、口味、回覆形式）
- ✅ 本地資料庫快速篩選符合店家
- ✅ Flex Carousel 顯示店家資訊與推薦文
- ✅ AI 生成「單店推薦」增加吸引力
## 自動化 / AI / 資料處理能力
- AI 解析與生成：使用 Gemini（Google AI）
- 資料處理：本地 JSON 形式的店家資料庫

---

## 案架構（Project Structure）

### 主要檔案
- `app.py`：主程式，負責 LINE webhook、意圖解析、資料篩選與回覆
- `processor.py`：依照意圖篩選 `ramen_data.json`
- `flex_handler.py`：組成 LINE Flex Carousel 的 Bubble
- `prompts.py`：Gemini prompt 模板（意圖解析、推薦文生成）
- `ramen_data.json`：本地拉麵店資料庫

### 核心模組用途
- **意圖解析**：將使用者輸入轉成 `{ location, style, ui_tag }`
- **篩選邏輯**：從資料庫中找出符合意圖的店家
- **推薦生成**：為每家店生成 AI 推薦語
- **UI 組裝**：建立 LINE Flex Message Carousel 回覆給使用者

---

## 安裝方式（Installation）

### 安裝依賴

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

### 需要的環境變數

建立 `.env` 檔案，並填入：

```env
LINE_CHANNEL_ACCESS_TOKEN=你的LINE Channel Access Token
LINE_CHANNEL_SECRET=你的LINE Channel Secret
GEMINI_API_KEY=你的Gemini API Key
GEMINI_MODEL=gemini-3-flash-preview
```

> ⚠️ 若公司電腦無法安裝套件，請確認是否允許安裝 Python 套件，或改用 Docker/雲端部署。

---

## 使用方式（Usage）

### 執行

```bash
python app.py
```

### 需要準備的資料
- `ramen_data.json`：本地拉麵店資料
- LINE Channel Token + Secret
- Gemini API Key

### 執行後會得到什麼結果
- LINE 會收到一則 Flex Carousel 推薦訊息
- 每一個泡泡會顯示店名、地區、口味、地址與 AI 生成的推薦句

---

## 範例（Example）

**Input（LINE 訊息）**
```
我想吃南港豚骨
```

**Output（LINE Flex Carousel）**
- 極濃豚骨一番（南港 / 豚骨）
  - AI 推薦："熬煮 18 小時的京都豚骨湯頭，奶油滑順、叉燒大片、可免費加麵，排隊也值得。"
- 其他符合店家（最多 3 筆）

---

## 系統設計（System Design）

### 流程（Pipeline）
1. 使用者輸入文字 → 送去 Gemini 解析意圖
2. 取得意圖（location / style / ui_tag）→ 本地篩選符合店家
3. 生成店家描述 → 送至 Gemini 生成推薦文
4. 組成 Flex Carousel → 回傳 LINE

### 核心邏輯
- 意圖解析：只取必要欄位（避免過度猜測）
- 篩選：簡單匹配地區&口味，未來可擴大模糊比對
- 推薦文：透過 prompt 控制風格與長度

---

## 技術棧（Tech Stack）
- 語言：Python
- LLM：Gemini（`google.generativeai`）
- LINE SDK：`line-bot-sdk`
- 資料：JSON（`ramen_data.json`）

---

## 商業價值（Business Impact）
- 減少使用者找店時間
- 強化推薦轉換（更高到店率）
- 可延伸為商業合作或廣告推播

---

## 使用情境（Use Case）
- 拉麵愛好者想快速找到合適店家
- 上班族午餐時間快速找附近口味
- 觀光客想用聊天方式查詢

---

## 未來優化（TODO / Future Work）
- IG 數據同步與 RAG 檢索
- 圖像辨識（上傳拉麵照片自動推薦口味）
- 部署雲端並加上錯誤監控

---

## 作者資訊
- 作者：MatiasWang
- Email：tzuanwork903@gmail.com
- Github：https://github.com/MatiasWang
