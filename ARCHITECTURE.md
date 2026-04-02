# RAMEN_BOT_PROJECT_SPEC (v3.1)

## 核心設計哲學 (Core Philosophy)
本專案不僅是一個聊天機器人，而是一個具備「意圖識別、即時數據、專業知識」三層架構的 AI Agent。系統核心採用 Agentic Router (意圖分發模型)，將需求工具化 (Skill-based)，確保功能擴充時的解耦與穩定性。

---

## 系統架構：意圖分發模型 (Agentic Router)
系統採用「中樞大腦 + Skill」模式，將使用者輸入經由核心分發至對應技能模組：

### 意圖分類邏輯 (Intent Classification)
|意圖標籤 (Intent)|觸發條件|對應技能模組|
|------------------|---------|-------------|
|SEARCH_BY_CRITERIA|使用者提供地區、口味等搜尋條件|Search Skill|
|GET_SPECIFIC_INFO|針對特定店名的深入資訊查詢|Info Skill|
|KNOWLEDGE_QUERY|拉麵流派、點餐禮儀等百科問答|Knowledge Skill|

### 系統架構
```Bash
[User Input] 
      |
[CORE] Router Agent (意圖分發)
      |
      +-- [SKILL 1] Search (條件搜尋)
      |
      +-- [SKILL 2] Info (特定查詢)
      |
      +-- [SKILL 3] Knowledge (知識百科)
      |
[CORE] Flex Handler (UI 渲染輸出)
```

---

##　技能開發規範 (Skill Specifications)
### Search Skill (條件搜尋)
- 數據源: 
    1. 本地 `ramen_data.json`。
    2. Gecoding API。

- 核心邏輯：判
    * 斷語意的篩選條件進行比對
        1. 篩選條件為口味、風格：本地資料庫直接模糊比對。
        2. 篩選條件為地區、地理位置相關：先進行gecoding轉換，再經緯度做比對。。
    *透過 asyncio 並行呼叫 LLM 產生推薦語。

### Info Skill (即時數據增強)
- 數據源：Google Places API (New)。

- 工具化流程:
    1. Text Search：獲取唯一 place_id。
    2. Place Details：抓取評分 (Rating)、評論數及營業狀態。
    3. Media Proxy：將 Google 照片編號轉換為符合 LINE 規範的 HTTPS URL。

### Knowledge Skill (RAG 知識庫)
- 數據源：專業拉麵知識文本。
- 技術棧：整合向量資料庫（如 ChromaDB）進行語義檢索 (Vector Search)。

---

## 工程挑戰與優化 (Engineering Challenges)

### LINE Webhook 逾時應對 (1s Limit)
- LINE 伺服器要求 Webhook 必須在 1 秒內回應。為了解決 AI 與地圖 API 呼叫的延遲問題，本系統實施：
- 快取優先策略 (Cache-First)：對 API 數據實施 24 小時至 7 天的快取機制，減少重複請求。
- 預處理機制：在背景完成 IG 數據採集與地址座標化 (Geocoding)，確保查詢時不需即時等待數據處理。

### 成本控管與效能限制 (Cost Guardrail)
- Field Masking：在呼叫 Google Places API 時，僅請求必要欄位（如 places.rating），有效降低 API 消耗支出。
- 圖片優化：設定 maxHeightPx 為 800px，確保圖片大小適合行動裝置載入且符合 LINE Flex Message 比例。

### 數據一致性校驗 (Data Integrity)
- 自動化健康檢查：建立 CLI 工具比對本地地址與 Google 地圖回傳地址的精確度，防止同名店家的誤判。
- 型別安全渲染：flex_handler.py 嚴格執行索引配對，確保推薦文案與店家位置精確對齊，禁止生成空盒子 UI。

---

## 目錄結構 (Directory Structure)

```Plaintext
/my_ramen_bot
├── app.py                 # 入口管理
├── agent_router.py        # [CORE] 意圖分發大腦
├── flex_handler.py        # [CORE] UI 渲染引擎
├── /skills
│   ├── Search_skill.py # [SKILL 1]
│   ├── info_skill.py      # [SKILL 2]
│   └── knowledge_skill.py # [SKILL 3]
├── /services
│   └── google_maps.py     # 外部 API 通訊
├── /data
│   └── ramen_data.json    # 本地資料庫與 API 快取
├── /script
│   └── ig_scraper.py      # 個人公開IG資料撈取腳本
├── processor.py           # 資料處理邏輯
├── prompt.py              # LLM promt存放處
└── .env                   # 密鑰管理
```


## 研發進度追蹤 (R&D Progress Checklist)
1. 研發進度追蹤 (Progress Checklist)
🟢 [CORE] 共用核心功能 (Shared Modules)
[v] 環境基礎建設：.env 配置、Gemini API Key 串接。
[v] 基礎意圖解析：解析 location、style 並決定 ui_tag。
[v] Flex 渲染引擎：flex_handler.py 字典操作邏輯開發完成。
[v] 動態 UI 邏輯：1~3 個並排社群按鈕自動伸縮與防錯（避免空盒子）。
[v] 推薦文配對：實作 enumerate 索引確保 AI 評論與店家位置精確對齊。
[ ] 分發器升級：擴展 agent_router.py 以支援三種 Skill 的自動切換。
[ ] Google API 封裝：建立 services/google_maps_api.py 統一處理 Text Search 與 Geocoding。
[ ] 全局錯誤處理：實作各 Skill 失敗時的降級回退（Fallback）機制。
[ ] 分發器升級：擴展 agent_router.py 以支援三種 Skill 的自動切換。
[ ] 非同步應對 (Async Handling)：實作防止 LINE Webhook 逾時的預處理機制。
[ ] 日誌與追蹤 (Logging & Trace)：紀錄 AI 思考過程與 API 呼叫日誌，便於 CLI 除錯。
[ ] 全局錯誤處理：實作各 Skill 失敗時的降級回退（Fallback）機制。

🍜 [SKILL 1] Search：條件找店家 (Criteria Search)
[v] 資料結構定義：本地 ramen_data.json 欄位規範定案。
[v] 本地篩選邏輯：地區（模糊比對）與口味關鍵字過濾實作完成。
[v] 推薦文並行生成：asyncio 並行呼叫 Gemini 產生每間店的專屬短評。
[v] 多筆輪播組裝：將篩選結果轉化為 carousel 格式輸出。
[ ] IG 數據採集 (Data Scraping)：開發腳本從指定公開 IG 帳號撈取貼文圖片與內文。
[ ] AI 內容提取 (LLM Extraction)：利用 LLM 從 IG 內文自動提取「店名、地址、口味標籤」並整理至 JSON。
[ ] 地址校驗與編碼 (Geocoding)：呼叫 Maps API 將 IG 地址轉為座標，並獲取 place_id。
[ ] 數據一致性校驗：開發腳本比對 IG 資訊與 Google 回傳資訊的準確性。

📍 [SKILL 2] Info：特定店家資訊 (Specific Info)
[ ] Google API 封裝：建立 Maps_api.py 處理 Text Search。
[ ] 成本控管機制 (Cost Guardrail)：設定每日 API 呼叫上限，防止異常扣款。
[ ] 數據欄位對齊：實作 Field Masking 抓取評分、總評論數與營業狀態。
[ ] 照片代理服務：將 photo_reference 轉換為有效 URL 並傳遞給 UI 層。
[ ] 智慧快取系統：實作「檢查本地 -> API 抓取 -> 回寫 JSON」的資料持久化邏輯。
[ ] CLI 預處理工具：開發批次腳本補全現有資料庫所有店家的 place_id。

📚 [SKILL 3] Knowledge：拉麵知識庫 (RAG System)
[ ] 知識資料集收集：整理拉麵流派、麵條硬度、點餐禮儀等文字資料。
[ ] 向量資料庫整合：選擇並整合向量資料庫（如 ChromaDB 或 Pinecone）。
[ ] 檢索邏輯開發：實作相似度搜尋（Vector Search）以取得最相關的知識片段。
[ ] 問答生成優化：撰寫專業拉麵大師語氣的 Prompt 進行回答合成。