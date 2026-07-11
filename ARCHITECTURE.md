# RAMEN_BOT_PROJECT_SPEC (v3.4)

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
|REPORT_ERROR|使用者指正某間店家資料有誤（地址、描述、評分等）|Feedback Skill|

### 系統架構
```
[User Input via LINE]
      |
[STEP 1] AgentRouter.dispatch() — Gemini 解析意圖
      | intent_data: {intent, location, style, shop_name, radius_km, open_now, ui_tag}
      |
[STEP 2] Skill 執行
      +-- SEARCH_BY_CRITERIA → filter_ramen_data() → [shop list]
      |       - Geocoding 取座標 → Haversine 半徑 2km 過濾
      |       - 無座標則字串模糊比對 fallback
      |       - 結果超過 3 筆時 random.sample 隨機抽選
      |
      +-- GET_SPECIFIC_INFO  → InfoSkill.get_shop_info() → [shop]
      |       - 本地快取優先（7天 TTL）
      |       - 過期則呼叫 Places API (New)
      |
      +-- KNOWLEDGE_QUERY    → KnowledgeSkill.answer() → [answer text]
      |       - Google Embedding API 嵌入查詢
      |       - 本地：ChromaDB 向量相似度搜尋（Top-3）
      |       - 生產：Firestore KNN find_nearest()（Top-3）
      |       - Gemini 生成拉麵大師風格回答
      |
      +-- REPORT_ERROR       → collect_report() → 確認訊息文字
      |       - 萃取 shop_name + error_description（來自 Gemini 解析）
      |       - 本地：寫入 log/feedback_reports.json
      |       - 生產：寫入 Firestore feedback_reports collection
      |       - 回覆 LINE 使用者確認訊息（TextSendMessage）
      |
[STEP 3] 推薦文 / 介紹文生成
      |       - SEARCH_BY_CRITERIA：generate_recommendations() — ThreadPoolExecutor +
      |         預熱 Client Pool 並行生成 3 筆推薦文（30-60 字）
      |       - GET_SPECIFIC_INFO：有 description 時呼叫 summarize_description()，
      |         以 INFO_SUMMARY_PROMPT 摘要為列點式介紹文（150 字以內）；無 description 時
      |         回退至 generate_recommendations() 生成 1 筆推薦文（30-60 字）
      |
[STEP 4] flex_handler UI 組裝
      |       - SEARCH_BY_CRITERIA：assemble_carousel() → Flex Carousel（最多 3 個 bubble）
      |       - GET_SPECIFIC_INFO：get_flex_bubble() → 單一 Flex Bubble（含 Map + social_links 按鈕）
      |
[LINE Flex Message Response]
```

---

## 技能開發規範 (Skill Specifications)

### Search Skill (條件搜尋) — `skills/Search_skill.py`
- **數據源**：本地 `data/ramen_data.json` / 生產 Firestore `ramen_shops` collection + Geocoding API
- **核心邏輯**：
    1. 口味比對：`target_style in shop["style"]` 模糊比對
    2. 地區比對（依查詢類型分流）：
       - 行政區查詢（地名以「區/市/縣」結尾）→ 跳過 Geocoding，直接以字串去除
         「市/區/縣」後比對 `shop["location"]`。原因：行政區面積大且形狀不
         規則，Geocoding 回傳的幾何中心點常離店家聚集處 2km 以上，放大半徑
         又會等量誤抓鄰近行政區店家，無安全半徑值（見 PENDING.md 中山區案例）
       - 捷運站等精確點查詢 → Geocoding 取目標座標 → Haversine 計算距離 →
         預設 **2km** 以內；查無座標則同樣回退至上述字串比對。使用者若明確指定
         範圍（intent 的 `radius_km`，例如「方圓 5 公里」）則覆寫此預設半徑
    3. 「現在有開」查詢（intent 的 `open_now` 為真）：以 `is_open_at()` 依店家
       `opening_hours.periods`（Places API 原生結構，day 0=週日）與注入的 `current_time`
       過濾當下營業中的店家；**無營業時間資料的店家一律排除**（無法確認，寧缺勿錯）
    4. 結果依 `distance_km` 排序（若有座標），超過 3 筆則 `random.sample` 隨機抽選
- **推薦文生成**：`ThreadPoolExecutor` + 預熱 Client Pool 並行呼叫 Gemini，最多 3 筆
- **輸出格式**：FlexSendMessage Carousel（最多 3 個 bubble，含 Map 按鈕 + social_links 按鈕）
- **定位推薦（LocationMessage 專用）**：`filter_by_location(lat, lng, radius_km=5.0, style)`
  不經 Geocoding、不隨機抽選，直接對店家快取跑 Haversine，依距離排序取最近 ≤3 間；
  回傳 `(results, nearest_km)`，`nearest_km` 供半徑內找不到時回覆使用者最近一間的距離

### Info Skill (即時數據增強) — `skills/info_skill.py`
- **數據源**：本地 `data/ramen_data.json` / 生產 Firestore `ramen_shops` + Google Places API (New)
- **快取邏輯**：
    1. 檢查本地（或 Firestore）是否有 `place_id` 且 `last_updated` 在 7 天以內
    2. 若需更新 → Text Search 取得 `place_id`、評分、評論數、照片
    3. 更新後回寫本地 JSON（或 Firestore document）
- **Field Masking**：`id, displayName, rating, userRatingCount, formattedAddress, photos`
- **介紹文生成**：有 `description`（IG 食記）時，以 `INFO_SUMMARY_PROMPT` 呼叫 Gemini 摘要為列點式介紹文、總長 150 字以內（`summarize_description()`）；無 `description` 時回退至 `RECOMMEND_PROMPT` 生成 30-60 字推薦文（同 Search Skill 的 `generate_recommendations`）。兩個函式皆已關閉 `gemini-2.5-flash` 預設的 thinking（`thinking_config=ThinkingConfig(thinking_budget=0)`），避免思考鏈消耗 `max_output_tokens` 預算導致輸出被硬切斷
- **輸出格式**：FlexSendMessage 單一 Bubble，含 Map 按鈕 + social_links 按鈕（邏輯與 Search Skill Carousel 相同，使用 `get_flex_bubble()`）

### Feedback Skill (錯誤回報佇列) — `skills/feedback_skill.py`
- **數據源**：本地 `log/feedback_reports.json` / 生產 Firestore `feedback_reports` collection
- **核心邏輯**：
    1. `collect_report(shop_name, error_description, user_id)`：將回報寫入儲存（雙路徑），每筆含 UUID、時間戳、`status="pending"`
    2. `check_pending_reports()`：於 `app.py` 啟動時自動呼叫，讀取 `status=pending` 的回報並印出至 console
- **修正流程**：人工確認後，將回報的 `status` 欄位改為 `"resolved"`（不再被 `check_pending_reports` 列出）
- **輸出格式**：TextSendMessage 確認訊息（不使用 Flex）

### Knowledge Skill (RAG 知識庫) — `skills/knowledge_skill.py`
- **數據源**：`knowledge/` 目錄下的 `.txt` / `.md` 知識文件
- **技術棧**：本地 ChromaDB + 生產 Firestore `ramen_knowledge` collection，均使用 Google Embedding API（`gemini-embedding-001`，768 維）
- **核心邏輯**：
    1. 啟動時自動掃描 `knowledge/` 目錄，切分段落（500 字元，50 字元重疊）
    2. 呼叫 Google Embedding API 建立向量索引，持久化至 `knowledge/.chroma_db/`
    3. 若索引已存在則直接載入，不重複建立（刪除 `.chroma_db/` 可強制重建）
    4. 查詢時嵌入問題（`task_type=retrieval_query`），取 Top-3 相關段落
    5. 組合 context 後交由 Gemini 以「拉麵大師」語氣生成回答
- **Prompt**：`KNOWLEDGE_ANSWER_PROMPT`（位於 `core/prompts.py`）

---

## 工程挑戰與優化 (Engineering Challenges)

### LINE Webhook 逾時應對 (1s Limit)
LINE 伺服器要求 Webhook 必須在 1 秒內回應。本系統以雙層策略應對：
- **非阻塞處理**：`handle_message` 收到請求後立即啟動 `threading.Thread`，主執行緒直接返回 200，AI 邏輯在背景完成後以 `push_message` 回覆（取代有 60 秒過期限制的 `reply_message`）。啟動時預熱 Firestore、Gemini、Google Maps、Gemini Client Pool，確保首次請求也能快速回應。
- **快取優先策略 (Cache-First)**：對 API 數據實施 7 天的快取機制，減少重複請求延遲。
- **預處理機制**：透過 `scripts/build_new_shops.py` 離線完成 IG 數據採集與座標化，確保查詢時不需即時等待；若有店家因人工新增或 Pipeline 中斷而缺少座標，可額外執行 `data/geocode_shops.py` 補齊，不影響已有座標的店家。

### Webhook 事件特徵擴充（v1 已完成）
> 對應 `line_response_usage.md` 第一階段（內容已併入本文件後刪除原檔）；第二、三階段（多模態事件、SLM 微調）列為後續優化項目，詳見 `PENDING.md`。

- **訊息去重 (`event.message.id`)**：`core/message_dedup.py` 的 `is_duplicate_message()` 在 `handle_message` 啟動背景執行緒前檢查，重複請求直接跳過（仍回 200），避免 LINE webhook 重送導致 Gemini 被重複觸發計費。本地：記憶體 `set`；生產：Firestore `processed_message_ids` collection（雙路徑判斷邏輯同 `DATA_BACKEND`）。
- **時間感知 (`event.timestamp`)**：`handle_message` 將毫秒時戳轉換為台北時間字串，透過 `AgentRouter.dispatch(user_text, current_time=...)` 注入 STEP 1 意圖解析的 `contents`，供 Gemini 解析「今天」、「最近」等相對時間用語。
  - 注：目前 `ramen_data.json` 尚無營業時間欄位，「現在有開的店」類查詢仍需額外資料工程才能實作，此處僅先注入時間感知本身。
- **來源環境分流 (`event.source.type`)**：`handle_message` 最前面判斷，非一對一私聊（`source.type != "user"`，即群組/多人聊天室）直接忽略、不呼叫 Gemini。範圍經使用者確認：目前不需要 @ 標記偵測。

### 多事件響應：LocationMessage 定位推薦（階段二）
使用者直接分享 LINE 位置（GPS pin）時，`app.py` 的 `@handler.add(MessageEvent, message=LocationMessage)`
→ `handle_location` → 背景執行緒 `_reply_location(lat, lng, user_id)`：
- **繞過 AgentRouter**：位置訊息無文字語意，不需 Gemini 意圖解析（省一次 LLM 呼叫），
  直接呼叫 `filter_by_location()` 跑 Haversine。
- **預設 5km 半徑**，取最近 ≤3 間 → `generate_recommendations` + `assemble_carousel` → Carousel。
- **找不到時的透明度**：半徑內無結果則回覆文字明白告知「已在 5 公里內搜尋、最近一間約在 X 公里外」，
  並提示可用文字指定更大範圍。
- 沿用 `handle_message` 的私聊分流與 `message.id` 去重兩道防護。

> 範圍指定/放大由**文字查詢路徑**承擔（意圖解析的 `radius_km` 覆寫 `filter_ramen_data` 的固定半徑），
> 不在純 GPS 位置訊息引入跨訊息對話狀態（簡化設計）。

### 自動化優化管線埋點（conversation_logs）
為了讓地端 Claude Code 能分析真實對話、抓出意圖分類錯誤並盤點資料盲區，系統在
AgentRouter 解析完意圖後埋點：
- **埋點位置**：`core/conversation_logger.py` 的 `log_conversation()`，由 `_dispatch_inner`
  在 STEP 3 完成、回傳前呼叫（STEP 1 例外的 FALLBACK 路徑不埋點）。
- **非阻塞**：僅在 `DATA_BACKEND=firestore` 生效，以 fire-and-forget daemon thread 寫入
  Firestore `conversation_logs`，不增加 dispatch 延遲；寫入失敗只印錯誤、不影響 LINE 回覆。
  本地終端模式為 no-op。
- **紀錄 schema**：`{timestamp（台北時間）, user_input, predicted_skill, args}`，`predicted_skill`
  由 intent 映射為可讀 skill 名，`args` 為意圖字典去除 `intent`/`ui_tag`。
- **地端同步**：`scripts/fetch_cloud_data.py` 把 `conversation_logs` → `data_logs/tracking_conversations.jsonl`、
  `feedback_reports` → `data_logs/tracking_feedbacks.json`（皆完整覆寫，`data_logs/` 不進版控 / 容器）。

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
- **Pipeline 驗證**：`build_new_shops.py` 呼叫 `services/google_maps.py` 的 `verify_shop_status()`（Places API New）驗證店家是否仍在營業，過濾 `CLOSED_PERMANENTLY` 店家。
- **型別安全渲染**：`flex_handler.py` 嚴格執行索引配對，確保推薦文案與店家位置精確對齊，禁止生成空盒子 UI。

### 本地 / 生產雙路徑設計（DATA_BACKEND）
透過環境變數在同一份 codebase 中切換資料後端，本地與生產可同時維護：

| `DATA_BACKEND` | 資料層 | 向量索引 | 啟動方式 |
|----------------|--------|---------|---------|
| `local`（預設） | `data/ramen_data.json` | ChromaDB | `python app.py` |
| `firestore` | Firestore `ramen_shops` | Firestore KNN | Cloud Run + gunicorn |

各模組均在對應函式頂層以 `USE_FIRESTORE = os.getenv("DATA_BACKEND", "local") == "firestore"` 判斷路徑。

**Firestore Client Singleton**：`services/firestore_client.py` 提供全域單一 `firestore.Client` 實例（`get_db()`），所有模組共用同一 gRPC 連線，避免每次請求重新建立連線的高延遲（每次建立需 5-30 秒）。

---

## 技術棧 (Tech Stack)

| 類別 | 本地開發 | 生產（Cloud Run） |
|------|---------|----------------|
| 語言 | Python 3.13.11 | Python 3.13.11 |
| Web 框架 | Flask dev server | Flask + gunicorn（1 worker / 8 threads） |
| 套件管理 | UV | pip（Dockerfile 內） |
| LLM | Gemini `google-genai` | 同左 |
| LINE SDK | `line-bot-sdk` | 同左 |
| 地圖服務 | `googlemaps`（Geocoding）、`requests`（Places API New） | 同左 |
| 資料庫 | JSON 檔案 | Firestore（`ramen_shops`、`config/daily_usage`） |
| 向量搜尋 | ChromaDB（本地持久化） | Firestore KNN（`ramen_knowledge`） |
| Embedding | Google `gemini-embedding-001`（768 維） | 同左 |
| 密鑰管理 | `.env` | GCP Secret Manager |
| 部署流程 | 手動啟動 | GitHub Actions（push to main → Build → Artifact Registry → Cloud Run） |
| 非同步 | `threading.ThreadPoolExecutor`（推薦文並行）+ `threading`（Webhook 非阻塞） | 同左 |

---

## 目錄結構 (Directory Structure)

```
Ramen-Bot/
├── app.py                  # 入口管理（LINE_TAG 切換正式/測試模式）
├── Dockerfile              # Cloud Run 容器化設定（gunicorn）
├── .dockerignore           # 排除 .env / data/ / log/ / .chroma_db/ 等
├── pytest.ini              # pytest 設定
├── requirements.txt        # 依賴套件清單
├── .github/
│   └── workflows/
│       └── deploy.yml      # CI/CD：push to main → Build → Artifact Registry → Cloud Run
├── core/                   # 核心業務邏輯模組
│   ├── agent_router.py     # [CORE] 意圖分發大腦（含全局 Fallback）
│   ├── flex_handler.py     # [CORE] UI 渲染引擎
│   ├── prompts.py          # LLM Prompt 存放處
│   ├── conversation_logger.py  # 對話特徵埋點（雲端 conversation_logs，本地 no-op）
│   └── usage_tracker.py    # 每日配額檢查（本地 JSON / Firestore 雙路徑）
├── skills/
│   ├── Search_skill.py     # [SKILL 1] 條件搜尋（本地 JSON / Firestore 雙路徑）
│   ├── info_skill.py       # [SKILL 2] 特定店家即時資訊（本地 JSON / Firestore 雙路徑）
│   ├── knowledge_skill.py  # [SKILL 3] RAG 知識庫（ChromaDB 本地 / Firestore KNN 雙路徑）
│   └── feedback_skill.py   # [SKILL 4] 錯誤回報佇列（本地 JSON / Firestore 雙路徑）
├── services/
│   ├── google_maps.py          # Google Maps API 統一封裝
│   └── firestore_client.py     # Firestore Client Singleton（全域共用連線）
├── scripts/
│   ├── build_new_shops.py                # 資料清洗 Pipeline（data/resource/ IG 匯出包 → LLM → Maps → 候選 JSON）
│   ├── update_api_data.py                # 批次補全店家 place_id（支援 --dry-run）
│   ├── migrate_to_firestore.py           # 店家資料匯入/同步 Firestore（import / sync 模式）
│   ├── migrate_knowledge_to_firestore.py # 知識庫向量索引寫入 Firestore（支援 --force）
│   ├── setup_secrets.ps1                 # 從 .env 一鍵上傳金鑰至 Secret Manager
│   ├── append_new_shops.py               # 將 build_new_shops.py 產出的候選清單附加至 ramen_data.json（不比對重複）
│   └── fetch_cloud_data.py               # 地端同步：雲端 conversation_logs / feedback_reports → data_logs/
├── tests/
│   ├── test_search_skill.py    # Haversine 距離計算、店家摘要建構
│   ├── test_flex_handler.py    # Bubble 生成、Carousel 組裝
│   └── test_usage_tracker.py   # 配額檢查、日期重置、Token 累加
├── data/
│   ├── ramen_data.json             # 主資料庫（本地開發 / 遷移來源）
│   ├── ramen_data_template.json    # 欄位格式範本
│   ├── ramen_data_checkpoint.json  # Pipeline Stage 2 中斷點備份
│   ├── instagram_data.json         # IG 爬蟲彙整輸出
│   ├── geocode_shops.py            # 常駐腳本：補齊缺少的經緯度座標（支援 --dry-run，可重複執行，不納入版控）
│   └── DATA_SCHEMA.md              # 資料結構與 IG 原始資料管理說明（不納入版控）
├── log/
│   ├── usage.json              # 每日 API / LLM 用量紀錄（本地開發用）
│   └── feedback_reports.json   # 使用者錯誤回報佇列（本地開發用，生產用 Firestore）
├── knowledge/
│   └── .chroma_db/         # ChromaDB 向量索引（本地自動生成，不納入版控；來源 .md 已於索引建立後移除）
└── .env                    # 密鑰管理（不納入版控）
```

---

> 研發進度與上線規劃詳見 [`PENDING.md`](PENDING.md)（不納入版控）。
