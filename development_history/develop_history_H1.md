# Ramen Bot 開發紀錄 - H1 階段：視覺介面轉型
## 1. 階段目標
- 介面升級：將原本的純文字列表回覆轉型為 LINE Flex Message (Carousel) 輪播介面。
- 資訊收斂：為了優化手機端閱讀體驗，將單次查詢結果嚴格限制為 最多 3 筆。
- 分工解耦：建立清晰的檔案架構，實現邏輯、資料處理與 UI 模板的分離。

##　2. 系統架構
本階段將程式碼拆解為三大核心檔案，確保後續擴充性：

1. app.py (Controller)：
    - 負責 LINE Webhook 請求處理與流程控管。
    - 整合 Gemini 進行「意圖解析」與「推薦文案生成」。
    - 分離意圖模型（強制 JSON）與推薦模型（純文字），解決文案格式混亂問題。

2. processor.py (Data Layer)：
    - 負責本地 ramen_data.json 的篩選邏輯。
    - 實作地區關鍵字模糊比對（如：自動處理「南港」與「南港區」的差異）。

3. flex_handler.py (View Layer)：
    - 管理 Flex Message 的 JSON 模板。
    - 負責將店家資料動態填入模板，並組裝成 Carousel 格式。

4. prompts.py (prompt database)：
    - 管理LLM Prompt。

##　3. 關鍵功能實作
- 資料填充映射：將資料庫欄位精確對應至 FM 卡片物件：

    1. {SHOP_NAME}：店家名稱。
    1. {LOCATION} · {STYLE}：地區與口味標籤。
    1. {ADDRESS}：詳細地址。
    1. {AI_RECOMMENDATION}：由 Gemini 生成的專業點評    1. 

- 互動行為：Footer 區塊設置「查看地圖」按鈕，點擊後透過 uri 動作導向 Google Maps 搜尋該店家。

- 安全機制：修正原本使用 str.replace 造成的 JSON 破壞問題，改用字典操作或 JSON 轉義以確保 UI 渲染穩定。

## 4. 當前進度 (2026-03-17)
✅ 地端測試：意圖解析、資料篩選、AI 推薦文案邏輯通過。<br>
✅ 樣式驗證：單一店家 Flex Message 於 LINE App 成功輸出。<br>
✅ 多筆驗證：正在測試 2-3 筆資料的 Carousel 輪播顯示效果。<br>
✅ 穩定性觀察：透過 error.log 追蹤 Webhook 連線與 API 調用穩定度。