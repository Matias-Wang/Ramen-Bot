# 拉麵店資料管線 (Ramen Data Pipeline)

自動化抓取 Instagram 公開貼文、透過 LLM 萃取結構化資訊，並整合 Google Maps Geocoding 建立可查詢的拉麵店資料庫。

---

## 架構概覽

```
IG Scraping → LLM Extraction → Geocoding → Data Validation → Cache & Persistence
```

---

## 模組說明

### 1. 社交媒體資料爬取 (IG Scraping)

使用 [`instaloader`](https://instaloader.github.io/) 針對指定的公開 Instagram 帳號進行自動化資料抓取。

**抓取目標：**
- 貼文原始圖片 URL
- 發文內容（Caption）
- 貼文時間戳記

**自動化策略：**  
以背景排程任務（Background Job）定期執行，確保資料庫內容維持新鮮度。

---

### 2. AI 結構化內容提取 (LLM Extraction)

將抓取到的貼文文案送至 Gemini 等大型語言模型進行語意解析，從感性文案中精確提取結構化欄位。

**提取欄位：**
| 欄位 | 說明 | 範例 |
|------|------|------|
| `name` | 店名 | 麵屋武藏 |
| `address` | 地址 | 台北市大安區... |
| `tags` | 口味標籤 | 豚骨、二郎系、沾麵 |

**格式標準化：**  
所有 AI 提取結果統一轉換為 `ramen_data.json` 定義的 JSON schema，確保下游模組相容性。

---

### 3. 地理座標強化與驗證 (Geocoding)

呼叫 Google Maps Platform 的 [Geocoding API](https://developers.google.com/maps/documentation/geocoding)，對每一筆地址資料進行座標轉換。

**核心流程：**
1. 將 LLM 提取的文字地址作為查詢輸入
2. 取得精確經緯度座標（`lat` / `lng`）
3. 記錄唯一識別碼 `place_id`

**用途：**  
座標資料為「附近店家搜尋」功能的必要索引欄位，支援未來的地理空間查詢。

---

### 4. 資料一致性校驗 (Data Consistency Check)

專屬的資料庫健康檢查腳本，防止因 LLM 幻覺或同名店家導致的資料錯誤。

**比對邏輯：**  
- 計算 IG 原始地址與 Google Maps 回傳標準地址的字元相似度
- 若落差超過 **50%**，自動標記為異常（`status: "flagged"`）

**人工介入機制：**  
所有被標記的異常資料須經人工審查後方可寫入正式資料庫，有效規避同名店家造成的搜尋錯誤。

---

### 5. 快取回寫與持久化 (Cache & Persistence)

將驗證後的乾淨資料回寫至 `data/ramen_data.json`，並實作 TTL（Time-To-Live）快取機制控制 API 請求頻率。

**更新策略：**

| 資料類型 | 有效期 |
|----------|--------|
| 一般店家資料 | 7 天 |
| 近期新增 / 異動 | 24 小時 |

過期資料才重新觸發 API 請求，避免不必要的 quota 消耗。

**UI 整合：**  
`flex_handler.py` 可直接讀取 `ramen_data.json` 中的所有新增欄位，用於 LINE Bot Flex Message 的 UI 渲染。

---

## 資料欄位結構 (`ramen_data.json`)

```json
{
  "id": "unique_shortcode",
  "name": "店名",
  "address": "地址（LLM 提取）",
  "address_normalized": "地址（Google Maps 標準化）",
  "place_id": "ChIJ...",
  "lat": 25.0330,
  "lng": 121.5654,
  "tags": ["豚骨", "二郎系"],
  "caption": "原始貼文內容",
  "image_url": "https://...",
  "posted_at": "2025-01-01T12:00:00Z",
  "status": "verified",
  "cached_at": "2025-01-01T12:00:00Z",
  "ttl_hours": 168
}
```

---

## 技術棧

| 類別 | 工具 |
|------|------|
| IG 爬蟲 | Python · `instaloader` |
| LLM 提取 | Google Gemini API |
| 地理編碼 | Google Maps Geocoding API |
| 資料儲存 | JSON (file-based cache) |
| UI 渲染 | `flex_handler.py` · LINE Flex Message |