# 🍜 拉麵機器人 (Ramen-Bot) 開發日誌
##　📌 專案核心架構：三層式 Agentic Workflow
為了確保回覆的「精準度」與「人性化」，本專案捨棄了單純的 AI 對話，採用以下三層架構：

- 第一層 (AI 意圖辨識)：分析使用者輸入，提取地區、口味等參數，並輸出 UI Tag。
- 第二層 (Python 邏輯篩選)：根據第一層參數，從 ramen_data.json 進行硬性過濾。
- 第三層 (AI 內容摘要)：針對篩選結果進行自然語言修飾。
- 最終端 (Python UI 組裝)：將文字填入對應的 LINE Flex Message 模板。

## ✅ 已完成里程碑
1. 通訊與環境架設
    - LINE 連線：成功串接 LINE Messaging API，並透過 ngrok 完成 Webhook 驗證。
    - 回應模式設定：於 LINE Official Account Manager 關閉自動回應，確保 app.py 擁有完全主導權。
    - 環境管理：使用 uv 管理虛擬環境與套件。

2. 資料層與第二層邏輯實作
    - Master JSON 建立：定義了 ramen_data.json 格式，包含 location、style、description 等關鍵欄位。
    - 篩選器開發：實作 processor.py，成功通過 uv run processor.py 測試，能精準過濾 JSON 資料。

3. 第一層 AI 大腦訓練 (進行中)
    - Google AI Studio：成功進入最新的 Build 模式 建立 Agent 原型。
    - System Instruction：已寫入意圖分類邏輯，教導 Gemini 將模糊語句轉換為結構化 JSON（含 ui_tag 指令）。

## 🛠️ 開發鐵則 (Matias's Principles)
    - 職責分離：AI 僅處理「文字內容」與「語義分析」，排版邏輯（Flex Message）嚴格由 Python 程式碼控制。
    - 額度控管與降級：當 API 額度用完時，系統必須捕捉錯誤並改傳送預設的「拉麵罐頭訊息」。
    - 雲端化目標：未來規劃部署至 Google Cloud 或 Azure AI Foundry (Prompt Flow)。
    - 內容專注：本機器人僅限於提供「拉麵資訊」，不涉及穿搭或其他無關主題。

📂 目前專案結構
    - app.py：主程式（負責接收 LINE 訊息與調度流程）。
    - processor.py：邏輯層（負責 JSON 篩選）。
    - ramen_data.json：資料庫（存放拉麵店詳細資訊）。
    - .env：環境變數（存放 LINE 與 Gemini API 密鑰）。

🚀 下一步計畫
1. 從 Google AI Studio 的 Build 畫面中點擊 "Code" 獲取 Python 串接程式碼。
1. 將 Gemini API 的呼叫邏輯整合進 app.py。
1. 測試「第一層 + 第二層」的連動：LINE 傳送需求 -> AI 分類 -> Python 篩選 -> 回傳原始 JSON 結果。