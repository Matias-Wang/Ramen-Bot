# 拉麵店資料管線 (Ramen Data Pipeline)

自動化從 Instagram 公開貼文抓取食記、透過 Gemini LLM 批次過濾與結構化提取、再經 Google Maps Places API (New) 驗證店家狀態，最終輸出可供 Bot 使用的拉麵店 JSON 資料庫。

**入口點**：`scripts/data_pipeline.py`

---

## 架構概覽

```
STEP 0: 初始化（Gemini + Maps API）
  ↓
STEP 1: 載入 instagram_data.json + 補充 posts_1.json 標題
  ↓
STEP 2: 批次 LLM 過濾 + 結構化提取（每批 10 筆，含 Checkpoint）
  ↓
STEP 3: Places API (New) 驗證（取 place_id + 座標，過濾永久歇業）
  ↓
STEP 4: 寫入 data/ramen_data_YYYYMMDD.json
```

---

## 各階段說明

### STEP 0：初始化 (Initialization)

設定 Gemini API 與 Google Maps API Key，確認環境變數齊全後啟動 Pipeline。

**若 `GOOGLE_MAPS_API_KEY` 未設定**，STEP 3 自動跳過，不影響 STEP 2 輸出。

---

### STEP 1：載入與補充 IG 資料 (Load & Enrich)

從 `data/instagram_data.json` 讀取 IG 爬蟲彙整資料，並補充 `data/media/posts_1.json` 中的貼文標題（`title`）作為描述來源。

**資料來源**：
- `data/instagram_data.json`：IG 爬蟲主輸出（含 `id`、`image_url`、`description`）
- `data/media/posts_1.json`：原始貼文資料，用於補充 `description` 為空的項目

**輸出**：補充描述後的 IG 資料 list，供 STEP 2 使用。

---

### STEP 2：批次 LLM 過濾 + 結構化提取 (LLM Extraction)

將 IG 貼文以每批 10 筆送入 Gemini，判斷是否為拉麵食記並提取結構化欄位。

**提取欄位**：
| 欄位 | 說明 | 範例 |
|------|------|------|
| `is_ramen` | 是否為拉麵食記 | `true / false` |
| `name` | 店家名稱 | `辣麻味噌沾麵 鬼金棒` |
| `location` | 台北市行政區 | `台北市中山區` |
| `style` | 口味標籤（擇一）| `豚骨 / 雞白湯 / 醬油 / 味噌 / 煮干 / 魚介 / 鹽味 / 二郎系 / 家系 / 沾麵 / 其他 / 限定` |
| `price_range` | 價位範圍 | `250-350` |
| `rating` | 評分（1-5）| `4.2` |
| `features` | 特色標籤 list | `["超辣味噌湯頭", "角煮"]` |
| `description` | 100 字內精簡描述 | 中文敘述 |

**Checkpoint 機制**：
- 每次 STEP 2 完成後將結果寫入 `data/ramen_data_checkpoint.json`
- 再次執行時若 checkpoint 存在，直接載入跳過 LLM 處理，節省 API 費用
- 若需重新處理，手動刪除 checkpoint 檔案即可

**批次間隔**：每批處理後 `sleep(10)` 秒，避免觸發 Gemini API 速率限制。

---

### STEP 3：Places API (New) 驗證 (Geocoding & Validation)

對每筆提取出的拉麵店呼叫 Google Maps Places API (New) Text Search，驗證店家真實狀態並補充地理資料。

**Field Masking**（僅請求必要欄位）：
```
places.id, places.businessStatus, places.location, places.formattedAddress
```

**處理邏輯**：
1. 以 `{location} {name} 拉麵` 作為查詢文字
2. 取得 `place_id`、`businessStatus`、`coordinates`、`formattedAddress`
3. 若 `businessStatus == "CLOSED_PERMANENTLY"` → 跳過，不納入輸出
4. 其他狀態（含 `OPERATIONAL`）→ 更新欄位後納入

**批次間隔**：每筆處理後 `sleep(0.5)` 秒，控管 API 呼叫頻率。

---

### STEP 4：寫入輸出 (Output)

將驗證後的乾淨資料寫入 `data/ramen_data_YYYYMMDD.json`（含日期戳記）。

**後續手動步驟**：確認輸出正確後，將檔案重命名/複製至 `data/ramen_data.json` 供 Bot 使用。

---

## 輸出資料結構 (`ramen_data.json` 欄位)

```json
{
  "id": "貼文 media_id（IG 唯一識別碼）",
  "name": "店家名稱",
  "location": "台北市行政區",
  "address": "Google Maps 標準化地址",
  "coordinates": {
    "lat": 25.0493628,
    "lng": 121.5211399
  },
  "style": "口味標籤",
  "price_range": "250-350",
  "rating": 4.1,
  "user_ratings_total": 3129,
  "features": ["特色1", "特色2"],
  "description": "100字內中文描述",
  "image_url": "IG 原始圖片 URL",
  "map_url": "Google Maps 分享連結",
  "social_links": [
    {"label": "我的 IG", "url": "https://www.instagram.com/p/{shortcode}/"},
    {"label": null, "url": null},
    {"label": null, "url": null}
  ],
  "place_id": "ChIJ...",
  "last_updated": "2026-04-26T12:00:00.000000"
}
```

**欄位來源對照**：
| 欄位 | 來源 |
|------|------|
| `id`, `image_url`, `social_links[0]` | IG 爬蟲（STEP 1） |
| `name`, `location`, `style`, `features`, `description` | Gemini LLM（STEP 2） |
| `place_id`, `coordinates`, `address` | Places API New（STEP 3） |
| `rating`, `user_ratings_total`, `last_updated` | InfoSkill 執行時回寫 |

---

## 快取回寫策略（Bot 執行期間）

`data/ramen_data.json` 在 Bot 執行過程中也會被 `InfoSkill` 動態更新：

| 條件 | 處理方式 |
|------|----------|
| 無 `place_id` | 觸發 Places API Text Search，回寫欄位 |
| `last_updated` 超過 7 天 | 觸發 Places API Text Search，更新評分與照片 |
| 資料新鮮（7 天以內）| 直接使用本地快取，不呼叫 API |

---

## 技術棧

| 類別 | 工具 |
|------|------|
| IG 爬蟲 | Python · `instaloader`（`scripts/ig_scraper.py`） |
| LLM 提取 | Google Gemini API（`google-generativeai`） |
| Maps 驗證 | Google Maps Places API (New)（`requests` 直接呼叫） |
| 資料儲存 | JSON（本地檔案）|
| UI 渲染 | `core/flex_handler.py` · LINE Flex Message |

---

## 執行方式

```bash
# 確保 .env 已設定 GEMINI_API_KEY 與 GOOGLE_MAPS_API_KEY
python scripts/data_pipeline.py
```

**前置條件**：
1. `data/instagram_data.json` 已存在（由 `ig_scraper.py` 產生）
2. `.env` 已填入所需 API Key

**重新處理**（忽略 checkpoint）：
```bash
# 刪除 checkpoint 後重新執行
del data\ramen_data_checkpoint.json
python scripts/data_pipeline.py
```
