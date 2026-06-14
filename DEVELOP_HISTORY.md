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
