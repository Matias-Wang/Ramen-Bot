# RAMEN_BOT_PROJECT_SPEC (v3.1)
## 核心願景 (Core Vision)
構建一個具備「意圖識別、即時數據、專業知識」三層架構的 AI Agent，將查詢、展示與百科功能工具化 (Skill-based)。

## 系統架構：意圖分發模型 (Agentic Router)
系統採用「中樞大腦 + 專業技能池」模式：

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

### 分類邏輯 (Intent Classification)
- SEARCH_BY_CRITERIA: 條件搜尋（地區、口味），觸發 SearchSkill。

- GET_SPECIFIC_INFO: 單店深入查詢，觸發 InfoSkill。

- KNOWLEDGE_QUERY: 拉麵百科問答，觸發 KnowledgeSkill。

##　技能開發規範 (Skill Specifications)
### Search Skill (已定案)
- 數據源: 本地 ramen_data.json。

- 邏輯: 執行關鍵字模糊比對與條件過濾。

### Info Skill (H2 開發重點)
- 數據源: Google Places API (New)。

- 工具化流程:

    1. Text Search: 換取唯一 place_id。

    2. Place Details: 抓取評分 (Rating)、評論數、相片編號。

    3. Media Proxy: 轉換相片編號為有效的 HTTPS URL。

- 快取策略: 24 小時至 7 天有效期，過期才觸發 API 請求並回寫 JSON。

## UI 渲染引擎規範 (Flex Handler Rules)
所有 Skill 返回的數據必須通過 flex_handler.py 進行標準化渲染：

[Rule 1] 索引配對: 嚴格執行 enumerate 索引配對，確保推薦文案與店家位置對齊。

[Rule 2] 容器完整性: 禁止生成空盒子 (contents: [])，按鈕區塊必須有資料才進行 append。

[Rule 3] 社交連結: 支援 1~3 個並排按鈕，解析 List of Dicts 結構並限制標籤長度 (4 碼)。

[Rule 4] 型別安全: 推薦文案 (AI_RECOMMENDATION) 強制為 String 格式。

## 專案目錄結構 (Recommended Directory)

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


## 其他注意事項 (Notes)
### 關於 Google Places API (New) 的成本與效能控管
在實作 services/google_maps.py 時，請務必提醒研發 Agent 注意以下兩點：
1. Field Masking (欄位遮罩)：Places API (New) 是根據請求的欄位計費的。在 Header 中務必只要求 places.rating, places.userRatingCount, places.photos, places.currentOpeningHours 等必要欄位，嚴禁使用 * 抓取全部資料，以節省開發經費。

2. Photo 請求優化：獲取 photo_url 時，建議設定 maxHeightPx 或 maxWidthPx（例如 800px），這能確保回傳的圖片大小適合 LINE 顯示，且能加快加載速度。

### 「特定查詢 (Info Skill)」的邏輯邊界
當使用者搜尋特定店名時，建議實作兩層搜尋邏輯：
- 第一層（精準匹配）：優先搜尋本地 ramen_data.json 是否有完全一致的名稱。

- 第二層（外部擴充）：若本地無資料，InfoSkill 應能「獨立生存」，直接透過 Google API 抓取該店資訊並回傳一筆臨時的 Flex Message。這會讓使用者覺得您的機器人「無所不知」，而不僅限於您的資料庫。

### 離線處理與數據一致性 (CLI 策略)
開發一個 「資料庫健康檢查腳本 (DB Health Checker)」：
- 任務：自動比對 ramen_data.json 裡的 address 與 Google Maps 回傳的地址是否落差太大（例如超過 50% 字元不符）。

- 目的：這能預防「店名+地區」搜尋到錯誤店家（例如同名的不同體系拉麵店）的情況。

### LINE Webhook 逾時應對
LINE 的伺服器要求 Webhook 必須在 1 秒內 回應。

- 挑戰：同時呼叫 Google API + Gemini 推薦文生成，極有可能超過 1 秒。

- 補充方向：建議 README 備註，如果 H2 延遲過高，應考慮實作非同步處理（回覆 200 OK 後，再使用 Push Message 回傳結果），或是極大化快取機制（Cache-First Strategy）來縮短回應時間。


---
## 開發 Agent 立即行動建議 (Immediate Next Action)
1. 鎖定目標：目前重心為 [SKILL 1] Search Skill。

2. 執行任務：在 services/google_maps.py 建立第一個工具函式，透過「店名+地區」獲取 Google 評分。

3. 更新狀態：完成後將 [SKILL 2] 的相關進度項目標記為 [x]。