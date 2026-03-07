#　拉麵機器人開發與數據整合進度規劃書
本規劃書分為短期衝刺、數據擴充、以及長期架構優化三個階段。

## 一、 短期開發衝刺 (每小時目標)
### H1：視覺介面轉型 (Flex Message)
- 核心任務：建立 LINE Flex Message Carousel 模板。
- 預期產出：將 results 中的店家資訊轉換為橫向滑動的泡泡卡片，包含標題、副標題與描述。

## H2：推薦靈魂注入 (Prompt Tuning)
- 核心任務：優化 prompts.py 模板，定義 AI 為「拉麵老饕」。
- 預期產出：讓 AI 根據 style 與 description 生成更具張力與食慾的 50-80 字短評。

## H3：地圖與互動功能整合 (UX Enhancement)
- 核心任務：在 Flex 卡片下方加入 URI Action 按鈕。
- 預期產出：點擊「開啟導航」按鈕可直接帶入 Google Maps 搜尋連結，提升實用性。

## 4：防禦性邏輯與引導對話 (Exception Flow)
- 核心任務：實作「查無結果」的引導對話。
- 預期產出：當資料庫無匹配時，AI 會主動詢問是否改看其他地區或口味，避免對話中斷。

# 二、 Instagram 數據整合階段 (擴充目標)
## H5：IG 數據採集腳本 (Data Fetching)
- 核心任務：開發 Instagram 爬蟲或使用 Graph API。
- 預期產出：自動抓取指定 IG 帳號（如使用者自己的食記帳號）的貼文圖片、內文與日期。

## H6：非結構化數據清理 (AI Processing)
- 核心任務：利用 Gemini 讀取 IG 內文。
- 預期產出：將口語化的貼文自動轉換為資料庫格式（地點、口味、個人評分、圖片連結）。

## H7：動態資料庫更新 (DB Enrichment)
- 核心任務：將清理後的 IG 數據匯入 ramen_data.json 或 Vector DB。
- 預期產出：機器人能即時檢索到最新的 IG 食記內容，實現「IG 同步更新」。

## H8：知識庫檢索增強 (RAG Implementation)
- 核心任務：讓模型在回答前優先檢索 IG 數據。
- 預期產出：AI 推薦時能說出：「這間店我最近在 IG 上有分享過，湯頭表現...」等個人化語句。

# 三、 長期架構與功能延伸
1. 多模態圖文識別 (Vision Capability)
描述：使用者傳送拉麵照片，AI 自動識別口味並回饋該店相關資訊或類似推薦。
2. 使用者口味畫像系統 (User P- rofiling)
描述：紀錄每位使用者的查詢習慣，未來主動推播符合其偏好的新店開張訊息。
3. 生產環境部署與監控 (Cloud Deployment)
描述：將程式部署至雲端平台（如 Heroku 或 Render），並建立錯誤日誌自動告警系統。
4. 外部 API 連動 (Ecosystem Expansion)
描述：串接天氣 API (雨天推薦) 或 預約系統，讓機器人從「推薦」轉向「服務」。

# 四、 系統最終架構圖
[使用者訊息] -> [Gemini 意圖分析]
|
v
[本地 JSON + IG 擴充資料庫] <- [IG 數據同步腳本] <- [Instagram 貼文]
|
v
[Python 硬邏輯篩選] -> [Gemini 語氣生成] -> [LINE Flex Message]
