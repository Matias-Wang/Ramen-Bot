# Ramen Bot (拉麵推薦機器人)

## 簡介（Tagline）
透過 LINE 對話，用 AI 幫你快速找出符合口味的拉麵並生成「食慾推薦文」。

---

## 專案說明

### 專案在解決什麼問題
使用者常常不知道該吃哪家拉麵，或是花很多時間在搜尋評論、比對店家。
本專案讓使用者只要在 LINE 輸入需求，就能直接得到符合條件的店家推薦，並附上 AI 生成的推薦描述。

### 使用架構/技術
- 透過 Gemini（Google AI）解析使用者輸入的自然語言（地區、口味、意圖類型）
- 以本地資料庫 `ramen_data.json` 做快速店家篩選（Geocoding + Haversine 距離過濾）
- 利用 Google Maps Places API (New) 取得即時評分、評論數與動態照片
- 再由 Gemini 依據店家內容生成一句吸引人的推薦文（asyncio 並行生成）
- 透過 LINE Flex Message Carousel 呈現推薦結果

### 開發規範 (Technical Standards)
- **Field Masking**：Places API (New) 僅抓取 `rating, userRatingCount, formattedAddress, photos` 欄位以控管成本。
- **Flex Handler 規範**：嚴格執行索引配對，禁止生成空盒子 (`contents: []`)，確保型別安全。
- **逾時應對**：針對 1 秒 Webhook 限制，目前透過 `data_pipeline.py` 預處理資料庫以減輕壓力；非阻塞異步應對機制（`asyncio.create_task`）尚在開發中。
- **每日配額保護**：`usage_tracker.py` 在每次呼叫前檢查 Google Maps、Gemini、LINE API 的每日上限。

### Google Maps API 相關服務
使用 Google Maps Platform 的 Places API (New) 與 Geocoding API：
1. **Geocoding API**：將地址/地名轉換為經緯度座標，用於 Search Skill 地理過濾。
2. **Places API (New)**：透過店名 Text Search 取得即時資訊（評分、照片）。
3. **Media Proxy**：將 `photo_name` 轉換為符合 LINE 規範的 HTTPS 圖片 URL。

> 注意：Google Maps API 可能產生費用，請合理使用並搭配每日配額上限保護。

### 目標使用者
- 拉麵愛好者、在地上班族、觀光客
- 想用聊天方式快速取得拉麵推薦的人

---

## 功能特色（Features）
- ✅ LINE 對話式拉麵推薦（自然語言輸入即可查詢）
- ✅ Gemini 解析意圖（地區、口味、意圖分類、店名）
- ✅ 地理距離過濾（Geocoding + Haversine 5km 半徑）
- ✅ 本地資料庫快速篩選符合店家
- ✅ Flex Carousel 顯示店家資訊（評分、地址、社群連結）
- ✅ AI 生成推薦文（asyncio 並行生成，最多 3 筆）
- ✅ 即時店家資料增強（Places API + 7 天 TTL 快取回寫）
- ✅ 每日 API / LLM 用量追蹤與配額保護

---

## 自動化 / AI / 資料處理能力
- **AI 解析與生成**：Gemini（Google AI）處理意圖識別與推薦文生成
- **資料 Pipeline**：`scripts/data_pipeline.py` 自動化從 IG 爬蟲 → LLM 提取 → Maps 驗證 → JSON 輸出
- **資料儲存**：本地 JSON 檔案作為主資料庫，支援 TTL 快取回寫

---

## 專案架構（Project Structure）
本專案採用 Agentic Router 架構，將功能拆分為核心中樞與獨立技能模組：

### 核心模組 [CORE]
1. `agent_router.py`：意圖分發大腦，解析意圖並分發至對應 Skill。
2. `flex_handler.py`：UI 渲染引擎，負責標準化 Flex Message 輸出。

### 獨立專業技能 [SKILLS]
1. **Search Skill** (`skills/Search_skill.py`)：地理位置 + 口味條件篩選，非同步推薦文生成。
2. **Info Skill** (`skills/info_skill.py`)：整合 Google Maps API，7 天快取策略取得即時評分與照片。
3. **Knowledge Skill**：拉麵百科問答系統（RAG，開發中）。

### 目錄結構 (Directory Structure)
```
Ramen-Bot/
├── app.py                  # 入口（LINE_TAG=1 正式 / LINE_TAG=0 本機測試）
├── agent_router.py         # [CORE] 意圖分發大腦
├── flex_handler.py         # [CORE] UI 渲染引擎
├── prompts.py              # LLM Prompt 存放處
├── usage_tracker.py        # 每日配額檢查與 token 追蹤
├── skills/
│   ├── Search_skill.py     # [SKILL 1] 條件搜尋
│   └── info_skill.py       # [SKILL 2] 特定店家資訊
├── services/
│   └── google_maps.py      # Google Maps API 統一封裝
├── scripts/
│   ├── data_pipeline.py    # 資料清洗 Pipeline
│   └── ig_scraper.py       # Instagram 爬蟲腳本
├── data/
│   ├── ramen_data.json     # 主資料庫
│   └── instagram_data.json # IG 原始資料
└── log/
    └── usage.json          # 每日 API 用量紀錄
```

### 核心模組用途
- **意圖解析**：Gemini 將使用者輸入轉成 `{intent, location, style, shop_name, ui_tag}`
- **篩選邏輯**：Geocoding 取座標 → Haversine 距離計算 → 5km 半徑過濾
- **推薦生成**：asyncio 並行呼叫 Gemini，每間店生成 30-60 字中文推薦文
- **UI 組裝**：建立 LINE Flex Message Carousel 回覆給使用者

---

## 安裝方式（Installation）

### 安裝依賴（使用 UV）
```bash
uv venv
uv pip install -r requirements.txt
```

### 安裝依賴（使用 pip）
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
GEMINI_MODEL = gemini-2.0-flash
GOOGLE_CLOUD_PROJECT_ID = GCP專案ID
GOOGLE_MAPS_API_KEY = GCP API Key
```

---

## 使用方式（Usage）

### 啟動 Bot（LINE 正式模式）
```bash
# 1. 將 app.py 頂部的 LINE_TAG 設為 1
# 2. 啟動 ngrok 並將 URL 設定至 LINE Developers Webhook URL
python app.py
```

### 本機測試模式
```bash
# LINE_TAG = 0（預設），直接在終端機互動
python app.py
# 輸入提問後，結果輸出至 temp.json
```

### 需要準備的資料
- `data/ramen_data.json`：本地拉麵店資料（可透過 data_pipeline.py 生成）
- LINE Channel Token + Secret
- Gemini API Key
- Google Maps API Key

### 執行後會得到什麼結果
- LINE 會收到一則 Flex Carousel 推薦訊息
- 每一個泡泡顯示店名、地區、口味、評分、地址與 AI 生成的推薦句

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
1. 使用者輸入文字 → LINE Webhook → `app.py`
2. `AgentRouter.dispatch()` 呼叫 Gemini 解析意圖（`intent, location, style, shop_name`）
3. 依 intent 分發至對應 Skill（Search / Info / Knowledge）
4. `generate_recommendations()` asyncio 並行生成推薦文
5. `assemble_carousel()` 組成 Flex Carousel → 回傳 LINE

### 核心邏輯
- **意圖解析**：只取必要欄位（intent, location, style, shop_name, ui_tag）
- **地理篩選**：Geocoding 取座標 → Haversine 5km；無座標則字串 fallback
- **推薦文**：30-60 字繁體中文，溫度 0.6，最多 1200 tokens
- **快取**：Info Skill 7 天 TTL，過期才呼叫 Places API

---

## 技術棧（Tech Stack）
| 類別 | 工具 |
|------|------|
| 語言 | Python 3.13.11 |
| 套件管理 | UV |
| 非同步 | asyncio |
| LLM | Gemini (`google-generativeai`) |
| LINE SDK | `line-bot-sdk` |
| Web 框架 | Flask |
| 地圖服務 | `googlemaps`（Geocoding）、`requests`（Places API New） |
| 資料 | JSON (`ramen_data.json`) |
| 環境變數 | `python-dotenv` |

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
1. **[CORE]**：完成非同步 Webhook 應對（防止 1 秒逾時）與全局 Fallback 機制。
2. **[SKILL 1]**：實作地址字元相似度一致性校驗腳本（50% 閾值標記）。
3. **[SKILL 2]**：開發 `update_api_data.py` 批次補全現有店家的 `place_id`。
4. **[SKILL 3]**：完成向量資料庫 (Vector DB) 整合與 RAG 檢索開發。

---

## 作者資訊
- 作者：MatiasWang
- Email：tzuanwork903@gmail.com
- Github：[Matias-Wang](https://github.com/Matias-Wang/Ramen-Bot)
- Instagram：https://www.instagram.com/tzuan903
