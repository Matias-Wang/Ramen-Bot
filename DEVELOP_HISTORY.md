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

### 已知待處理（資料問題，非程式邏輯）→ ✅ 已解決
- ~~使用者詢問「介紹麒麟拉麵」時，比對到的店家為「麒麟創作拉麵坊」，但其
  `description` 欄位內容描述的卻是另一間店「木麒麟拉麵」。~~
  複查確認該筆資料已於先前某次維護中修正，本地 `ramen_data.json` 與 Firestore
  `ramen_shops` 的 description 現皆正確描述自己。另已新增
  `scripts/check_description_shop_match.py` 掃描全庫，179 筆無任何可疑錯置，
  並納入 `add-new-shops` skill 的合併後建議步驟。詳見 `PENDING.md`
  「測試與資料品質」表格第 2 項。

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

---

## 2026-06-21 — 修正行政區查詢無安全半徑值問題

### 修正
- `skills/Search_skill.py`：`filter_ramen_data()` 新增 `is_district_query`
  判斷（地名以「區/市/縣」結尾），行政區查詢完全跳過 Geocoding，改走既有
  字串比對 fallback（比對 `shop["location"]`）。根因：實測 Geocoding API，
  「中山區」回傳的幾何中心點 `(25.0792, 121.5427)` 落在區域邊緣，離店家
  聚集處（中山站周邊）超過 2.3km，2km 半徑內 0 間符合；放大半徑至 4km
  （涵蓋全部 21 間中山區店家）會同時誤抓 22 間大安/內湖/士林/新竹店家，
  確認半徑法在此資料集無安全值。捷運站等精確點查詢維持原 Geocoding +
  Haversine 2km 邏輯不變。
- 連帶修正字串比對 fallback 既有潛在 bug：`location` 欄位為空字串的店家會
  因 `"" in clean_target_loc` 恆為 True 誤判為符合任何地區查詢，已加上
  `shop_loc and (...)` 防呆，兩處重複邏輯同步修正。

### 新增
- `tests/test_search_skill.py` 新增 3 個測試案例（行政區查詢跳過 Geocoding、
  地區字串正確比對排除其他區、`location` 為空的店家不誤配對），測試
  fixture 新增 `shop_missing_location`。全套測試由 88 個增至 90 個，全數
  通過（review/review_20260621_1721.md，PASSED）。

---

## 2026-06-26 — 重建新店家資料建立流程，取代失效的 IG 爬蟲 Pipeline

### 新增
- `scripts/build_new_shops.py`：取代原先已失效的 `ig_scraper.py`／
  `data_pipeline.py`。直接掃描 `data/resource/*/your_instagram_activity/media/posts_1.json`
  （IG 官方「下載你的資料」匯出包，使用者已將 `data/newdata/` 改名為
  `data/resource/` 以符合慣例），修正 IG 匯出常見的 Latin-1/UTF-8 雙重編碼
  亂碼，排除已存在於 `ramen_data.json` 的貼文 id 與空白文案後，呼叫
  Gemini（`google-genai` SDK，沿用 `core/agent_router.py` 既有呼叫慣例）
  批次判斷是否為拉麵食記並結構化提取，再呼叫新增的
  `services/google_maps.py` 的 `verify_shop_status()` 驗證營業狀態與座標、
  過濾永久歇業店家，最終輸出候選清單至 `data/ramen_data_new_<時間戳>.json`，
  不直接覆寫 `ramen_data.json`，供人工確認後執行既有的
  `scripts/append_new_shops.py` 合併。
- `services/google_maps.py` 新增 `verify_shop_status()` 方法：Places API
  (New) Text Search，Field Mask 為 `places.id`、`places.businessStatus`、
  `places.location`、`places.formattedAddress`，與既有 `get_shop_details()`
  （評分/照片用途）職責分離。

### 移除
- 刪除 `scripts/ig_scraper.py`、`scripts/data_pipeline.py`：兩者已於
  `PENDING.md`（2026-06 維護紀要）標記為無法直接執行（依賴套件未列在
  `requirements.txt`、預期輸入檔案不存在、`ig_scraper.py` schema 與
  `ramen_data.json` 不相容且會整份覆寫資料），且全專案無程式碼引用，
  確認可安全刪除。

### 驗證
- 實測執行 `build_new_shops.py`：掃描兩包 `data/resource/` 匯出資料，找到
  5 筆新貼文，全數判定為拉麵食記並通過 Places API 驗證取得
  `place_id`／座標／地址，輸出格式與 `ramen_data.json` schema 一致。
  全套 95 個測試通過（review/review_20260626_1645.md，PASSED）。

---

## 2026-06-27 — 修正 build_new_shops.py 三項正確性問題，建立 add-new-shops skill

### 修正
- `scripts/build_new_shops.py`：人工檢視前一日新增腳本的實際輸出後發現三個問題：
  1. `description` 被 Gemini 改寫/摘要，非原始文案。已移除 prompt 中的
     `description` 輸出欄位，最終 `description` 一律取自程式碼層解析（僅修正
     IG 匯出常見的 Latin-1/UTF-8 雙重編碼亂碼），LLM 完全不參與此欄位生成。
  2. `media_id_to_ig_url()`（沿用自舊版 `data_pipeline.py`）對媒體檔名數字
     做 base64 還原以重建貼文短碼，但用 `ramen_data.json` 中已知的
     id/真實短碼配對反向驗證後證實此演算法從根本上是錯的；且 IG 官方
     「下載你的資料」匯出包本身不含貼文短碼/permalink，此資訊無法從這個
     資料來源推導。已移除此函式，`social_links` 改為誠實留空（`null`），
     需要真實連結時由人工查找後手動補上。
  3. `image_url` 原本寫入本機檔案相對路徑（非有效 URL，等同於當天另一個
     獨立發現的「131 家既有店家圖片皆為佔位圖」問題根因）。已改為取得
     `place_id` 後呼叫 `GoogleMapsService.get_photo_by_place_id()`
     取得真實 `https://lh3.googleusercontent.com/...` 照片。
- 重新執行驗證：5 筆候選資料三項問題皆修正確認，全套 95 個測試通過
  （review/review_20260627_0027.md，PASSED）。

### 新增
- `.claude/skills/add-new-shops/SKILL.md`：將「掃描 `data/resource/` →
  Gemini 提取 → Places 驗證 → 人工審查清單 → 合併 → 視情況同步 Firestore」
  整個流程寫成 Claude Code skill，供使用者每月手動執行一次。內含審查
  checklist（description 必須逐字、image_url 必須是 https 或 null、
  social_links 必須是 null）與 Google Maps API 每日配額暫時放寬的標準
  操作程序。

### 移除
- `scripts/geocode_shops.py`：與 `data/geocode_shops.py`
  diff 比對後確認為舒舊重複檔（無 `--dry-run`、無備份、路徑處理較粗略），
  `ARCHITECTURE.md` 已文件化 `data/geocode_shops.py` 為正式版本，全專案
  無任何地方引用 `scripts/` 版本，確認安全移除。
- `scripts/address_consistency_check.py`：比對邏輯依賴
  `address_raw`/`ig_address` 欄位，但確認全專案歷來無任何腳本（含已移除的
  `data_pipeline.py` 與新的 `build_new_shops.py`）寫入過此欄位，
  `ramen_data.json` 171 筆均無此欄位，執行時 100% 跳過比對、從未真正
  標記過任何結果，屬於從未發揮作用的死碼，確認移除。

---

## 2026-06-28 — 修正 build_new_shops.py 單張照片貼文文案遺漏 bug，合併 9 筆新店家

### 修正
- `scripts/build_new_shops.py` `collect_candidate_posts()`：使用者質疑前次（2026-06-27）
  僅找到 5 筆候選店家「不可能只有五筆」，重新比對 IG 匯出 schema 後發現：單張照片貼文的文案
  實際存放在 `media[0]["title"]`，而非貼文層級的 `title`（該欄位只存在於輪播貼文）。原邏輯
  只讀貼文層級 `title`，導致 9 筆候選中有 4 筆單張照片貼文被誤判為空文案而跳過。已加上
  `post.get("title") or media[0].get("title", "")` fallback 修正。
- `scripts/build_new_shops.py` `run_llm_extraction()`：`social_links` 預設輸出第一筆 label
  改為 `"我的 IG"`（原為 `null`），配合使用者長期慣例，往後人工補連結時只需填 `url`。

### 驗證
- 修正後重跑：候選數由 5 筆增至 9 筆，新增 4 筆（麵魚、拉麵天外天、達摩拉麵、博多一幸舍）
  人工確認皆為真實拉麵食記。9 筆全數通過 Gemini 拉麵食記判定與 Places API 驗證（取得
  `place_id`／座標／地址／真實照片，無歇業店家）。使用者人工補上 IG 真實短碼連結後，執行
  `append_new_shops.py` 合併進 `ramen_data.json`（171 → 180 筆，已自動備份）。全套 95 個測試
  通過（review/review_20260628_0143.md，PASSED）。

---

## 2026-06-28 — 找出並修正店家介紹文隨機被截斷的根本原因（thinking tokens）

### 修正
- 使用者回報 `search_skill` 第 2、3 筆店家、`info_skill` 的店家介紹文仍會出現「未經處理、
  像被硬切斷」的狀況，只有 `search_skill` 第 1 筆是完整的。以真實 Gemini API
  （`gemini-2.5-flash`）重現後找到根本原因：模型預設啟用 thinking（思考鏈），思考過程
  消耗的 tokens 計入 `max_output_tokens` 總預算（實測單次思考鏈耗用 238～383 tokens），
  常吃光 `_get_recommendation_threaded()`（400 tokens）與 `summarize_description()`
  （600 tokens）的輸出預算，導致可見文字被硬切斷在句子中間
  （`finish_reason=MAX_TOKENS`）。「哪一筆完整」純屬隨機，與第幾筆店家無關——這也解釋了
  為何同一段程式碼第 1 筆有時完整、第 2/3 筆卻被切斷。`knowledge_skill.py` 因從未設定
  `max_output_tokens` 上限而未受影響，間接佐證根因確實是 thinking tokens 排擠輸出預算。
- `skills/Search_skill.py`：`_get_recommendation_threaded()` 與 `summarize_description()`
  的 `GenerateContentConfig` 皆新增 `thinking_config=ThinkingConfig(thinking_budget=0)`
  關閉思考鏈（短文案不需要推理）。
- `core/prompts.py`：`INFO_SUMMARY_PROMPT` 字數上限由 100~150 字提高至 150~200 字
  （使用者要求：有 `description` 時 info_skill 字數應高於 search_skill 的 30~60 字；
  無 `description` 時維持既有 `generate_recommendations()` 30~60 字 fallback，
  `agent_router.py` 分支邏輯不變）。

### 驗證
- 以真實 API 重跑 `RECOMMEND_PROMPT`／`INFO_SUMMARY_PROMPT` 各 5 次，加上
  `thinking_budget=0` 後皆為 5/5 次 `finish_reason=STOP`，文字完整無截斷。
- `python -m pytest -q`：95 個測試全數通過。
- `python scripts/e2e_test.py --full`：4 個 Search 情境（共 12 筆店家）+ 2 個 Info
  情境，人工檢視介紹文皆為完整、自然收尾的句子，無截斷現象
  （review/review_20260628_1156.md，PASSED）。

## 2026-06-28 — 介紹文改為列點格式並縮短字數限制；Flex 描述文字樣式調整

### 修正
- 使用者回報：上次調整後的 `info_skill` 介紹文（150~200 字）仍嫌太長，要求改為精簡列點式
  內容；並要求所有 Skill 輸出的描述細節文字（Flex Message 推薦文案/介紹文）改為正體
  （非斜體）、字級縮小、顏色改為深灰色，以提升行動裝置可讀性。
- `core/prompts.py`：`INFO_SUMMARY_PROMPT` 移除固定「150~200 字」目標，改為新增「輸出
  字數與格式限制」區塊：禁止輸出大段落連續文字、強烈建議以 Bullet Points 呈現精簡子句、
  總長控制在 150 字以內。
- `core/flex_handler.py`：`BASE_BUBBLE_STRUCTURE` 推薦文案文字節點移除 `style: italic`
  （改為正體），顏色由 `#666666` 改為深灰色 `#333333`，字級維持 `sm`。此節點為
  `get_flex_bubble()` 共用結構，Search Skill 輪播與 Info Skill 單一 Bubble 的描述文字
  皆同步套用新樣式。

### 驗證
- `python -m pytest -q`：95 個測試全數通過。
- 確認程式碼中已無其他位置使用 `italic` 樣式（review/review_20260628_1621.md，PASSED）。

## 2026-06-28 — 修正介紹文偶發超過 150 字與 Markdown 粗體殘留

### 修正
- 使用者手動於 `INFO_SUMMARY_PROMPT` 加入「列點最多四點」限制後，要求以真實 API 實測驗證。
  對 3 間 `description` 長度差異大的店家測試後發現兩個問題：
  1. 描述越長時，模型雖守住四點上限，但每點塞入更多內容，導致總字數超過 150 字
     （「隣 Tonari" 一例達 222 字）。
  2. 模型偶發輸出 `**粗體**` 等 Markdown 強調符號，但 LINE Flex Message 的 `text` 元件
     不支援 Markdown，會直接顯示星號字元。
- `core/prompts.py`：`INFO_SUMMARY_PROMPT` 修正兩處：(1) 輸出規則明確列出禁止的具體
  Markdown 符號（` ``` `、`**粗體**`、`*斜體*`、`#` 標題），不再只是泛稱「不要 Markdown」；
  (2) 「150 字以內」改為「所有列點加總的硬性上限」，並要求超過時優先縮短文字或減少
  列點數量，而非固守四點。

### 驗證
- 以真實 Gemini API（`gemini-2.5-flash`，`thinking_budget=0`）重新測試同 3 間店家：
  豚人本店 96 字、鬼金棒中山店 109 字（首次呼叫遇 Gemini 503 高負載暫時性錯誤，重試後成功）、
  隣 Tonari 由修正前的 222 字降至 124 字，3 筆皆在 150 字內且無 Markdown 符號殘留。
- `python -m pytest -q`：95 個測試全數通過（review/review_20260628_1808.md，PASSED）。

## 2026-06-28 — Webhook 事件特徵擴充 Phase 1：訊息去重、時間感知、群組分流

### 新增
- `core/message_dedup.py`：新增 `is_duplicate_message(message_id)`，本地模式以記憶體 `set`
  記錄已處理過的 LINE `message.id`，生產模式寫入 Firestore `processed_message_ids`
  collection（雙路徑判斷沿用既有 `DATA_BACKEND` 慣例）。`app.py` 的 `handle_message`
  在啟動背景執行緒前先檢查，重複請求（LINE webhook 因網路延遲重送）直接跳過，避免
  Gemini 被重複觸發計費。
- `core/agent_router.py`：`dispatch()` / `_dispatch_inner()` 新增 `current_time` 參數，
  有提供時會以 `[目前時間：...]` 前綴注入 STEP 1 意圖解析的 `contents`，供 Gemini 判斷
  「現在」、「今天」等相對時間用語。`app.py` 的 `handle_message` 將 `event.timestamp`
  （毫秒時戳）轉換為台北時間字串後傳入。
- `app.py`：`handle_message` 最前面新增來源過濾，非一對一私聊（`event.source.type != "user"`，
  即群組/多人聊天室）直接 return、不呼叫 Gemini。範圍經使用者確認：目前不需要 @ 標記偵測，
  群組訊息一律忽略。

### 已知限制 → ✅ 已解除
- ~~時間感知目前僅注入意圖解析步驟；`ramen_data.json` 尚無營業時間欄位，
  「現在有開的店」類查詢仍無法實作。~~
  已於 2026-07-04 補齊（見下方該日條目）：`opening_hours` 資料回填完成
  （179 筆中 168 筆有實際時段），`is_open_at()` + `open_now` 過濾上線，
  「現在有開的店」查詢已可運作。

### 驗證
- 新增 `tests/test_message_dedup.py`（3 個測試：首次出現、重複偵測、不同 id 互不影響），
  全套測試由 95 個增至 98 個，全數通過。
- 以真實 Gemini API 呼叫 `dispatch(user_text, current_time=...)`，確認注入時間背景後
  仍能正確解析意圖 JSON（`SEARCH_BY_CRITERIA` / `CAROUSEL`），未破壞既有輸出格式。
- 執行 `gcloud firestore fields ttls update received_at --collection-group=processed_message_ids
  --database=ramendata --enable-ttl --expiration-offset=86400s` 設定 Firestore TTL policy
  （24 小時後自動清除），避免 `processed_message_ids` collection 無限累積。首次執行時
  單獨帶 `--enable-ttl` 報錯（`Exactly one of (--disable-ttl | [--enable-ttl :
  --expiration-offset]) must be specified`），需與 `--expiration-offset` 一起指定，
  重跑後確認 `state: ACTIVE`。

## 2026-06-29 — 修正意圖解析失敗時錯誤 fallback 成無條件搜尋的問題

### 修正
- 使用者在正式環境實測「現在這個時間有適合吃的拉麵嗎」時，意圖推薦出與台北完全無關的
  大阪店家。追查 Cloud Run log 發現根因：該次 Gemini 呼叫剛好遇到 503（暫時性高負載
  錯誤），`core/agent_router.py` 的 `_dispatch_inner()` 在 STEP 1 例外時原本會 fallback
  成 `{"intent": "SEARCH_BY_CRITERIA", "ui_tag": "TEXT"}`（無 `location`/`style`），而
  `skills/Search_skill.py` 的 `filter_ramen_data()` 將「無地區條件」視為「符合全部
  店家」，導致從全部 180 間店（含使用者個人去大阪旅遊時記錄的 NEXT SHIKAKU）隨機抽選，
  使用者完全無法察覺背後其實是 API 暫時失敗。與本次 Webhook 時間感知功能本身無關，是
  巧合在測試時撞上既有的容錯邏輯缺口。
- `core/agent_router.py`：STEP 1 的例外處理改為直接回傳 `self._fallback_result("系統忙碌中，
  請稍後再試。")`，不再偽裝成合法的無條件搜尋繼續往下執行 STEP 2/3。

### 驗證
- 更新 `tests/test_pipeline.py` 的 `TestDispatchFallback`：原測試斷言「Gemini 回傳非 JSON
  時 fallback 為 SEARCH_BY_CRITERIA」已改為斷言回傳 `FALLBACK`；新增
  `test_gemini_api_error_returns_fallback`，模擬 `generate_content` 直接拋出例外
  （對應真實的 503 情境，非僅 JSON 格式錯誤）。全套測試由 98 增至 99 個，全數通過。

---

## 2026-07-04 — Webhook 階段二：LocationMessage 定位推薦 + 店家營業時間

### 新增
- **LocationMessage 定位推薦**：`app.py` 新增 `@handler.add(MessageEvent, message=LocationMessage)`
  事件處理與 `_reply_location()`，使用者直接分享 LINE 位置（GPS pin）時，繞過 Gemini 意圖解析，
  直接對店家快取跑 Haversine 找最近 ≤3 間並生成推薦文 Carousel。沿用私聊分流與 message.id 去重。
- `skills/Search_skill.py` `filter_by_location(lat, lng, radius_km=5.0, style)`：不經 Geocoding、
  不隨機抽選，依距離排序取最近店家；回傳 `(results, nearest_km)`，nearest_km 供半徑內找不到時
  回覆使用者「最近一間在 X 公里外」。
- **店家營業時間 + 「現在有開」查詢**：`services/google_maps.py` `get_opening_hours_by_place_id()`
  取 Places `regularOpeningHours`；`scripts/update_api_data.py` 新增 `--update-hours` 批次補全模式
  （每店 1 次 API，179 筆分 2 天）。
- `skills/Search_skill.py` `is_open_at(opening_hours, dt)`：依 Places `periods`（day 0=週日）判斷
  營業狀態，正確處理跨午夜與 24 小時店。
- `core/prompts.py` 意圖解析新增 `radius_km`（使用者指定範圍）與 `open_now`（找營業中店家）欄位。

### 修改
- `filter_ramen_data()` 新增 `current_time` 參數並接收 `radius_km` / `open_now`：`radius_km` 覆寫
  精確點查詢的固定 2km 半徑（行政區字串比對不變）；`open_now` 為真時以 `is_open_at` 過濾，
  無營業時間資料的店家一律排除（寧缺勿錯）。`core/agent_router.py` 把既有 `current_time` 往下傳。

### 驗證
- `pytest` 118 個測試全數通過（新增 19 個：is_open_at 7 / filter_by_location 7 / radius 2 / open_now 3）。
- 真實資料：中山站 2km 回傳 3 間排序正確、偏遠處回傳空但正確回報 nearest_km。
- 真實 Gemini API：新欄位正確解析，一般查詢無回歸。
- 待後續營運：分 2 天執行 `--update-hours` 回填營業時間並 sync Firestore；LocationMessage 待 LINE 實機測試。

---

## 2026-07-07 — 自動化優化管線階段一+二：雲端對話埋點與地端同步

### 新增
- **對話特徵埋點**：`core/conversation_logger.py` 新增 `log_conversation(user_input, intent, intent_data)`，
  於 `DATA_BACKEND=firestore`（雲端）時以 fire-and-forget daemon thread 非同步寫入 Firestore
  `conversation_logs` collection；本地終端模式 no-op（無真實使用者）。紀錄 schema
  `{timestamp（台北時間）, user_input, predicted_skill, args}`，`predicted_skill` 由 intent 映射
  為可讀 skill 名（`SEARCH_BY_CRITERIA→Search_skill` 等），`args` 為意圖字典去除 `intent`/`ui_tag`。
- **地端同步腳本**：`scripts/fetch_cloud_data.py` 一鍵把雲端數據覆寫回 `data_logs/`：
  `feedback_reports` → `tracking_feedbacks.json`（JSON Array，映射為 `{timestamp, user_id, feedback_text}`）、
  `conversation_logs` → `tracking_conversations.jsonl`（每行一筆 JSON），供 Claude Code 分析分類錯誤、
  盤點資料盲區。

### 修改
- `core/agent_router.py`：`_dispatch_inner` 成功解析意圖並執行完 STEP 3 後、回傳前呼叫
  `log_conversation`（STEP 1 例外的 FALLBACK 路徑不埋點，因意圖未成立）。
- `.gitignore` / `.dockerignore`：排除 `data_logs/`（含 user_id 與對話內容的隱私日誌，不進版控 / 不打包進容器）。

### 設計決策
- 沿用既有 `feedback_reports` collection，不新開計畫初稿提到的 `feedbacks`：feedback skill 已在
  prod 寫入該集合、`check_pending_reports` 依賴它，改名會遺失既有待處理回報且無實益。

### 驗證
- `python -m pytest -q`：118 個測試全數通過（埋點在預設 local 模式為 no-op，不影響既有測試）。
- mock Firestore（`DATA_BACKEND=firestore`）驗證埋點 record 與示範檔 schema 完全一致
  （collection=`conversation_logs`，正確排除 `intent`/`ui_tag`）。
- 待真實環境：對真實 Firestore 執行 `fetch_cloud_data.py`；部署後於 LINE 實際對話確認
  `conversation_logs` 正確累積（review/review_20260707_2242.md）。


---

## 2026-07-09 ~ 2026-07-30 — 成本、延遲、可觀測性與資料飛輪四線優化

> 本段補寫先前留白的期間。H1~H4 高風險漏洞、M1~M5 中風險、L1~L7 低風險清理、
> AI 摘要快取、效能計時 KPI 的逐項細節已完整記錄於 `PENDING.md`，此處不重複，
> 僅補上該檔未涵蓋的四項。

### 階段定位（2026-07-24 盤點）
專案已過 MVP、進入「可維運的產品」階段：雙後端、CI/CD、KPI 計時、快取、回報佇列、
對話埋點皆齊備。此後的重點不是加功能，而是三件事——**壓成本、降延遲、把資料飛輪轉起來**。
以下四項即依此排序執行。

### 新增
- **結構化日誌 `core/obs.py`（PR #14）**：新增 `emit_metric`，輸出單行 JSON（保留既有
  彩色 print 不變）。`app.py` 於 webhook `finally` 輸出 `request` / `location_request`
  事件（含 `intent`、`total_s`），供 Cloud Logging 建立 FALLBACK 率 / 延遲告警。
  告警規則本身屬 GCP ops，待需要時於 Cloud Console 建立。
- **資料飛輪分析工具 `scripts/analyze_conversations.py`（2026-07-30）**：本地工具
  （`scripts/` 不進版控），讀 `data_logs/` 產出三份輸出：
  1. 意圖（skill）分布 + 熱門查詢地區 / 店名——分類錯誤挖掘的起點
  2. 資料盲區盤點：列出「查詢有、但實際查不到店家」的地區 / 店名，指導人工補店
     （地區判定於 2026-08-21 改為直接呼叫搜尋主路徑 `filter_ramen_data()`；
     原本的 `location` 字串比對會把站名查詢全部誤判為盲區）
  3. 待處理回報數：讀 `tracking_feedbacks.json` 提醒 feedback 修正別漏

  用法：`PYTHONUTF8=1 python scripts/analyze_conversations.py [--top N]`
  （需先跑 `scripts/fetch_cloud_data.py` 同步）。持續維運（每週執行、據以補店 / 調 prompt）
  仍屬人工流程。

### 修改
- **Geocoding 快取層級化（2026-07-30，PR #13）**：`_get_latlng_cached` 改為三層快取
  （記憶體 → Firestore `geocode_cache` → API）。成功結果下沉 Firestore，可跨 worker /
  重啟共用；失敗結果不持久化。`pytest` 137 passed + 真實 Firestore 端到端驗證。
- **圖片存活檢查加快取（2026-07-30，PR #14）**：`_is_image_url_alive` 新增短期存活快取
  （TTL 300s、上限 2000 筆），TTL 內同一圖片略過 HEAD request，省下使用者等待路徑上的
  往返。只快取「存活」結果；失效者照舊走背景修復，不因快取而延誤。

---

## 2026-08-01 — 找出 LINE 回覆延遲的真正根因：Cloud Run CPU throttling

### 修正
- **正式環境 Cloud Run 服務改為 CPU 常駐配置**（`--no-cpu-throttling`）。這是長期
  「回覆延遲數分鐘」問題的真正根因，先前 PENDING.md 待處理BUG #1 所做的三項優化
  （Firestore 心跳、`f.result()` timeout、info_skill 快取）雖各自正確，但均未觸及此點，
  故延遲反覆復發。
- `.github/workflows/deploy.yml`：Deploy 步驟加上 `--no-cpu-throttling` 並註明原因，
  避免日後 CI 部署遺失此設定。

### 根因說明
`src/app.py` 的 `handle_message` 為滿足 LINE webhook 1 秒限制，收到訊息後立即回 200，
AI pipeline 全部交給背景 daemon thread。而 Cloud Run **預設只在「處理請求期間」配置 CPU**，
請求既已結束，該背景執行緒即在近乎 0 的 CPU 下執行，所有網路 I/O（gRPC / TLS /
protobuf 反序列化）連帶慢 30~400 倍，TLS 交握甚至被凍到對端斷線（`SSL: UNEXPECTED_EOF`，
導致圖片自我修復路徑長期失效）。`min-instances=1` 只保證實例不被回收，不等同配置 CPU。

決定性證據：同一段程式、同一批 180 筆資料的 Firestore 讀取，在「有請求在飛」時為 0.3s，
在背景執行緒中則為 94.3s / 112.5s / 133.6s / 142.1s。

### 驗證
- 新舊 revision 啟動預熱對照：Firestore 讀取 180 筆 142.1s → **0.5s**；完整啟動序列
  （Firestore + Gemini + Maps + LINE + Client Pool 全部預熱）12 秒完成。
- LINE 實機複測「民權西路推薦的拉麵店有什麼」：端到端 **248.75s → 2.73s**（91 倍）。
  拆解：意圖解析 25.2s→2.06s、篩選 145.9s→0.0s、push_message 53s→0.3s。
- 附帶恢復：過期圖片的背景換網址（`_refresh_shop_image`）先前每次都因 SSL EOF 失敗，
  現已能正常寫回新網址。
- 詳見 `review/review_20260801_1423.md`。

### 成本影響
CPU 常駐改以實例生命週期計費，`min-instances=1`、1 vCPU / 1GiB 常駐，粗估每月增加
約 US$40~50。已向使用者澄清 Google Maps Platform 的每月 US$200 抵免額為 Maps SKU
專款專用、不可支付 Cloud Run / Firestore，使用者確認額度充足後同意採行。

> 註：2026-07-09 ~ 2026-07-30 期間的項目已於上一段補寫；其中 AI 摘要快取、效能計時 KPI、
> 高風險漏洞修補 H1~H4 的逐項細節記於 `PENDING.md`。

---

## 2026-08-02 — 修復店家照片 93% 顯示預設圖，並建立網址壽命觀測機制

### 修正
- **照片機制重做**。使用者回報 LINE 上多數店家顯示預設圖，逐筆 HEAD 檢查確認
  正式環境 180 筆中 **168 筆已失效（93.3%）**、本地 179 筆中 160 筆失效。
- **離線批次補齊**：`scripts/update_api_data.py --update-photos` 一次補全 155 筆
  （成功 155／失敗 0），同步 Firestore 177 筆異動，存活率 **6.7% → 99.4%**。

### 根因
`services/google_maps.py` 的 `get_photo_url()` 以 `skipHttpRedirect=true` 取回的
`photoUri` 是**有時效的簽章網址**，卻被當成永久網址寫入 `image_url` 持久化，
因此每一筆遲早都會 403。既有的「HEAD 檢查 + 背景換新網址」補救無法收斂——換回來的
仍是另一個會過期的簽章網址；且該背景修復在 2026-08-01 修好 CPU throttling 之前
每次都因 `SSL: UNEXPECTED_EOF` 失敗，從未真正運作過。

### 新增
- **`image_url_renew_date` 欄位**：記錄網址取得時刻（UTC ISO 8601 帶時區），
  供觀測簽章網址的實際存活時間。Google 未公開此壽命，且實測顯示**不是固定 TTL**
  （勝王 34.7h 已死，但同時段寫入的滿雞軒／誠屋拉麵 34.5h 仍存活，更早 3.7 天的
  烹星／隱家拉麵也存活），故需累積分布才能決定刷新策略。
- **`scripts/check_image_url_lifetime.py`**：壽命觀測工具，輸出屆齡分組存活率與
  壽命上下界，並在「最短命的已死樣本比最長壽的存活樣本更年輕」時標示壽命非固定 TTL。
  只讀不寫、不消耗 Maps 配額。
- **`Search_skill.persist_shop_fields()`**：多欄位單次寫回，確保 `image_url` 與
  `image_url_renew_date` 同進退不會不一致；`persist_shop_summary()` 改為委派之。

### 修改
- `resolve_shop_images()` 改回傳 `(可顯示清單, 待更新清單)`，**檢查階段完全不呼叫
  Google API**（已加測試強制保證）；新增 `refresh_shop_images_async()` 供
  `push_message` 送出**之後**才觸發更新，不與回覆搶資源。
- `get_photo_url()` 增加 `maxWidthPx=1000`：實測發現橫幅照片寬度可達 1067px，
  超過 LINE Flex Message 的 1024px 上限而無法顯示。
- `get_photo_name_by_place_id()` 自 `get_photo_by_place_id()` 拆出。

### 設計決策
- 過程中曾實作 `/photo/<place_id>` 代理端點（不儲存網址、每次載入時即時取得），
  並發現 **LINE 客戶端載入 Flex hero 圖時不跟隨 302 轉址**（收到 302 即停止，
  連轉址至預設圖也無效），改為直接回傳位元組後端點自測正常。
- 但使用者評估後**決定不採用代理方案**，改為先蒐集簽章網址的真實壽命數據再決定
  策略。故最終回到「儲存網址 + 回覆前檢查 + 回覆後更新」並加上觀測欄位，
  代理相關程式碼（`core/photo_service.py`、`/photo` 端點、`PHOTO_PROXY_BASE`）已移除。

### 驗證
- `pytest -q`：145 passed。
- 真實 API 端到端（Strike軒，原網址 403）：判定過期 → 本次用預設圖 → 回覆後更新
  → `image_url_renew_date` 寫入 → 新網址存活。
- LINE 實機（「西門推薦的拉麵店有什麼」）：**照片正常顯示**，端到端 2.03s，
  日誌無任何「圖片網址已過期」紀錄。
- 詳見 `review/review_20260802_0035.md`、`review/review_20260803_0103.md`。

### 後續
`image_url_renew_date` 自此累積，需定期執行觀測腳本取得壽命分布，
再決定是否導入定期全量刷新（全量一輪約 180 次呼叫，而每日上限為 100 次）。

---

## 2026-08-18 — Flex Bubble 顯示今日營業時段

### 新增
- `core/flex_handler.py` `_format_opening_hours()`：直接由 Places API 原生 `periods`
  產出中文顯示文字，插在 Bubble 分隔線之前（地址之後）。不使用英文的 `weekday_text`，
  故**不需額外呼叫 API**。支援多時段（`11:00-14:00、17:00-22:00`）、跨午夜、
  24 小時營業與今日公休。
- 與 2026-07-04 的 `open_now` 過濾**職責分離**：那個負責「篩出現在有開的店」，
  這個只負責「把今日時段顯示出來」，不判斷當下是否營業中。店家下午有無休息，
  自然反映為一段或多段。

### 設計決策
- 插入時機刻意排在既有索引配對完成「之後」，避免影響 `get_flex_bubble()` 中
  「有無 rating」造成的索引位移邏輯。

### 驗證
- 新增 13 個單元測試（多時段／跨午夜／今日公休／24 小時／與 rating 併存），`pytest` 160 passed。
- LINE 實機確認營業時間正常顯示（2026-08-22）。註：實機所見為一般時段，
  跨午夜與今日公休兩個分支目前僅單元測試覆蓋。

---

## 2026-08-28 — LINE SDK v2 相容層遷移至 v3 原生 API

### 修改
- `src/app.py`（全專案唯一引用 linebot 之處，92 增 38 刪）由 `linebot.*` 相容命名空間
  改為 `linebot.v3.*` 原生 API。相容層已 deprecated，未來大版本將移除；
  `requirements.txt` **未變動**（v2 相容層與 v3 原生 API 同屬 `line-bot-sdk==3.22.0`）。

| v2 | v3 |
|----|----|
| `LineBotApi(token)` | `Configuration` + `ApiClient` + `MessagingApi` |
| `push_message(uid, m)` | `push_message(PushMessageRequest(to=uid, messages=[m]))` |
| `TextSendMessage` | `TextMessage` |
| `FlexSendMessage(contents=dict)` | `FlexMessage(contents=FlexContainer.from_dict(dict))` |
| `QuickReplyButton` | `QuickReplyItem` |
| 收訊 `TextMessage` / `LocationMessage` | `TextMessageContent` / `LocationMessageContent` |

### 三個刻意處理的風險點
1. **命名反轉（無聲錯誤）**：v3 的 `TextMessage` / `LocationMessage` 是「**發訊**」型別，
   與 v2 的「收訊」語意相反且同名。誤用**不會 import 失敗**，只會讓 handler 收不到訊息。
   已於 import 區加註說明，收訊一律改用 `webhooks.*Content`。
2. **連線預熱不可失效**：v3 官方範例慣例為 per-request `with ApiClient(...)`，
   照抄會讓 `app.py` 的 LINE 連線預熱失效，使第一次 push 的冷連線成本
   （2026-08-01 實測約 22 秒）回歸。改採**模組層級長生命週期 `ApiClient`**，
   並於初始化處註明原因。
3. **測試零覆蓋**：`tests/` 未引用 linebot，160 個測試對此段程式**沒有任何保護力**。
   另以匯入煙霧測試與真實資料建構測試補強，並認知 LINE 實機是唯一的驗證網。

### 驗證
- 匯入煙霧測試：型別為 `MessagingApi` / v3 `WebhookHandler`，handler 註冊為
  `MessageEvent_TextMessageContent` / `MessageEvent_LocationMessageContent`。
- 以真實店家資料建構 Carousel／單一 Bubble／`TextMessage`+QuickReply，
  `FlexContainer.from_dict` 與 `PushMessageRequest` 序列化皆通過。
- `pytest` 160 passed；`scripts/e2e_test.py --full` 全情境通過。
- **正式環境（revision `ramen-bot-00077-f49`）**：log 出現
  `[STARTUP] LINE API 連線預熱完成`；LINE 實機驗證文字回覆、Carousel、單一 Bubble、
  定位推薦 + 分享位置 QuickReply 四項皆正常。
- **冷啟延遲未退化**：`push_message` 實測 **v3 0.2s vs v2 0.2s**（對照舊 revision
  `ramen-bot-00076-5ff`）；部署後第一則訊息端到端 7.57s，其中 LLM 佔 6.48s，
  非 LLM 開銷約 1.1s，未出現 20 秒級冷連線成本。

### 過程中的教訓
遷移仍在分支上未合併時，曾有一次「LINE 實機測試通過」的誤判——當時 `origin/main`
停在 `9d84f7e`、服務中的 revision 仍是遷移前的 `ramen-bot-00076-5ff`，且 `deploy.yml`
只在 push 到 main 時觸發、無 `workflow_dispatch`，故測到的其實是 v2 舊版。
**在測試網不覆蓋的區域，「測的是哪個 revision」必須先確認過，驗證才算數。**

### 回退資產（已實測）
tag `pre-line-sdk-v3`（= `9d84f7e`，本地與 origin 皆有）；revision `ramen-bot-00076-5ff`
為 Ready，其映像檔 `sha256:70c6fc4a…` 仍在 Artifact Registry，回退後即使冷啟擴容也可拉起。
兩條回退路徑，依可靠度排列：

**路徑 A — 由 git 重建（最可靠，不依賴任何雲端保留機制）**
```
git revert -m 1 7d5da8a && git push origin main     # 或 git checkout pre-line-sdk-v3 取回 v2 的 app.py
```
CI/CD 會重新 build 並部署。`pre-line-sdk-v3` 同時鎖住了當時的 `requirements.txt`
（`line-bot-sdk==3.22.0`），故重建結果可重現。**只要 GitHub repo 還在，這條路永遠有效。**

**路徑 B — 切回舊 revision（最快，數秒生效，但有前提）**
```
gcloud run services update-traffic ramen-bot --region=asia-east1   --to-revisions=ramen-bot-00076-5ff=100
```
恢復追蹤最新版用 `--to-latest`。
前提是該 revision 與其映像檔（`sha256:70c6fc4a…`）仍存在——2026-08-28 查核時
Artifact Registry **無清理政策**，映像檔不會被自動刪除；但日後若有人加上清理政策，
或該 revision 被刪除，此路徑即失效，改走路徑 A。

> 註：本段刻意寫在版控檔案內。`PENDING.md` 與 `CR/` 均在 `.gitignore`，
> 只存在於當時那台機器上，不能作為回退資訊的唯一來源。


## 2026-08-30 — 依真實對話日誌修補意圖分類與回覆品質

> 起點是「盤點 PENDING 未完成事項並排序」。盤點過程中同步雲端日誌（24 → 38 筆），
> 新增的 14 筆直接提供了兩項待辦的正式環境證據，本次據以修補。
> 詳見 `review/review_20260830_1034.md`。

### 修正
- **意圖誤判：店名被當成流派**。日誌完整重現序列（2026-08-28）：
  「札幌拉麵的特色是什麼」✅ →「那喜多方拉麵呢」✅ →**「告訴我麵魚的特色」❌ 判為
  KNOWLEDGE_QUERY** →使用者被迫改寫成「我說的是拉麵店麵魚」才拿到正確結果。
  根因是 `IDENTIFY_INSTRUCTION_PROMPT` **完全沒有 few-shot**，四個 intent 各只有一行
  描述；而 `dispatch()` 本就不帶對話歷史，故**並非上下文污染**，是模型抓「X 的特色」
  這個表層句型。已加入「流派 vs 店名」判別規則與 5 組 few-shot。
  > A/B 實測（各 3 次）：舊 prompt **3 次中 1 次**誤判、新 prompt 3/3 正確。
  > 舊版是 **33% 隨機誤判**而非必然失敗——這正好解釋為何正式環境只偶發一次。
  > 新版價值在於收斂隨機性，小樣本不足以宣稱 0% 誤判率。

- **無有效搜尋條件時推薦出去不了的店**。「我想吃拉麵」解析後 `location`/`style` 皆空，
  `filter_ramen_data()` 視為「符合全部 179 間」，而資料庫含 **22 筆日本店**
  （大阪 9、福岡 5、東京 5、熊本 2、奈良 1），每個推薦位置約 **12% 機率**推出日本店家。
  與 2026-06-29 的大阪誤推薦是同一失效模式，但走的是**意圖解析成功**的路徑，
  當初的修法（解析失敗才回 FALLBACK）並未涵蓋。兩段收斂：
  - 地區與口味皆空 → 擴充既有 LOCATION_REQUEST 分支條件，導向位置分享流程
  - 有口味無地區 → 新增 `_OVERSEAS_PATTERN`，`location` 為空時排除海外店家
  > 實測 200 輪 × 3 筆：海外店家出現 0 次；明確查詢「大阪市」仍回傳 3 間（未誤傷）。

- **多地名查詢解析出不可用的 location**。「南港或東湖有沒有推薦的拉麵店」（2026-08-27）
  被解析為整串 `"南港或東湖"`，Geocoding 與字串比對都不可能命中，使用者隨即自行
  拆成兩句重問。prompt 加上「location 必須是單一地名」規則止血
  （A/B：舊 3/3 不可用 → 新 3/3 收斂為「南港」）。
  ⚠️ **這是止血不是功能**——使用者仍拿不到東湖的結果，正式的 fan-out + 合併未實作。

### 新增
- **功能說明攔截**：「怎麼使用」曾被判為 KNOWLEDGE_QUERY 而進入拉麵 RAG、答非所問。
  新增 `_HELP_PATTERN`，沿用既有 `_NEAR_ME_PATTERN` 的作法在 STEP 1 後攔截，
  回固定功能清單。**未新增第五種 intent**——單一情境不值得擴充 schema。
  雙重守門（長度 ≤15 字 **且**「整句就是用法詞 或 主詞指向本機器人」）避免誤攔
  「替玉怎麼用比較好吃」「食券機怎麼用」這類拉麵知識問題。
  ⚠️ 守門刻意採**正面表列主詞**而非排除領域名詞——「X 怎麼用」的 X 是開放式清單
  （食券機／替玉／海苔／餐券…），漏列一個就會讓原本能被知識庫答出來的問題退步。
  埋點寫入 `predicted_skill="HELP"`，累積真實頻率作為日後是否獨立 intent 的依據。
- **回覆前置引導文**：Search Carousel／Info Bubble／定位推薦三處，於 Flex 前加一句
  說明。**併入同一個 `push_message` 的 messages 陣列**（LINE 單次 push 可帶 5 則），
  不增加 push 次數、不影響端到端 KPI。
- **Knowledge 回覆版型**：`KNOWLEDGE_ANSWER_PROMPT` 原本只限字數與語氣、無固定結構。
  改為「一句總起（25 字內）＋空行＋2～4 點『・重點詞：說明』」，總長上限 200 字，
  並沿用 `INFO_SUMMARY_PROMPT` 已驗證有效的作法明列禁用 Markdown 符號。
- 測試 +6：`TestDispatchNoSearchConditions`、`TestDispatchHelpQuery`、海外排除與
  「明確查詢不誤傷」各 2。

### 一併盤出
- **首個真實資料盲區：南港**（2 次查詢、0 結果）。`ramen_data.json` 179 筆中無任何
  南港店家，而 `README.md` 的範例輸入正是「南港有什麼推薦的豚骨拉麵」——範例已改為
  資料庫查得到的「大安區有什麼推薦的豚骨拉麵」，補店列入待辦。
- PENDING.md 有 4 個主 app 待辦被誤掛在 `[SLM] 意圖路由器自訓練` 段落下，已移出
  另立 `[APP] 對話品質待辦`。

### LINE 實機才發現的問題
知識問答的回答開頭會出現「**知識庫指出**」，把內部實作洩漏給使用者。
根因是為了修審查意見而加的「說明知識庫涵蓋範圍有限」一句，反而引導模型講出資料來源。
已於 `KNOWLEDGE_ANSWER_PROMPT` 加明確禁令（禁「知識庫」「資料庫」「根據檢索」
「資料顯示」等字眼），知識不足時改為「把確定的部分講完，一句話帶過尚無更細節資訊」。

> 單元測試無法驗證 LLM 實際輸出，這類問題只有實機看得出來、代價高。
> 故新增 `TestKnowledgePromptGuards` 退而守住「規則本身沒被誤刪」。

### 驗證
`pytest` **173 passed**（起始 160，+13）／`e2e_test.py --full` 全部自動檢查通過／
真實 Gemini 意圖回歸 9/9／獨立 Reviewer 三輪後 **PASSED**。

**LINE 實機驗證通過**（ngrok + 本機 `LINE_TAG=1`，6 題全過，端到端 2.1～8.7s）。
⚠️ 測前已確認正式 revision 為 `ramen-bot-00079-2rz`、流量走 ngrok 進本機，
確保測的是新版而非正式環境舊版（PENDING L36-40 教訓的直接應用）。

### 上線
commit `7d2bd43`，merge `d951dcd`，CI/CD run `33290802925` success。
正式環境 revision **`ramen-bot-00079-2rz` → `ramen-bot-00080-r2v`**，
啟動日誌六項預熱全部完成（含 Firestore gRPC 心跳，證實 `DATA_BACKEND=firestore` 生效）。

回退：`git revert -m 1 d951dcd && git push origin main`（CI 重新 build），
或 `gcloud run services update-traffic ramen-bot --region=asia-east1 --to-revisions=ramen-bot-00079-2rz=100`（數秒生效）。


## 2026-08-30（下午）— 修正「南港」查無結果與連續查詢重推同三間

### 修正
- **「南港」查無結果，根因不是資料缺口而是搜尋 bug**。原先被 `analyze_conversations.py`
  盤為「資料盲區」，實際上 `ramen_data.json` 有 1 間南港店家（鐵丸十三堂）：
  | 查詢 | 路徑 | 結果 |
  |------|------|------|
  | 南港區 | 行政區 → 字串比對 | ✅ 1 間 |
  | 南港站 / 昆陽 | Geocoding → 2km | ✅ 1 間 |
  | **南港** | Geocoding → 2km | ❌ **0 間** |

  Geocoding 對光禿禿的行政區名回傳**行政區幾何中心**(25.0312, 121.6112)，
  離店家 **2.67km**，超出 2km 預設半徑。與 search_skill BUG #8（中山區）同一失效模式，
  但既有的 `is_district_query` 只認「區/市/縣」結尾，未帶「區」字的地名會漏掉。

  修法：`filter_ramen_data` 的主迴圈抽為巢狀函式 `_collect(coords)`，
  **座標查詢回 0 筆時退回字串比對再跑一次**。只在原本就要回空結果時觸發——
  實測中山站／雙連站／西門／忠孝復興／民權西路／中山區／大安區／小巨蛋／東湖／西湖
  十個既有查詢結果完全不變、皆未觸發 fallback。
  > 考慮過但沒採用：維護一份行政區名清單。那是開放式清單，且台北以外的縣市會漏。

### 新增
- **避免連續查詢重推同三間**（2026-08-11 日誌「還有其他的台北拉麵嗎？」）。
  新增 `src/core/recent_shops.py`，沿用 `core/message_dedup.py` 的 OrderedDict +
  上限淘汰寫法；`user_id` 從 `app.py` → `dispatch()` → `filter_ramen_data()` 穿透三層。
  在「去重之後、抽選之前」排除近期已看過的店家。
  實測同使用者連查「台北」三次，相鄰兩次結果重疊 0 間。

  **識別鍵用 `place_id` 而非 `id`**（Reviewer 發現）：`ramen_data.json` 是**以料理為單位**
  而非以店家為單位，實測 33 組同 `place_id` 多筆變體、其中 11 組連座標都不同，
  且 `_dedupe_by_place_id` 在不同查詢路徑下可能留下不同變體。用 `id` 當鍵會讓
  同一家店以不同變體逃過排除。已對齊 `_dedupe_by_place_id`。

  設計取捨（皆寫入模組 docstring）：
  - **只做記憶體實作**，兩種 `DATA_BACKEND` 皆同。與訊息去重的需求不同——去重跨實例
    失效會導致 Gemini 重複計費、必須正確；重複推薦失效的代價僅是看到相同結果。
    生產 `maxScale=3` 跨實例讀不到屬可接受降級；改 Firestore 等於在搜尋熱路徑多一次
    讀寫，與同檔案為省延遲而做的 stale-while-revalidate 自相矛盾。
  - **只記最近一次、不累積**：累積會讓反覆查詢同一地區時可選店家越來越少。
  - **TTL 30 分鐘**：否則使用者隔天查同一地區會莫名被排除。
  - **排除後不足 3 間則放棄排除**：寧可重複，也不因排除而少給結果。
  - ⚠️ 尚未涵蓋 GPS 定位推薦路徑（`filter_by_location`）。

### 一併盤出（未修，已登錄 PENDING）
🔴 **「台/臺」未正規化**：`filter_ramen_data({"location": "台北市"})` → **0 筆**，
而 `臺北市` → 3 筆。因「市」結尾會短路 Geocoding 走字串比對，而店家 `location`
存的是「臺北市…」。本次的座標 fallback 幫不上（根本沒走座標路徑）。屬既有問題，
依「精準修改」原則另案處理。

### 驗證
`pytest` **192 passed**（前次 173，+19）／`e2e_test.py --full` 全部自動檢查通過／
新增行 0 行超過 88 字元／獨立 Reviewer **PASSED**（`review/review_20260830_1205.md`），
其 001（排除鍵）與 005（無效斷言）兩項建議已於 commit 前修正。

> Reviewer 對 `_collect` 重構做了機械化等價比對（去縮排＋壓空白後逐行比對），
> 而非目視——因為我在重構過程中踩過兩個坑：`s.index` 抓到 `filter_by_location`
> 的同名迴圈而刪掉 `def filter_ramen_data`；以及 `results.append(shop)` 縮排錯誤
> 被塞進 `if open_now` 區塊內（導致 15 個測試失敗）。兩次都由測試套件即時擋下。


## 2026-08-30（傍晚）— 「台/臺」異體字正規化

### 修正
使用者打「台北市」查不到任何店家（回 0 筆），打「臺北市」才有 3 筆。
店家 `location` 一律是「臺」（Places API 回寫的官方寫法，實測 179 筆中
臺北 116／臺中 10／臺南 2、零筆用「台」），使用者卻幾乎都打「台」。
行政區查詢走字串比對，兩種寫法互不為子字串，比對必然落空。

新增 `normalize_tai()`，於 `filter_ramen_data` 的兩處字串比對前對**查詢與店家兩側**
都做正規化。兩側都做是刻意的——雖然目前資料端已一致，但新店家經
`build_new_shops.py` 進來時可能帶「台」，不該依賴資料剛好一致。

**只處理「台北／台中／台南／台東／台灣」五個詞**（使用者指定範圍）。
「台」在舞台、電台、平台、台階、燈台、天台、台語、站台、後台等詞裡
**並不是「臺」的異體**，全域替換會製造出錯誤的字——已就這 9 個詞寫測試守住。

驗證：`台北市`／`台中市`／`台南市` 由 0 筆變為有結果；十個既有查詢
（中山站／雙連站／西門／忠孝復興／民權西路／中山區／大安區／小巨蛋／南港／東湖）
筆數完全不變。

### 一併發現、未修（已登錄 PENDING）
**完整行政區名查不到**：`「臺北市中山區」→ 0 筆`，而 `「中山區」→ 3 筆`。
已用 `git stash` 對照 HEAD 確認**是既有問題、非本次引入**。
根因：`clean_target_loc` 把查詢的「市/區/縣」去掉（臺北市中山區 → 臺北中山），
店家 `location` 卻保留（臺北市中山區），兩邊互不為子字串。
與本次的異體字問題無關，且修法會動到比對語意的範圍，依「精準修改」原則另案處理。

### 環境註記
`data/ramen_data.json` 會被 e2e 測試改動——`info_skill` 的 7 天 TTL 快取回寫會把
Places API 回傳值寫回本地（含把「台」正規化為「臺」）。屬既有設計行為，
`data/` 不進版控、正式環境走 Firestore，但統計本地資料時須注意數字會隨測試變動。

### 驗證
`pytest` **211 passed**（前次 192，+19）／`e2e_test.py --full` 全部自動檢查通過／
新增行 0 行超過 88 字元。
