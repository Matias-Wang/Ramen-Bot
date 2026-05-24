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
      +-- KNOWLEDGE_QUERY    → KnowledgeSkill.answer() → [answer text]
      |       - Google Embedding API 嵌入查詢
      |       - 本地：ChromaDB 向量相似度搜尋（Top-3）
      |       - 生產：Firestore KNN find_nearest()（Top-3）
      |       - Gemini 生成拉麵大師風格回答
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
- **數據源**：本地 `data/ramen_data.json` / 生產 Firestore `ramen_shops` collection + Geocoding API
- **核心邏輯**：
    1. 口味比對：`target_style in shop["style"]` 模糊比對
    2. 地區比對（優先序）：
       - 有座標 → Geocoding 取目標座標 → Haversine 計算距離 → 5km 以內
       - 無座標 → 字串去除「市/區/縣」後模糊比對 fallback
    3. 結果依 `distance_km` 排序（若有座標）
- **推薦文生成**：`asyncio.gather` 並行呼叫 Gemini，最多 3 筆

### Info Skill (即時數據增強) — `skills/info_skill.py`
- **數據源**：本地 `data/ramen_data.json` / 生產 Firestore `ramen_shops` + Google Places API (New)
- **快取邏輯**：
    1. 檢查本地（或 Firestore）是否有 `place_id` 且 `last_updated` 在 7 天以內
    2. 若需更新 → Text Search 取得 `place_id`、評分、評論數、照片
    3. 更新後回寫本地 JSON（或 Firestore document）
- **Field Masking**：`id, displayName, rating, userRatingCount, formattedAddress, photos`

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
- **非阻塞處理**：`handle_message` 收到請求後立即啟動 `threading.Thread`，主執行緒直接返回 200，AI 邏輯在背景完成後再呼叫 `reply_message`。Firestore singleton 將處理時間壓縮至 15-20 秒，可安全在 reply token 有效期（60 秒）內完成。
- **快取優先策略 (Cache-First)**：對 API 數據實施 7 天的快取機制，減少重複請求延遲。
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
| 非同步 | `asyncio`（推薦文並行）+ `threading`（Webhook 非阻塞） | 同左 |

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
│   └── usage_tracker.py    # 每日配額檢查（本地 JSON / Firestore 雙路徑）
├── skills/
│   ├── Search_skill.py     # [SKILL 1] 條件搜尋（本地 JSON / Firestore 雙路徑）
│   ├── info_skill.py       # [SKILL 2] 特定店家即時資訊（本地 JSON / Firestore 雙路徑）
│   └── knowledge_skill.py  # [SKILL 3] RAG 知識庫（ChromaDB 本地 / Firestore KNN 雙路徑）
├── services/
│   ├── google_maps.py          # Google Maps API 統一封裝
│   └── firestore_client.py     # Firestore Client Singleton（全域共用連線）
├── scripts/
│   ├── data_pipeline.py                  # 資料清洗 Pipeline（IG → LLM → Maps → JSON）
│   ├── ig_scraper.py                     # Instagram 公開帳號資料爬取腳本
│   ├── geocode_shops.py                  # 地址 Geocoding 預處理腳本
│   ├── update_api_data.py                # 批次補全店家 place_id（支援 --dry-run）
│   ├── address_consistency_check.py      # IG vs Google 地址字元相似度校驗（50% 閾值）
│   ├── migrate_to_firestore.py           # 店家資料匯入/同步 Firestore（import / sync 模式）
│   ├── migrate_knowledge_to_firestore.py # 知識庫向量索引寫入 Firestore（支援 --force）
│   └── setup_secrets.ps1                 # 從 .env 一鍵上傳金鑰至 Secret Manager
├── tests/
│   ├── test_search_skill.py    # Haversine 距離計算、店家摘要建構
│   ├── test_flex_handler.py    # Bubble 生成、Carousel 組裝
│   └── test_usage_tracker.py   # 配額檢查、日期重置、Token 累加
├── data/
│   ├── ramen_data.json             # 主資料庫（本地開發 / 遷移來源）
│   ├── ramen_data_template.json    # 欄位格式範本
│   ├── ramen_data_checkpoint.json  # Pipeline Stage 2 中斷點備份
│   ├── instagram_data.json         # IG 爬蟲彙整輸出
│   ├── media/                      # IG 原始爬蟲資料（posts/profile/stories）
│   └── data_cleaning_rule.md       # 資料清洗規則說明
├── log/
│   ├── usage.json          # 每日 API / LLM 用量紀錄（本地開發用）
│   └── testing_result.md   # cowork從電腦版Line上的測試結果
├── knowledge/
│   ├── ramen_category.md   # 拉麵口味與流派知識文件
│   ├── ramen_etiquette.md  # 麵條硬度、點餐禮儀與常見 FAQ
│   └── .chroma_db/         # ChromaDB 向量索引（本地自動生成，不納入版控）
└── .env                    # 密鑰管理（不納入版控）
```

---

> 研發進度與上線規劃詳見 [`PROGRESS.md`](PROGRESS.md)（不納入版控）。
