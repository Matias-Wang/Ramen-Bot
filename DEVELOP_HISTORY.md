# DEVELOP_HISTORY.md — 研發歷史摘要

> 記錄本專案重要研發項目與重要優化，依時間排序。
> 詳細審查紀錄請見 `review/`，目前待辦事項請見 `PENDING.md`。

格式說明：
```
## {日期/階段} — {標題}
### 新增
### 修改
### 修正
### 移除
```

---

## 2026-04 ~ 2026-05 初 — 核心架構與三大 Skill 完成

### 新增
- Agentic Router 架構：`core/agent_router.py` 意圖分發（SEARCH_BY_CRITERIA / GET_SPECIFIC_INFO / KNOWLEDGE_QUERY）
- Search Skill：本地 `ramen_data.json` Haversine 距離過濾、字串模糊比對 fallback、隨機抽選 3 筆
- Info Skill：Google Places API (New) 7 天快取機制、Field Masking、照片代理服務
- Knowledge Skill：ChromaDB + Google Embedding API（`gemini-embedding-001`）RAG 系統
- 每日 API / LLM 用量追蹤器 `core/usage_tracker.py`
- pytest 測試架構，核心模組整理至 `core/`

---

## 2026-05-23 — SDK 遷移：google-generativeai → google-genai

### 修改
- 全面改用 `google-genai` 新版 SDK，`AgentRouter` 集中建立並管理 `genai.Client`
- `Search_skill.py` 函式簽章由 `model` 改為 `client + model_name`
- `knowledge_skill.py` embedding 改用 `result.embeddings[0].values`（新 SDK 物件存取）

---

## 2026-05-26 — Cloud Run 上線與回覆延遲修復（Phase 1-6）

### 新增
- 容器化與部署：`Dockerfile`、`.dockerignore`、Secret Manager、Cloud Run（`min-instances=1`）
- Firestore 資料層遷移：`ramen_shops`、`config/daily_usage`、`ramen_knowledge`（768 維向量索引，取代 ChromaDB）
- CI/CD：`.github/workflows/deploy.yml`（push to main → Build → Artifact Registry → Cloud Run，WIF 認證）
- `services/firestore_client.py`：Firestore Client Singleton + `start_heartbeat()` 背景執行緒，避免冷連線延遲

### 修正
- 回覆延遲問題（3-4 分鐘）：改用 `push_message` 取代 `reply_message`，根治 reply token 60 秒過期；移除有 bug 的 `asyncio.run()`，改用 `ThreadPoolExecutor` 確保推薦文真並行
- 搜尋半徑由 5km 縮小為 2km，修正中山站推薦到信義區/新竹店家的跨區問題
- 推薦結果改為 `random.sample` 隨機抽選，避免每次回覆店家順序固定

---

## 2026-06 — BUG 修復與資料 / 文件整理

### 修正
- `GET_SPECIFIC_INFO` 改輸出 Flex Bubble，以 IG 食記 `description` 生成 LLM 摘要推薦文
- 修正描述截斷問題：`Search_skill.py` fallback 改為空字串，`flex_handler.py` 改用 `shop.description[:80]` 補底
- 修正 `info_skill.py` 將查詢店家寫入 Firestore 污染搜尋資料集的問題（孤兒文件清理 + `migrate_to_firestore.py --mode=clean`）
- `knowledge_skill.py` 回答語氣由「拉麵大師」改為「拉麵知識導覽系統」，避免過度擬人化
- `core/usage_tracker.py` 的 `LOG_PATH` 路徑錯誤（原指向 `core/log/`，已改為專案根目錄 `log/`）

### 新增
- `scripts/append_new_shops.py`：將新抓取的店家資料附加至 `ramen_data.json`（不比對重複），執行前自動備份至 `data/backup/`
- `data/DATA_SCHEMA.md`：整合 `ramen_data.json` / `instagram_data.json` 欄位結構與 IG 原始資料管理（`data/resource/`）說明

### 移除
- 移除已無用腳本：`clean_instagram_data.py`、`fix_ig_links.py`、`ramen_data_csv_tool.py`、`update_descriptions.py`、`update_ratings.py`
- 移除根目錄 `DATA_PIPELINE.md`（內容已併入 `data/DATA_SCHEMA.md`，不納入版控）
- 移除空白未使用的 `DEVLOG.md`（內容由本檔案取代）

---

## 2026-06-14 — Search / Info Skill 比對邏輯修正

### 修正
- `services/google_maps.py`：`get_latlng` 加上 `region="tw"`，避免「中山區」等地名被 Geocoding 解析到海外或外縣市同名地點
- `skills/Search_skill.py`：地名未含「市/縣」時自動補上「台北市」前綴後再 Geocoding（店家資料以台北市為主，修正「中山站」「中山區」搜尋不到結果的問題）
- `core/agent_router.py`：`GET_SPECIFIC_INFO` 在店家無預存 IG `description` 時，改為即時呼叫 Gemini 生成推薦文，取代原本顯示的「點擊查看地圖了解更多」
- `skills/info_skill.py`：
  - 新店家（未收錄資料庫）的 `style` 欄位由 `"未知"` 改為空字串，避免污染推薦文 prompt 與 UI 顯示「· 未知」
  - 新增 `_find_shop_by_name()`，店名比對改為「完全比對 → difflib 相似度模糊比對（門檻 0.5，地區相符加分）」，修正使用者口語化店名（如「麒麟拉麵」）對應不到資料庫完整店名（如「麒麟創作拉麵坊」）的問題

---

## 2026-06-15 — 補齊店家經緯度座標

### 新增
- `data/geocode_shops.py`：常駐維護腳本，掃描 `ramen_data.json` 補齊缺少的 `coordinates`（支援 `--dry-run`，可重複執行，不納入版控）

### 修正
- 執行該腳本補齊 15 筆店家座標，`ramen_data.json` 171 筆店家全數具備有效座標（原始資料已備份至 `data/backup/`）

---

## 2026-06-15 — 修正「系統忙碌中」崩潰、介紹文截斷與站名搜尋跨區問題

### 修正
- `core/flex_handler.py`：`get_flex_bubble()` 的 `name` / `location` / `style` / `address`
  欄位改用 `or` 取代 `dict.get(key, default)`。原寫法在欄位「存在但值為 `None`」
  （例如 Firestore 文件中該欄位為 `null`）時會直接回傳 `None`，導致
  `quote(name)` 拋出 `TypeError`，此例外發生在 `app.py` 的 `dispatch()` 呼叫範圍之外，
  最終觸發 `_reply_to_line` 最外層的例外處理，回覆「系統忙碌中，請稍後再試。」。
  此為使用者回報「詢問中山站拉麵時回覆系統忙碌中」的根因；同一查詢重試時因
  `random.sample` 抽到不同店家而未觸發崩潰，因而「看似恢復正常」。
- `core/flex_handler.py`：新增 `_truncate_description()`，將 `description` 欄位
  的截斷邏輯由單純 `desc[:80]`（會在詞彙中間截斷，例如「...並融入獨特」後突然中止）
  改為盡量在標點符號處斷句並補上「…」，避免介紹文出現不完整詞彙。
- `skills/Search_skill.py`：新增 `_build_geocode_query()`，將地名以「站」結尾
  （例如「中山站」）的查詢改用「台北捷運」前綴（如「台北捷運中山站」），
  避免 Geocoding API 將「OO站」誤判為同名但不相關的公車站/地標（例如萬華區的
  「中山堂(西門)」站牌），造成搜尋座標錨點落在錯誤行政區，回傳跨區（如萬華區）
  的店家。

### 已知待處理（資料問題，非程式邏輯）
- 使用者詢問「介紹麒麟拉麵」時，比對到的店家為「麒麟創作拉麵坊」，但其
  `description` 欄位內容描述的卻是另一間店「木麒麟拉麵」（例如：「『木麒麟拉麵』
  承襲了麒麟本店的精髓...」）。這是 `ramen_data.json` / Firestore
  `ramen_shops` 資料本身的內容錯置問題（IG 食記與店家對應錯誤），需直接檢視並
  修正該筆資料的 `description` 欄位，非程式邏輯可修正範圍。

---

## 2026-06-16 — 強化 filter_ramen_data 測試覆蓋

### 新增
- `tests/test_search_skill.py` 新增 11 個測試案例（`TestFilterRamenDataLocationQueries`、`TestFilterRamenDataStyleAndCombined`），直接針對 `filter_ramen_data()` 核心邏輯：
  - 捷運站查詢回傳鄰近店家並依距離排序
  - 不同捷運站查詢（公館站）驗證邏輯非針對單一站名硬編碼
  - 行政區查詢自動補上「台北市」前綴
  - Geocoding 失敗時回退至字串模糊比對
  - 無座標店家透過字串比對納入結果
  - style 過濾、通用詞（「推薦」）不過濾、組合查詢、無結果回傳空清單、超過 3 筆隨機抽 3 筆上限
- `_mock_search_dependencies` autouse fixture，以固定測試資料與模擬 Geocoding 取代 `_load_all_shops` / `_get_latlng_cached`，不依賴實際 `ramen_data.json` 或 Google Maps API
- 全套測試由 59 個增至 70 個，全數通過（commit `ffed325`，CI/CD 部署成功）

