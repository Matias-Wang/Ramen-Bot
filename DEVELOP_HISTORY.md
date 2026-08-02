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

### 已知限制
- 時間感知目前僅注入意圖解析步驟；`ramen_data.json` 尚無營業時間欄位，「現在有開的店」
  類查詢仍無法實作，需後續另外補資料工程。

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

> 註：本檔案 2026-07-09 ~ 2026-07-30 期間的項目（AI 摘要快取、效能計時 KPI、
> 高風險漏洞修補 H1~H4、Geocoding 快取下沉、A2/A4）尚未補寫，進度詳見 `PENDING.md`。
