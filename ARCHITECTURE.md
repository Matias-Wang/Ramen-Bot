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
      |       - ChromaDB 向量相似度搜尋（Top-3）
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

### Knowledge Skill (RAG 知識庫) — `skills/knowledge_skill.py` ✅
- **數據源**：`knowledge/` 目錄下的 `.txt` / `.md` 知識文件
- **技術棧**：ChromaDB（本地持久化）+ Google Embedding API（`gemini-embedding-001`）
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
├── pytest.ini              # pytest 設定
├── requirements.txt        # 依賴套件清單
├── core/                   # 核心業務邏輯模組
│   ├── agent_router.py     # [CORE] 意圖分發大腦
│   ├── flex_handler.py     # [CORE] UI 渲染引擎
│   ├── prompts.py          # LLM Prompt 存放處
│   ├── usage_tracker.py    # 每日配額檢查與 token 累計
│   └── processor.py        # 資料處理邏輯（預留）
├── skills/
│   ├── Search_skill.py     # [SKILL 1] 條件搜尋 + 非同步推薦文生成
│   ├── info_skill.py       # [SKILL 2] 特定店家即時資訊 + 7 天快取
│   └── knowledge_skill.py  # [SKILL 3] RAG 知識庫問答（ChromaDB + Gemini Embedding）
├── services/
│   └── google_maps.py      # Google Maps API 統一封裝
│                           #   - Geocoding API (get_latlng)
│                           #   - Places API New (get_shop_details)
│                           #   - Media Proxy (get_photo_url)
├── scripts/
│   ├── data_pipeline.py            # 資料清洗 Pipeline（IG → LLM → Maps → JSON）
│   ├── ig_scraper.py               # Instagram 公開帳號資料爬取腳本
│   ├── geocode_shops.py            # 地址 Geocoding 預處理腳本
│   ├── update_api_data.py          # 批次補全店家 place_id（支援 --dry-run）
│   └── address_consistency_check.py # IG vs Google 地址字元相似度校驗（50% 閾值）
├── tests/
│   ├── test_search_skill.py    # Haversine 距離計算、店家摘要建構
│   ├── test_flex_handler.py    # Bubble 生成、Carousel 組裝
│   └── test_usage_tracker.py   # 配額檢查、日期重置、Token 累加
├── data/
│   ├── ramen_data.json             # 主資料庫（bot 查詢使用）
│   ├── ramen_data_template.json    # 欄位格式範本
│   ├── ramen_data_checkpoint.json  # Pipeline Stage 2 中斷點備份
│   ├── instagram_data.json         # IG 爬蟲彙整輸出
│   ├── media/                      # IG 原始爬蟲資料（posts/profile/stories）
│   └── data_cleaning_rule.md       # 資料清洗規則說明
├── log/
│   ├── usage.json          # 每日 API / LLM 用量紀錄
│   └── testing_result.md   # cowork從電腦版Line上的測試結果
├── knowledge/
│   ├── ramen_category.md   # 拉麵口味與流派知識文件
│   ├── ramen_etiquette.md  # 麵條硬度、點餐禮儀與常見 FAQ
│   └── .chroma_db/         # ChromaDB 向量索引（自動生成，不納入版控）
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
- [v] 全局錯誤處理：`_fallback_result()` + 頂層 try/except，任何 Skill 失敗均回傳 FALLBACK intent。
- [v] 非同步應對 (Async Handling)：`handle_message` 改為背景 `threading.Thread`，Webhook 立即返回 200。

🍜 [SKILL 1] Search：條件找店家 (Criteria Search)
- [v] 資料結構定義：本地 `ramen_data.json` 欄位規範定案。
- [v] 本地篩選邏輯：地理經緯度比對（Haversine 5km）與字串模糊比對 fallback。
- [v] 推薦文並行生成：`asyncio` 並行呼叫 Gemini 產生每間店的專屬短評。
- [v] 多筆輪播組裝：將篩選結果轉化為 carousel 格式輸出。
- [v] IG 數據採集：`scripts/ig_scraper.py` 爬取公開 IG 帳號，輸出至 `instagram_data.json`。
- [v] AI 內容提取：`data_pipeline.py` Stage 2 批次 LLM 提取店名、地區、口味標籤。
- [v] 地址校驗與編碼：`data_pipeline.py` Stage 3 呼叫 Places API (New) 取得座標與 `place_id`。
- [v] 數據一致性校驗：`scripts/address_consistency_check.py` 使用 SequenceMatcher 比對字元相似度，標記低於 50% 的店家至 `log/address_flag.json`。

📍 [SKILL 2] Info：特定店家資訊 (Specific Info)
- [v] Google API 封裝：`services/ oogle_maps.py` 處理 Text Search 與照片取得。
- [v] 成本控管機制：每日 API 呼叫上限（`usage_tracker.py`）。
- [v] 數據欄位對齊：Field Masking 抓取評分、總評論數、地址與照片。
- [v] 照片代理服務：`get_photo_url()` 將 `photo_name` 轉換為有效 HTTPS URL。
- [v] 智慧快取系統：「檢查本地 → API 抓取 → 回寫 JSON」的 7 天 TTL 資料持久化。
- [v] CLI 預處理工具：`scripts/update_api_data.py` 支援 `--dry-run`，逐筆呼叫 Places API 補全 `place_id`，含每日配額保護。

📚 [SKILL 3] Knowledge：拉麵知識庫 (RAG System)
- [v] 知識資料集收集：`knowledge/ramen_category.md`（拉麵流派、湯底、地方特色等）。
- [v] 向量資料庫整合：ChromaDB 本地持久化（`knowledge/.chroma_db/`）。
- [v] 檢索邏輯開發：Google Embedding API（`gemini-embedding-001`）向量相似度搜尋 Top-3。
- [v] 問答生成優化：`KNOWLEDGE_ANSWER_PROMPT` 拉麵大師語氣，回答 100～200 字。
- [v] 知識文件擴充：`knowledge/ramen_etiquette.md`（麵條硬度、點餐禮儀、常見 FAQ），索引擴充至 13 個段落。

> ✅ **本地開發階段全部完成**。所有 Skill 均可在 `LINE_TAG=0` 模式下正常運作與測試。
> 接下來進入上線規劃，從 **Phase 0 前置確認** 開始逐步執行。

---

## 上線規劃 (GCP + Firestore Deployment)

目標架構：全面遷移至 Firestore（資料 + 向量索引），Flask App 以 gunicorn 容器化部署至 Cloud Run。

### 本地 / 雲端共存策略（DATA_BACKEND 環境變數）

同一份 codebase，透過 `DATA_BACKEND` 環境變數切換資料後端，兩個環境可同時維護：

```
DATA_BACKEND=local     # 本地開發（預設）→ 使用 JSON + ChromaDB
DATA_BACKEND=firestore # Cloud Run 上線 → 使用 Firestore KNN
```

各模組的切換位置：
| 模組 | 切換點 | Phase |
|------|--------|-------|
| `skills/Search_skill.py` | `filter_ramen_data()` 讀取店家 | Phase 2 |
| `skills/info_skill.py` | `_load_data()` / `_save_data()` 快取回寫 | Phase 2 |
| `core/usage_tracker.py` | `_load()` / `_save()` 用量讀寫 | Phase 2 |
| `skills/knowledge_skill.py` | `__init__()` 初始化 + `answer()` KNN 查詢 | Phase 3 |

> 目前 Firestore 分支均印 WARNING 並降級至本地模式（TODO 標記）。Phase 2/3 實作時填入對應 Firestore 呼叫即可。

### 技術選型

| 元件 | 本地開發 | GCP 上線 |
|------|----------|----------|
| 應用程式主機 | `python app.py`（Flask dev server） | Cloud Run + gunicorn（1 worker / 8 threads） |
| 店家資料庫 | `data/ramen_data.json` | Firestore collection `ramen_shops` |
| 用量追蹤 | `log/usage.json` | Firestore document `config/daily_usage` |
| 向量索引 | ChromaDB 本地（`knowledge/.chroma_db/`） | Firestore collection `ramen_knowledge`（原生 KNN 向量搜尋） |
| 密鑰管理 | `.env` 檔案 | Secret Manager |
| 部署流程 | 手動啟動 | Cloud Build / GitHub Actions CI/CD |

> **為何不用 ChromaDB on GCS**：ChromaDB 是檔案型資料庫，部署至 Cloud Run 每次冷啟動都需從 GCS 下載索引（數百 MB），且多 instance 寫入時有檔案鎖定衝突。Firestore 原生向量搜尋（KNN）可完全取代，架構更簡潔。

### Phase 0：前置環境確認（開始前必做）

> **這個 Phase 不需要寫任何 code**，只需要確認工具和帳號都到位。

#### Step 1：GCP 帳號與專案
- [v] 確認有 Google 帳號，並能登入 [console.cloud.google.com](https://console.cloud.google.com)
- [v] 確認帳號已綁定信用卡（GCP 需付款方式，新帳號有 $300 USD 免費額度，90 天有效）
- [v] 在 GCP Console 建立一個新專案（例如 `ramen-bot-prod`），記下 **Project ID**
- [v] 在 `.env` 補上 `GOOGLE_CLOUD_PROJECT_ID=你的-project-id`

#### Step 2：本地工具安裝
- [v] 安裝 **Google Cloud CLI（gcloud）**：https://cloud.google.com/sdk/docs/install
  ```bash
  gcloud auth login          # 登入 Google 帳號
  gcloud config set project 你的-project-id
  ```
- [v] 安裝 **Docker Desktop**（Windows）：https://docs.docker.com/desktop/install/windows-install/
  - 確認安裝完後 `docker --version` 有輸出版本號 > 29.4.3

#### Step 3：GCP 服務啟用
- [v] 在 GCP Console 或用 gcloud 啟用以下 API：
  ```bash
  gcloud services enable run.googleapis.com
  gcloud services enable firestore.googleapis.com
  gcloud services enable secretmanager.googleapis.com
  gcloud services enable artifactregistry.googleapis.com
  ```

#### Step 4：確認可以繼續
- [v] `gcloud projects list` 能看到你的專案
- [v] `docker ps` 不報錯（Docker 正常運行）
- [v] ✅ 以上都完成後，告知 Claude → 開始 Phase 1

---

### Phase 1：容器化與基礎建設
- [ ] 撰寫 `Dockerfile`（Python 3.13 slim base image，安裝依賴）
  ```dockerfile
  CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app
  ```
  - `--workers 1`：Cloud Run 水平擴縮由平台管理，單 worker 即可
  - `--threads 8`：支援多個 LINE Webhook 並行處理
  - `--timeout 0`：交由 Cloud Run 管理逾時，防止 gunicorn 誤 kill 等待 AI 的 worker
- [ ] 撰寫 `.dockerignore`（排除 `.env`、`knowledge/.chroma_db/`、`data/`、`log/`）
- [ ] 在 GCP 建立專案，啟用 Cloud Run、Firestore、Secret Manager API
- [ ] 將 LINE Token / Gemini Key / Maps Key 上傳至 Secret Manager

### Phase 2：Firestore 資料層遷移（店家資料 + 用量追蹤）
- [ ] 設計 Firestore 資料結構：
  - `ramen_shops/{shop_id}`：每筆文件對應一間店（現有 JSON 欄位對齊）
  - `config/daily_usage`：單一 document 記錄當日計數，使用 `FieldValue.increment()` 解決多 instance 競態
- [ ] 撰寫 `scripts/migrate_to_firestore.py`：將 `ramen_data.json` 批次匯入 `ramen_shops` collection
- [ ] 更新 `skills/Search_skill.py`：從 Firestore 讀取店家資料（取代本地 JSON）
- [ ] 更新 `skills/info_skill.py`：快取回寫改為 `document.update()`（取代 JSON 寫檔）
- [ ] 更新 `core/usage_tracker.py`：讀寫改為 Firestore document，`check_and_increment` 改用 Transaction

### Phase 3：Firestore 向量索引（取代 ChromaDB）
- [ ] 設計 `ramen_knowledge/{chunk_id}` collection 欄位：
  ```
  content   : string   // 文字段落
  source    : string   // 來源檔名
  embedding : vector   // 768 維向量（gemini-embedding-001 輸出維度）
  ```
- [ ] 在 GCP Console 或 gcloud CLI 建立向量索引（`FieldPath=embedding`，`Dimension=768`，`Measure=COSINE`）
- [ ] 撰寫 `scripts/migrate_knowledge_to_firestore.py`：讀取 `knowledge/*.md`，切分段落，嵌入後批次寫入 Firestore
- [ ] 更新 `skills/knowledge_skill.py`：移除 ChromaDB 依賴，改用 Firestore `find_nearest()` KNN 查詢
- [ ] 移除 `requirements.txt` 中的 `chromadb`，新增 `google-cloud-firestore`

### Phase 4：Cloud Run 部署與 LINE Webhook 串接
- [ ] 建立 Cloud Run service，環境變數從 Secret Manager 注入
- [ ] **設定 `min-instances=1`**：徹底解決冷啟動導致 LINE Webhook 1 秒逾時的問題（費用約 $5~10 USD/月）
  > 未來若需降至 $0 可評估 Cloud Tasks + Push API 方案，但會大幅增加架構複雜度
- [ ] 將 Cloud Run 服務 URL 設定至 LINE Developers Webhook URL
- [ ] 確認 `handle_message` 的 threading 非阻塞機制在 gunicorn 環境下正常運作

### Phase 5：CI/CD 自動化
- [ ] 撰寫 `cloudbuild.yaml` 或 GitHub Actions workflow
- [ ] 推送 `main` 分支後自動 Build → Push to Artifact Registry → Deploy to Cloud Run
