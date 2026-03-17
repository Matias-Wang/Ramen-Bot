import os
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# 1. 設定 Line 機器人的連線資訊，從環境變數中抓取你在第一小時準備好的資料
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 2. 建立 Webhook 接收端點，當 Line 伺服器傳訊息過來時，會訪問這個 /callback 網址
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']    # 取得 Line 傳過來的簽章 (Signature)
    body = request.get_data(as_text = True)    # 取得請求內容 (Body)
    # 處理訊息驗證與轉發
    try:
        handler.handle(body, signature)        # 這像是一個「防偽標籤」，確保這則訊息真的是從 Line 官方傳來的
    except InvalidSignatureError:
        print("簽章驗證失敗，請檢查 .env 中的 Secret 是否正確")
        abort(400)

    return 'OK'

# 3. 定義「收到文字訊息」時的動作，當有人傳送文字給機器人時，這個函式會被觸發
@handler.add(MessageEvent, message = TextMessage)
def handle_message(event):
    user_text = event.message.text    # # 取得使用者傳送的原始文字
    reply = TextSendMessage(text=f"連線測試成功！Matias，你剛才說的是：{user_text}")    # 這裡的 TextSendMessage 就是要傳回給手機的訊息物件
    # 使用 reply_token 將訊息回傳
    # 這行是通訊成功的最後一哩路
    line_bot_api.reply_message(
        event.reply_token,
        reply
    )

if __name__ == "__main__":
    print("--- 拉麵機器人伺服器啟動中 ---")
    print("目前的通訊埠 (Port): 5000")
    # 啟動 Flask 伺服器，監聽 5000 端口
    app.run(port=5000)