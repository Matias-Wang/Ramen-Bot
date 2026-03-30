# Ramen Bot (拉麵推薦機器人)

## 簡介（Tagline）
透過 LINE 對話，用 AI 幫你快速找出符合口味的拉麵並生成「食慾推薦文」。

---

##  專案說明
### 專案在解決什麼問題
使用者常常不知道該吃哪家拉麵，或是花很多時間在搜尋評論、比對店家。
本專案讓使用者只要在 LINE 輸入需求，就能直接得到符合條件的店家推薦，並附上 AI 生成的推薦描述。

### 使用架構/技術
- 透過 Gemini（Google AI）解析使用者輸入的自然語言（地區、口味、回覆形式）
- 以本地資料庫`ramen_data.json` 做快速店家篩選
- 利用Google Map 相關資訊，生成店家相關資料和敘述
- 再由 Gemini 依據店家內容生成一句吸引人的推薦文
- 透過 LINE Flex Message Carousel 呈現推薦結果

### 開發規範 (Technical Standards)
- Field Masking (欄位遮罩)：Places API 僅抓取 rating, userRatingCount, photos, currentOpeningHours 欄位以控管成本。
- Flex Handler 規範：嚴格執行索引配對，禁止生成空盒子 (contents: [])，確保型別安全。
- 逾時應對：針對 1 秒 Webhook 限制，考慮實作非同步處理或極大化快取機制。


### Google Maps API 相關服務類別
使用 Google Maps API 的Places API (New) 與 Geocoding API 服務
1. Geocoding API：將地址轉換為經緯度座標。用於提供地理位置相關的查詢。
2. Places API (New)：透過店名搜尋即時資訊 (含星等、照片)。
注意：使用 Google Maps API 可能會產生費用，請確保在開發和測試過程中合理使用 API，並考慮使用 API 的配額限制，並限制取得的資訊。

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

---

## 自動化 / AI / 資料處理能力
- AI 解析與生成：使用 Gemini（Google AI）
- 資料處理：本地 JSON 形式的店家資料庫

---

## 專案架構（Project Structure）
本專案採用 Agentic Router 架構，將功能拆分為核心中樞與獨立技能模組：

- 核心模組 [CORE]
    1. `agent_router.py`：意圖分發大腦，判定動作類型。
    2. `flex_handler.py`：UI 渲染引擎，負責標準化 Flex Message 輸出。

- 獨立專業技能 [SKILLS]
    1. Search Skill (`Search_skill.py`)：處理地區與口味的條件篩選。
    2. Info Skill (`info_skill.py`)：整合 Google Maps API 獲取即時評分與動態相片。
    3. Knowledge Skill (`knowledge_skill.py`)：拉麵百科問答系統（RAG）。

### 主要檔案
目錄結構 (Directory Structure)
- agent_router.py：[CORE] 意圖分發大腦。
- flex_handler.py：[CORE] UI 渲染引擎。
- /skills/：存放 Search_skill.py、info_skill.py、knowledge_skill.py。
- /services/：存放 Maps.py 處理外部 API 通訊。
- /data/：存放 ramen_data.json 本地資料庫與 API 快取。

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
LINE_CHANNEL_ACCESS_TOKEN = 你的LINE Channel Access Token
LINE_CHANNEL_SECRET = 你的LINE Channel Secret
GEMINI_API_KEY = 你的Gemini API Key
GEMINI_MODEL = gemini-3-flash-preview
GOOGLE_CLOUD_PROJECT_ID = GCP專案ID
GOOGLE_MAPS_API_KEY = GCP API Key
```

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
南港有什麼推薦的豚骨拉麵
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
研發進度 (R&D Progress)
1. [CORE]：完成非同步應對 (Async Handling) 與日誌追蹤。
2. [SKILL 1]：實作 IG 數據採集、AI 內容提取與 Geocoding 地址校驗。
3. [SKILL 2]：實作照片代理服務與智慧快取回寫系統。
4. [SKILL 3]：完成向量資料庫 (Vector DB) 整合與 RAG 檢索開發。

---

## 作者資訊
- 作者：MatiasWang
- Email：tzuanwork903@gmail.com
- Github：https://github.com/Matias-Wang
