# RAMEN_BOT_PROJECT_SPEC (v3.2)

## 核心設計哲學 (Core Philosophy)
本專案不僅是一個聊天機器人，而是一個具備「意圖識別、即時數據、專業知識」三層架構的 AI Agent。系統核心採用 Agentic Router (意圖分發模型)，將需求工具化 (Skill-based)，確保功能擴充時的解耦與穩定性。

---

## 系統架構：意圖分發模型 (Agentic Router)
系統採用「主要Agent + Skill」模式，將使用者輸入經由核心分發至對應技能模組：

### 意圖分類邏輯 (Intent Classification)
|意圖標籤 (Intent)|觸發條件|對應技能模組|
|------------------|---------|-------------|
|SEARCH_BY_CRITERIA|使用者提供地區、口味等搜尋條件|Search Skill|
|GET_SPECIFIC_INFO|針對特定店名的深入資訊查詢|Info Skill|
|KNOWLEDGE_QUERY|拉麵流派、點餐禮儀等百科問答|Knowledge Skill|

### 系統架構
```
[User Input via LINE]
      |
[STEP 1] AgentRouter.dispatch() — Gemini 解析意圖
      | intent_data: {intent, location, style, shop_name, ui_tag}
      |
[STEP 2] Skill 執行
      +-- SEARCH_BY_CRITERIA → filter_ramen_data() → [shop list]
      |       - Geocoding 取座標 → Haversine 半徑 5km 過濾
      |       - 無座標則字串模糊比對 fallback
      |
      +-- GET_SPECIFIC_INFO  → InfoSkill.get_shop_info() → [shop]
      |       - 本地快取優先（7天 TTL）
      |       - 過期則呼叫 Places API (New)
      |
      +-- KNOWLEDGE_QUERY    → 回傳「百科功能開發中」訊息
      |       (RAG 系統尚在開發)
      |
[STEP 3] generate_recommendations() — asyncio 並行 Gemini 生成推薦文
      |
[STEP 4] assemble_carousel() — flex_handler 組裝 LINE Flex Carousel
      |
[LINE Flex Message Response]
```

---

## 技能開發規範 (Skill Specifications)

### Search Skill (條件搜尋) — `skills/Search_skill.py`
- **數據源**：本地 `data/ramen_data.json` + Geocoding API
- **核心邏輯**：
    1. 口味比對：`target_style in shop["style"]` 模糊比對
    2. 地區比對（優先序）：
       - 有座標 → Geocoding 取目標座標 → Haversine 計算距離 → 5km 以內
       - 無座標 → 字串去除「市/區/縣」後模糊比對 fallback
    3. 結果依 `distance_km` 排序（若有座標）
- **推薦文生成**：`asyncio.gather` 並行呼叫 Gemini，最多 3 筆

### Info Skill (即時數據增強) — `skills/info_skill.py`
- **數據源**：本地快取 + Google Places API (New)
- **快取邏輯**：
    1. 檢查本地是否有 `place_id` 且 `last_updated` 在 7 天以內
    2. 若需更新 → Text Search 取得 `place_id`、評分、評論數、照片
    3. 更新後回寫 `ramen_data.json`
- **Field Masking**：`id, displayName, rating, userRatingCount, formattedAddress, photos`

### Knowledge Skill (RAG 知識庫) — 🚧 開發中
- **數據源**：專業拉麵知識文本
- **技術棧**：整合向量資料庫（如 ChromaDB）進行語義檢索 (Vector Search)
- **現況**：router 已預留 KNOWLEDGE_QUERY 分支，回覆佔位訊息

---

## 工程挑戰與優化 (Engineering Challenges)

### LINE Webhook 逾時應對 (1s Limit)
- LINE 伺服器要求 Webhook 必須在 1 秒內回應。為了解決 AI 與地圖 API 呼叫的延遲問題，本系統實施：
- **快取優先策略 (Cache-First)**：對 API 數據實施 7 天的快取機制，減少重複請求。
- **預處理機制**：透過 `scripts/data_pipeline.py` 離線完成 IG 數據採集與座標化，確保查詢時不需即時等待。

### 成本控管與效能限制 (Cost Guardrail)
- **Field Masking**：呼叫 Google Places API 時，僅請求必要欄位，有效降低 API 消耗支出。
- **圖片優化**：設定 `maxHeightPx` 為 800px，符合 LINE Flex Message 比例規範。
- **每日用量追蹤器 (Daily Usage Tracker)**：`usage_tracker.py` 在每次呼叫外部 API 或 LLM 前執行配額檢查。

  **追蹤範圍與上限：**
  | 追蹤鍵值 | 涵蓋呼叫 | 每日上限 |
  |---|---|---|
  | `google_maps_api` | Geocoding + Places Text Search + Places Photo（三者加總）| 100 次 |
  | `llm_gemini` | 意圖解析 + 推薦文生成（所有 Gemini 呼叫加總）| 100 次 |
  | `line_api` | reply_message 發送次數 | 100 次 |

  **運作邏輯：**
  1. 讀取 `log/usage.json`，比對 `date` 欄位。
  2. 若日期不是當天 → 所有計數歸零、更新日期。
  3. 若計數已達上限 → 印出錯誤訊息並阻擋呼叫，LINE 回覆仍發送。
  4. 正常情況 → 計數 +1 並寫回 JSON；LLM 呼叫額外記錄 `token_consumed`。
  5. 追蹤器本身若發生例外 → 放行正常流程，不因追蹤錯誤影響服務。

  **相關檔案：**
  - `log/usage.json`：每日用量資料，可直接開啟查看。
  - `usage_tracker.py`：提供 `check_and_increment(key)` 與 `record_tokens(tokens)` 兩支函式。

### 數據一致性校驗 (Data Integrity)
- **Pipeline 驗證**：`data_pipeline.py` Stage 3 呼叫 Places API (New) 驗證店家是否仍在營業，過濾 `CLOSED_PERMANENTLY` 店家。
- **型別安全渲染**：`flex_handler.py` 嚴格執行索引配對，確保推薦文案與店家位置精確對齊，禁止生成空盒子 UI。

---

## 目錄結構 (Directory Structure)

```
Ramen-Bot/
├── app.py                  # 入口管理（LINE_TAG 切換正式/測試模式）
├── agent_router.py         # [CORE] 意圖分發大腦
├── flex_handler.py         # [CORE] UI 渲染引擎
├── prompts.py              # LLM Prompt 存放處
├── usage_tracker.py        # 每日配額檢查與 token 累計
├── processor.py            # 資料處理邏輯（預留）
├── skills/
│   ├── Search_skill.py     # [SKILL 1] 條件搜尋 + 非同步推薦文生成
│   └── info_skill.py       # [SKILL 2] 特定店家即時資訊 + 7 天快取
├── services/
│   └── google_maps.py      # Google Maps API 統一封裝
│                           #   - Geocoding API (get_latlng)
│                           #   - Places API New (get_shop_details)
│                           #   - Media Proxy (get_photo_url)
├── scripts/
│   ├── data_pipeline.py    # 資料清洗 Pipeline（IG → LLM → Maps → JSON）
│   ├── ig_scraper.py       # Instagram 公開帳號資料爬取腳本
│   └── update_api_data.py  # 批次更新資料庫 API 資料（規劃中）
├── data/
│   ├── ramen_data.json             # 主資料庫（bot 查詢使用）
│   ├── ramen_data_template.json    # 欄位格式範本
│   ├── ramen_data_checkpoint.json  # Pipeline Stage 2 中斷點備份
│   ├── instagram_data.json         # IG 爬蟲彙整輸出
│   ├── media/                      # IG 原始爬蟲資料（posts/profile/stories）
│   └── data_cleaning_rule.md       # 資料清洗規則說明
├── log/
│   └── usage.json          # 每日 API / LLM 用量紀錄
└── .env                    # 密鑰管理（不納入版控）
```

---

## 研發進度追蹤 (R&D Progress Checklist)

🟢 [CORE] 共用核心功能 (Shared Modules)
- [v] 環境基礎建設：`.env` 配置、Gemini API Key 串接。
- [v] 基礎意圖解析：解析 `location`、`style`、`shop_name`、`ui_tag` 並分發。
- [v] Flex 渲染引擎：`flex_handler.py` 字典操作邏輯開發完成。
- [v] 動態 UI 邏輯：1~3 個並排社群按鈕自動伸縮與防錯（避免空盒子）。
- [v] 推薦文配對：`enumerate` 索引確保 AI 評論與店家位置精確對齊。
- [v] 分發器升級：`agent_router.py` 支援三種 Skill 的自動切換（含 KNOWLEDGE_QUERY 佔位）。
- [v] Google API 封裝：`services/google_maps.py` 統一處理 Geocoding、Text Search、Media。
- [v] 日誌與追蹤：每日 API / LLM 用量追蹤器（`usage_tracker.py`）。
- [ ] 全局錯誤處理：實作各 Skill 失敗時的降級回退（Fallback）機制。
- [ ] 非同步應對 (Async Handling)：實作防止 LINE Webhook 逾時的非阻塞處理機制。

🍜 [SKILL 1] Search：條件找店家 (Criteria Search)
- [v] 資料結構定義：本地 `ramen_data.json` 欄位規範定案。
- [v] 本地篩選邏輯：地理經緯度比對（Haversine 5km）與字串模糊比對 fallback。
- [v] 推薦文並行生成：`asyncio` 並行呼叫 Gemini 產生每間店的專屬短評。
- [v] 多筆輪播組裝：將篩選結果轉化為 carousel 格式輸出。
- [v] IG 數據採集：`scripts/ig_scraper.py` 爬取公開 IG 帳號，輸出至 `instagram_data.json`。
- [v] AI 內容提取：`data_pipeline.py` Stage 2 批次 LLM 提取店名、地區、口味標籤。
- [v] 地址校驗與編碼：`data_pipeline.py` Stage 3 呼叫 Places API (New) 取得座標與 `place_id`。
- [ ] 數據一致性校驗：開發腳本比對 IG 地址與 Google 回傳地址的字元相似度（50% 閾值）。

📍 [SKILL 2] Info：特定店家資訊 (Specific Info)
- [v] Google API 封裝：`services/google_maps.py` 處理 Text Search 與照片取得。
- [v] 成本控管機制：每日 API 呼叫上限（`usage_tracker.py`）。
- [v] 數據欄位對齊：Field Masking 抓取評分、總評論數、地址與照片。
- [v] 照片代理服務：`get_photo_url()` 將 `photo_name` 轉換為有效 HTTPS URL。
- [v] 智慧快取系統：「檢查本地 → API 抓取 → 回寫 JSON」的 7 天 TTL 資料持久化。
- [ ] CLI 預處理工具：批次補全現有資料庫所有店家的 `place_id`（`update_api_data.py` 規劃中）。

📚 [SKILL 3] Knowledge：拉麵知識庫 (RAG System)
- [ ] 知識資料集收集：整理拉麵流派、麵條硬度、點餐禮儀等文字資料。
- [ ] 向量資料庫整合：選擇並整合向量資料庫（如 ChromaDB 或 Pinecone）。
- [ ] 檢索邏輯開發：實作相似度搜尋（Vector Search）以取得最相關的知識片段。
- [ ] 問答生成優化：撰寫專業拉麵大師語氣的 Prompt 進行回答合成。
