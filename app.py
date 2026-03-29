# === 在檔案頂部定義開關 ===
# LINE_TAG = 1: 正式模式 (LINE + ngrok)
# LINE_TAG = 0: 地端測試模式 (終端機輸出 + temp.json)
LINE_TAG = 0

# === imports ===
import os
import json
import google.generativeai as genai
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FlexSendMessage
from dotenv import load_dotenv

# === 外部模組匯入 ===
from agent_router import AgentRouter
from flex_handler import assemble_carousel

# === 使用者自定義變數 (第 21-27 行) ===
RED = '\033[91m'
YELLOW = '\033[93m'
GREEN = '\033[92m'
BLUE = '\033[94m'
CYAN = '\033[96m'
MAGAENTA = '\033[95m'
RESET = '\033[0m'

# 載入環境變數 (.env)
load_dotenv()

# 初始化 Flask App
app = Flask(__name__)

# 初始化 LINE Bot API 與 Webhook Handler
line_bot_api = LineBotApi(os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 設定 Gemini API Key
genai.configure(api_key=os.getenv('GEMINI_API_KEY'))

# 初始化 AgentRouter：負責意圖解析與技能分發
router = AgentRouter(os.getenv('GEMINI_MODEL'))

@app.route("/callback", methods=['POST'])
def callback():
    """
    LINE Webhook 入口點。
    """
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    處理 LINE 收到文字訊息的事件。
    """
    user_text = event.message.text
    
    try:
        result = router.dispatch(user_text)
        
        intent = result.get('intent')
        data = result.get('data')
        recommendations = result.get('recommendations')
        ui_tag = result.get('ui_tag')
        
        if not data and intent != 'KNOWLEDGE_QUERY':
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text='找不到符合條件的拉麵店，請試著提供更多或不同的條件。')
            )
            return

        if ui_tag == 'CAROUSEL' and data:
            carousel_contents = assemble_carousel(data, recommendations)
            flex = FlexSendMessage(alt_text='拉麵推薦', contents=carousel_contents)
            line_bot_api.reply_message(event.reply_token, flex)
        elif intent == 'KNOWLEDGE_QUERY':
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=result.get('message', '百科功能開發中！'))
            )
        else:
            items = []
            for i, s in enumerate(data[:5]):
                name = s.get('name') or '不明店名'
                loc = s.get('location') or '不明地區'
                style = s.get('style') or '不明口味'
                items.append(f"{i+1}. {name} ({loc} / {style})")

            reply_text = f"找到 {len(data)} 間店：\n" + "\n".join(items)
            if recommendations:
                reply_text += "\n\n推薦詞：\n" + "\n".join(f"{i+1}. {r}" for i, r in enumerate(recommendations))
            
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text))

    except Exception as e:
        print(f"Error in handle_message: {e}")
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text='系統忙碌中，請稍後再試。'))

if __name__ == "__main__":
    if LINE_TAG == 1:
        app.run(port=5000)
    else:
        print(f"{CYAN}--- 地端測試模式啟動 ---{RESET}")
        while True:
            test_input = input(f"\n{YELLOW}>> 請輸入提問 (輸入 'exit' 離開): {RESET}")
            if test_input.lower() == 'exit':
                break
            
            # STEP 1, 2, 3 在 router.dispatch 內部執行並會有各自的錯誤輸出
            print(f"{GREEN}STEP 1: 正在呼叫 Gemini 解析使用者意圖...{RESET}")
            res = router.dispatch(test_input)
            
            print(f"{GREEN}STEP 2: 執行對應 Skill 並獲取資料 (Intent: {res['intent']})...{RESET}")
            print(f"  - 找到店家數量: {len(res.get('data', []))}")
            
            if res.get('recommendations'):
                print(f"{GREEN}STEP 3: 正在生成 AI 推薦文案...{RESET}")
            
            # STEP 4: UI 組裝與檔案輸出 (此處補上 Error 輸出)
            print(f"{GREEN}STEP 4: 正在進行 UI 組裝與 temp.json 輸出...{RESET}")
            try:
                if res.get('ui_tag') == 'CAROUSEL' and res.get('data'):
                    carousel_contents = assemble_carousel(res['data'], res.get('recommendations'))
                    with open('temp.json', 'w', encoding='utf-8') as f:
                        json.dump(carousel_contents, f, ensure_ascii=False, indent=2)
                    print(f"{CYAN}  => 成功！Flex Message JSON 已寫入 temp.json{RESET}")
                else:
                    print(f"{BLUE}  => 此意圖建議使用文字回覆，未生成 Carousel JSON。{RESET}")
            except Exception as e:
                print(f"{RED} STEP 4 ERROR：{e}{RESET}")
            
            print(f"{MAGAENTA}{'='*50}{RESET}")
