# 🍜 拉麵機器人 (Ramen-Bot) 開發紀錄

## 📌 專案概述
本專案旨在建立一個串接 Gemini AI 大腦的 LINE 機器人，專門提供使用者要求的拉麵相關資訊（店名、評價、口味分析等）。

## 📅 開發階段紀錄
### 第一階段：環境地基工程 (已完成)
- 環境管理工具：全面採用 uv 進行開發，取代傳統 pip，實現極速套件安裝與虛擬環境管理。
- 虛擬環境建立：已成功建立 .venv (Ramen-Bot) 並安裝核心依賴（Flask, line-bot-sdk, python-dotenv 等）。
- 金鑰保護機制：建立 .env 檔案，將 LINE Channel Access Token 與 Secret 進行環境變數隔離，符合安全性規範。

### 第二階段：通訊工程與 Webhook 配置 (已完成)
- Web 框架：使用 Flask 建立核心伺服器 app.py。
- 隧道架設：透過 ngrok 將本地端 5000 端口映射至公網，成功獲取 HTTPS 轉發網址。
- LINE 後台連線：成功通過 LINE Developers Webhook URL 驗證 (Success)。
- 完成「回應設定」調整：開啟 Webhook、關閉自動回應、切換為 Bot 模式。
- 連線測試：手機端發送測試訊息，機器人能正確執行「鸚鵡學舌」邏輯並回傳成功字串。

### 第三階段：核心邏輯分析 (已完成)
- Webhook 回呼處理：正確實作 request.headers['X-Line-Signature'] 驗證與 body 內容取得。
- 事件驅動架構：使用 @handler.add(MessageEvent, message=TextMessage) 精準捕捉使用者的文字需求。

## 🛠️ 目前技術棧 (Tech Stack)
- Language: Python 3.13+
- Package Manager: uv
- Web Framework: Flask
- API: LINE Messaging API
- Tunneling: ngrok

## 🚀 後續開發計畫 (Next Steps)
1. 注入 AI 大腦：申請 Google AI Studio API Key 並串接 Gemini 模型。
2. 拉麵專家設定：撰寫 System Instruction，限定 AI 僅能針對「拉麵」相關主題進行專業回覆。
3. 部署與優化：考慮未來將服務部署至雲端平台，擺脫 ngrok 的連線限制。