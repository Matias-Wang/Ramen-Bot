# UI 測試手冊（本地 LINE 模擬測試台）

`scripts/test_ui.py` 是一個 Streamlit 網頁，用來在本機模擬「使用者在 LINE 上跟拉麵機器人對話」。
它執行的是正式環境真正在跑的回覆流程（`src/app.py` 的 `_reply_to_line` / `_reply_location`），
只有一個差別：**要推播給 LINE 的訊息會被攔截，改顯示在網頁上**，不會真的送出。

> 自動化的回歸測試請用 `scripts/e2e_test.py`（pre-commit 與 GitHub Actions 都會跑）。
> 本測試台用於人工驗證：看回覆長相、Flex 排版、意圖判斷與效能。

---

## 1. 事前準備

| 項目 | 說明 |
|---|---|
| Python 環境 | Python 3.13，已建立 `.venv` |
| `.env` | 需含 `GEMINI_API_KEY`、`GEMINI_MODEL`、`GOOGLE_MAPS_API_KEY`、`GOOGLE_CLOUD_PROJECT_ID`、`FIRESTORE_DATABASE`、`LINE_CHANNEL_ACCESS_TOKEN`、`LINE_CHANNEL_SECRET`（向專案負責人索取） |
| GCP 登入 | 測試台會連正式 Firestore，需先登入：`gcloud auth application-default login` |

`.env` 裡的 `DATA_BACKEND` 設什麼都沒關係，測試台啟動時一律改用 Firestore。

## 2. 安裝與啟動

```bash
# 安裝開發依賴（含 streamlit；生產映像檔不會安裝）
pip install -r requirements-dev.txt        # 或：uv pip install -r requirements-dev.txt

# 啟動（瀏覽器會自動開啟 http://localhost:8501）
streamlit run scripts/test_ui.py
```

第一次載入需要 10～30 秒預熱連線（Firestore、Gemini、Google Maps），之後每則訊息約 2～8 秒。

## 3. 畫面說明

**左側側邊欄：模擬控制面板**
- **User ID**：模擬的 LINE 使用者，預設 `U_test_simulator`。換不同 ID 可模擬不同使用者
  （例如「避免重複推薦」只對同一個使用者生效）。
- **📍 模擬定位**：從下拉選單選熱門地點會自動帶入經緯度，也可以手動改數字；
  按「📍 發送位置訊息」等同使用者在 LINE 分享位置。

**畫面下方輸入框**：輸入文字訊息，按 Enter 送出。

**左欄「💬 對話」**：仿 LINE 的對話紀錄。Flex Message 會顯示成可展開的 JSON 樹，並附
「📋 一鍵複製 Flex JSON」按鈕。

**右欄「🔧 除錯與效能」**：可用下拉選單切換要看第幾輪。

| 區塊 | 用途 |
|---|---|
| 端到端耗時 | 送出到收到回覆的總秒數 |
| 摘要快取命中 | 推薦文是否全部命中店家的 `search_ai_summary` / `info_ai_summary` 快取（True / False；知識問答等不需推薦文時顯示 N/A） |
| LLM 呼叫 | 本輪呼叫 Gemini 的次數 |
| 大腦決策 | 判斷出的 intent、使用的 Skill、解析出的參數（地區、口味、店名…） |
| 摘要快取（search_ai_summary / info_ai_summary） | 每間店是否命中快取 |
| LLM / 配額 I/O 耗時明細 | 每次 Gemini 呼叫花多久 |
| 模擬的 LINE Webhook Payload | 送進管線的 LINE 事件原始格式 |
| 攔截到的 push_message（原始 JSON） | 原本要送給 LINE 的完整訊息內容 |
| 管線日誌 | 本輪的程式輸出，除錯時最有用。回覆送出後才執行的背景工作（例如更新圖片網址）不會出現在這裡，請看啟動測試台的終端機 |

## 4. 建議測試案例

| # | 情境 | 操作 | 預期結果 |
|---|---|---|---|
| 1 | 地區搜尋 | 輸入「中山區推薦的拉麵」 | 引導文 + Flex Carousel（最多 3 間）；intent = `SEARCH_BY_CRITERIA`、location = 中山區 |
| 2 | 捷運站搜尋 | 輸入「中山站附近的拉麵」 | Carousel；店家都在 2 公里內 |
| 3 | 要求定位 | 輸入「附近的拉麵」 | 文字回覆，附 Quick Reply［分享位置］ |
| 4 | 位置訊息 | 側邊欄選「台北車站」→ 發送位置訊息 | 「找到你附近 N 間拉麵店」+ Carousel |
| 5 | 位置無結果 | 選「南港展覽館」→ 發送位置訊息 | Carousel，或「5 公里內找不到、最近一間約 X 公里」 |
| 6 | 單店查詢 | 輸入「麵魚好吃嗎」 | 單一 Flex Bubble；intent = `GET_SPECIFIC_INFO` |
| 7 | 知識問答 | 輸入「札幌拉麵的特色是什麼」 | 純文字列點回答；摘要快取顯示 N/A |
| 8 | 錯誤回報 | 輸入「這家店的地址不對」 | 感謝回報的文字；intent = `REPORT_ERROR` |
| 9 | 換批推薦 | 同一 User ID 連續兩次輸入「中山區推薦的拉麵」 | 第二次換一批店；換 User ID 後不再排除 |
| 10 | 功能說明 | 輸入「怎麼使用」 | 固定功能清單，不進知識庫 |

## 5. 檢查 Flex 排版

1. 在 Flex 訊息下方按「📋 一鍵複製 Flex JSON」。
   （若瀏覽器擋下複製，展開「Flex JSON 原文」，用區塊右上角的複製圖示。）
2. 開啟 [LINE Flex Message Simulator](https://developers.line.biz/flex-simulator/)，
   選「View as JSON」貼上，即可看到實際手機上的樣子。

## 6. 測試資料會寫到哪裡

測試台啟動時固定開啟測試模式（`E2E_TEST_MODE=1`）：

| 資料 | 寫入位置 | 說明 |
|---|---|---|
| 對話日誌 | `test_conversation_logs` | 不進正式 `conversation_logs`，不影響對話分析 |
| 錯誤回報 | `test_feedback_reports` | 不進正式 `feedback_reports` |
| 每日配額 | 不計入 | Gemini、Google Maps、LINE 配額都不扣 |
| 店家推薦文快取、圖片網址 | 正式 `ramen_shops` | 屬於店家資料本身，由誰觸發都一樣有效 |
| LINE 推播 | 不送出 | 只顯示在網頁上。唯一例外：啟動時會以 `.env` 的 LINE token 查詢一次訊息額度（唯讀，用於連線預熱，不會發送任何訊息） |

`test_` 集合可隨時整批清除，不影響正式服務。

## 7. 常見問題

| 狀況 | 處理 |
|---|---|
| 畫面顯示「載入機器人管線失敗」 | 檢查 `.env` 是否齊全、是否已執行 `gcloud auth application-default login` |
| `ModuleNotFoundError: streamlit` | 尚未安裝開發依賴，執行 `pip install -r requirements-dev.txt` |
| 回覆「系統忙碌中」 | 多為 Gemini 暫時性錯誤，展開「管線日誌」看錯誤內容，再送一次 |
| 改了 `src/` 的程式碼沒有生效 | 到終端機按 Ctrl+C 停止，再重新 `streamlit run`（管線只在啟動時載入一次） |
